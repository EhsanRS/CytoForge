"""Reproducible cytometry analyses with explicit event identity and sampling semantics."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from importlib.metadata import version
from pathlib import Path

import numpy as np

from .formulas import parse
from .models import AnalysisData, AnalysisRequest, AnalysisResult, Workspace
from .science import Engine, save_array, save_events
from .store import Store, now


def atomic_json(path: Path, value: dict):
    temp = path.with_suffix(".writing")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, allow_nan=False, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def input_hash(workspace: Workspace, request: AnalysisRequest) -> str:
    """Ignore presentation/history changes; track every scientific input dependency."""
    scientific = [_sample_signature(workspace, request, source) for source in request.inputs]
    return hashlib.sha256(json.dumps(scientific, sort_keys=True).encode()).hexdigest()


def transform_signature(spec):
    # Keep fingerprints of previously saved analyses stable as models gain defaults.
    if spec.kind in {"linear", "log", "logicle", "hyperlog", "asinh"}:
        include = {"kind", "cofactor", "t", "w", "m", "a"}
        if spec.bound_min is not None or spec.bound_max is not None:
            include.update({"bound_min", "bound_max"})
        return spec.model_dump(include=include)
    return spec.model_dump()


def gate_signature(gate):
    value = gate.model_dump(exclude={"name", "color", "provenance"})
    for key in ["x_transform", "y_transform"]:
        value[key] = transform_signature(getattr(gate, key))
    for key, default in {
        "dimensions": [],
        "covariance": [],
        "coordinates": [],
        "distance_square": 1,
        "complement": False,
        "operand_complements": [],
        "quality_id": None,
        "quality_excluded_bins": [],
        "quality_exclusions": [],
        "quality_keep": True,
    }.items():
        if value[key] == default:
            value.pop(key)
    for dim in value.get("dimensions", []):
        from .models import Transform

        dim["transform"] = transform_signature(Transform.model_validate(dim["transform"]))
    return value


def _sample_signature(workspace, request, source):
    compensated = getattr(request, "compensated", True)
    samples = {s.id: s for s in workspace.samples}
    gates = {g.id: g for g in workspace.gates}
    results = {
        a.id: a
        for a in [
            *workspace.analyses,
            *workspace.cell_cycle_results,
            *workspace.proliferation_results,
            *workspace.kinetics_results,
        ]
    }
    matrices = {c.id: c for c in workspace.compensations}
    sample = samples[source.sample_id]
    channels = {c.name: c for c in sample.channels}
    derived = {d.name: d for d in sample.derived_parameters}
    computed = {p.name: p for p in sample.computed_parameters}
    dependencies, gate_defs, gate_matrices = {}, {}, {}

    def column(name: str, include_transform=False):
        key = name + (":transformed" if include_transform else ":raw")
        if key in dependencies:
            return
        c = channels[name]
        dependencies[key] = {"name": name}
        if include_transform:
            dependencies[key]["transform"] = transform_signature(c.transform)
        if name in sample.aliases:
            dependencies[key]["alias_source"] = sample.aliases[name]
            column(sample.aliases[name])
        if name in derived:
            dependencies[key]["expression"] = derived[name].expression
            for dependency in parse(derived[name].expression)[1]:
                column(dependency)
        if name in computed:
            param = computed[name]
            result = results[param.analysis_id]
            data = next(d for d in result.data if d.sample_id == sample.id)
            dependencies[key]["analysis"] = [param.analysis_id, param.index, data.sha256]

    def gate(gate_id):
        if not gate_id or gate_id in gate_defs:
            return
        g = gates[gate_id]
        gate_defs[gate_id] = gate_signature(g)
        if g.kind == "quality":
            result = next(q for q in workspace.quality_results if q.id == g.quality_id)
            dependencies[f"quality:{result.id}"] = {"event_flags_sha256": result.data.sha256}
        for dim in g.dimensions:
            for name in dim.ratio_channels or (dim.channel,):
                column(name)
            if compensated and dim.compensation_ref == "FCS":
                gate_matrices["FCS"] = sample.metadata.get("spillover") or sample.metadata.get(
                    "spill"
                )
            elif compensated and dim.compensation_ref not in {"sample", "uncompensated"}:
                gate_matrices[dim.compensation_ref] = matrices[dim.compensation_ref].model_dump()
        if g.x and not g.dimensions:
            column(g.x)
        if g.y and not g.dimensions:
            column(g.y)
        for dependency in ([g.parent_id] if g.parent_id else []) + g.operands:
            gate(dependency)

    for name in request.channels:
        column(name, request.use_transforms)
    gate(source.gate_id)
    signature = {
        "sample_id": sample.id,
        "sha256": sample.sha256,
        "events": sample.event_count,
        "acquisition_channels": [c.name for c in sample.acquisition_channels],
        "compensation": matrices[sample.compensation_id].model_dump()
        if sample.compensation_id and compensated
        else None,
        "channels": dependencies,
        "gates": gate_defs,
    }
    if gate_matrices:
        signature["gate_compensations"] = gate_matrices
    if not compensated:
        signature["coordinate_basis"] = "acquired"
    return signature


def is_stale(workspace: Workspace, result: AnalysisResult) -> bool:
    try:
        return input_hash(workspace, result.request) != result.input_hash
    except (KeyError, StopIteration, ValueError):
        return True


def validate_request(workspace: Workspace, request: AnalysisRequest):
    total_values = 0
    for source in request.inputs:
        sample = next((s for s in workspace.samples if s.id == source.sample_id), None)
        if sample is None:
            raise ValueError("Analysis references a missing sample")
        missing = set(request.channels) - {c.name for c in sample.channels}
        if missing:
            raise ValueError(f"{sample.name} is missing channels: {', '.join(sorted(missing))}")
        if source.gate_id and not any(
            g.id == source.gate_id and g.sample_id == sample.id for g in workspace.gates
        ):
            raise ValueError("Analysis population must belong to its input sample")
        total_values += sample.event_count * len(request.channels)
    if total_values > 40_000_000:
        raise ValueError(
            "This analysis exceeds the 40-million input-value limit. "
            "Select fewer samples or channels until streaming analysis is available."
        )


def validate_result_data(store, workspace_id, result, data, values):
    finite = np.all(np.isfinite(values), axis=1)
    undefined = np.all(np.isnan(values), axis=1)
    if not np.all(finite | undefined) or int(finite.sum()) != data.mapped_count:
        raise ValueError("Analysis mapping counts or undefined event rows are inconsistent")
    if result.request.algorithm in {"tsne", "phenograph"} and (
        data.mapped_count != data.fitted_count
    ):
        raise ValueError("This analysis defines output for fitted event identities only")
    if result.request.algorithm == "phenograph":
        sizes = result.diagnostics.get("community_sizes")
        if (
            len(result.columns) != 1
            or not data.fitted_ids_sha256
            or not isinstance(sizes, dict)
            or set(sizes) != {str(i) for i in range(1, len(sizes) + 1)}
            or any(
                type(size) is not int or size < result.request.min_cluster_size
                for size in sizes.values()
            )
        ):
            raise ValueError("PhenoGraph communities and fitted identities are inconsistent")
        labels = values[finite, 0]
        if not np.isin(labels, [0, *range(1, len(sizes) + 1)]).all():
            raise ValueError(
                "PhenoGraph labels must name a reviewed community or unassigned label 0"
            )
        counts = result.diagnostics.get("cluster_counts")
        if (
            not isinstance(counts, dict)
            or set(counts) != {item.sample_id for item in result.data}
            or any(not isinstance(row, dict) for row in counts.values())
            or any(
                key not in {"0", *sizes} or type(count) is not int or count <= 0
                for row in counts.values()
                for key, count in row.items()
            )
            or any(
                sum(row.get(key, 0) for row in counts.values()) != size
                for key, size in sizes.items()
            )
            or result.diagnostics.get("unassigned_fitted_count")
            != sum(row.get("0", 0) for row in counts.values())
            or result.diagnostics.get("graph_nodes")
            != sum(item.fitted_count for item in result.data)
            or sum(sum(row.values()) for row in counts.values())
            != result.diagnostics.get("graph_nodes")
        ):
            raise ValueError("PhenoGraph graph and global community counts are inconsistent")
        actual = {
            str(int(label)): int(count)
            for label, count in zip(*np.unique(labels, return_counts=True), strict=True)
        }
        if counts.get(data.sample_id) != actual:
            raise ValueError("PhenoGraph per-sample community counts do not match event labels")
    if data.fitted_ids_sha256:
        path = store.fitted_ids_path(workspace_id, result.id, data.sample_id)
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != data.fitted_ids_sha256:
            raise ValueError("Fitted event identities failed their integrity check")
        identities = np.load(path, mmap_mode="r", allow_pickle=False)
        if identities.shape != (data.fitted_count,) or identities.dtype.kind not in "iu":
            raise ValueError("Invalid fitted event identities")
        if len(identities) and (
            identities[0] < 0
            or identities[-1] >= data.event_count
            or np.any(identities[1:] <= identities[:-1])
        ):
            raise ValueError("Fitted event identities must be unique, ordered and in this sample")
        if not np.all(finite[identities]):
            raise ValueError("Fitted event identities do not match the mapped analysis output")


def sample_indices(counts: list[int], maximum: int, mode: str, seed: int) -> list[np.ndarray]:
    """Return unique, sorted indices; redistribute unused balanced quotas."""
    total = sum(counts)
    target = min(maximum, total)
    rng = np.random.default_rng(seed)
    if mode == "proportional":
        selected = np.sort(rng.choice(total, target, replace=False))
        offsets = np.cumsum([0, *counts])
        return [
            selected[(selected >= offsets[i]) & (selected < offsets[i + 1])] - offsets[i]
            for i in range(len(counts))
        ]
    quotas = np.zeros(len(counts), dtype=np.int64)
    capacity = np.asarray(counts, dtype=np.int64)
    remaining = target
    while remaining:
        active = np.flatnonzero(quotas < capacity)
        base, remainder = divmod(remaining, len(active))
        proposed = np.full(len(active), base, dtype=np.int64)
        proposed[:remainder] += 1
        add = np.minimum(proposed, capacity[active] - quotas[active])
        quotas[active] += add
        remaining -= int(add.sum())
    return [
        np.sort(rng.choice(n, int(q), replace=False)) for n, q in zip(counts, quotas, strict=True)
    ]


class ConsensusMetacluster:
    """Resampled co-assignment consensus, followed by average-linkage clustering."""

    def __init__(self, n_clusters, seed=42, resamples=100, proportion=0.9):
        self.n_clusters, self.seed = n_clusters, seed
        self.resamples, self.proportion = resamples, proportion

    def fit_predict(self, codes):
        from sklearn.cluster import AgglomerativeClustering

        rng = np.random.default_rng(self.seed)
        n = len(codes)
        selected_count = min(n, max(self.n_clusters, int(np.ceil(n * self.proportion))))
        together, occurrences = np.zeros((n, n)), np.zeros((n, n))
        for _ in range(self.resamples):
            selected = np.sort(rng.choice(n, selected_count, replace=False))
            labels = AgglomerativeClustering(
                n_clusters=self.n_clusters, linkage="average"
            ).fit_predict(codes[selected])
            position = np.ix_(selected, selected)
            occurrences[position] += 1
            together[position] += labels[:, None] == labels[None, :]
        consensus = np.divide(
            together, occurrences, out=np.zeros_like(together), where=occurrences > 0
        )
        np.fill_diagonal(consensus, 1)
        self.consensus_ = consensus
        self.labels_ = AgglomerativeClustering(
            n_clusters=self.n_clusters, metric="precomputed", linkage="average"
        ).fit_predict(1 - consensus)
        return self.labels_


def watch_parent(parent_pid: int, stop: threading.Event):
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            os._exit(1)
        try:
            while not stop.is_set():
                if kernel.WaitForSingleObject(ctypes.c_void_p(handle), 1000) != 258:
                    os._exit(1)
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    else:
        while not stop.wait(1):
            if os.getppid() != parent_pid:
                os._exit(1)


def run_analysis(job_dir: str):
    """Spawn target. All writes are confined to this job and its immutable output directory."""
    directory = Path(job_dir)
    started = time.monotonic()

    def progress(stage: str, fraction: float):
        atomic_json(directory / "progress.json", {"stage": stage, "progress": fraction})

    store = None
    stop = threading.Event()
    try:
        payload = json.loads((directory / "input.json").read_text())
        threading.Thread(
            target=watch_parent, args=(payload["parent_pid"], stop), daemon=True
        ).start()
        workspace = Workspace.model_validate(payload["workspace"])
        request = AnalysisRequest.model_validate(payload["request"])
        validate_request(workspace, request)
        store = Store(Path(payload["data_dir"]))
        engine = Engine(store, cache_bytes=32 * 1024**2)
        progress(
            "Reading compensated populations"
            if request.compensated
            else "Reading acquired populations",
            0.04,
        )
        inputs, counts, warnings = [], [], []
        for i, source in enumerate(request.inputs):
            sample = engine.sample(workspace, source.sample_id)
            mask = engine.mask(workspace, sample, source.gate_id, compensated=request.compensated)
            indices = np.flatnonzero(mask)
            values = np.column_stack(
                [
                    engine.column(
                        workspace,
                        sample,
                        name,
                        next(c.transform for c in sample.channels if c.name == name)
                        if request.use_transforms
                        else None,
                        compensated=request.compensated,
                    )[indices]
                    for name in request.channels
                ]
            )
            finite = np.all(np.isfinite(values), axis=1)
            excluded = int((~finite).sum())
            if excluded:
                warnings.append(f"{sample.name}: excluded {excluded:,} nonfinite events")
            if not finite.any():
                warnings.append(f"{sample.name}: no eligible finite events in this population")
            values, indices = values[finite], indices[finite]
            inputs.append((sample, indices, values, int(mask.sum())))
            counts.append(len(indices))
            progress("Preparing features", 0.05 + 0.2 * (i + 1) / len(request.inputs))
        selected = sample_indices(counts, request.max_events, request.sampling, request.seed)
        train = np.concatenate(
            [v[index] for (_, _, v, _), index in zip(inputs, selected, strict=True)]
        )
        if len(train) < 4:
            raise ValueError("Select a population containing at least four eligible finite events")
        spread = np.std(train, axis=0)
        usable = np.isfinite(spread) & (spread > np.maximum(np.abs(train.mean(0)), 1) * 1e-12)
        if usable.sum() < 2:
            raise ValueError("At least two selected features must vary in the fitted population")
        removed = [c for c, keep in zip(request.channels, usable, strict=True) if not keep]
        if removed:
            warnings.append("Excluded constant features: " + ", ".join(removed))
        train = train[:, usable]
        mean, scale = np.zeros(train.shape[1]), np.ones(train.shape[1])
        if request.standardize:
            mean, scale = train.mean(0), train.std(0)
        train = np.ascontiguousarray((train - mean) / scale)
        progress("Loading analysis engine", 0.28)
        from sklearn.decomposition import PCA
        from threadpoolctl import threadpool_limits

        if request.algorithm == "umap":
            import numba
            from umap import UMAP

            numba.set_num_threads(1)
        if request.algorithm == "tsne":
            from sklearn.manifold import TSNE
        if request.algorithm == "flowsom":
            import numba
            from flowsom.models import FlowSOMEstimator

            numba.set_num_threads(1)
        diagnostics = {
            "feature_names": [c for c, keep in zip(request.channels, usable, strict=True) if keep],
            "feature_mean": mean.tolist(),
            "feature_scale": scale.tolist(),
            "excluded_constant_features": removed,
            "fitted_events": len(train),
            "sampling": request.sampling,
            "eligible_events": sum(counts),
        }
        with threadpool_limits(limits=1):
            progress(f"Fitting {request.algorithm.upper()}", 0.32)
            if request.algorithm == "pca":
                model = PCA(n_components=2, svd_solver="full").fit(train)
                training_output = model.transform(train)
                suffixes = ["PC1", "PC2"]
                diagnostics.update(
                    explained_variance_ratio=model.explained_variance_ratio_.tolist(),
                    loadings=model.components_.T.tolist(),
                    pca_mean=model.mean_.tolist(),
                    mapping="Linear projection of every eligible event",
                )
            elif request.algorithm == "umap":
                if request.n_neighbors >= len(train):
                    raise ValueError("UMAP neighbors must be less than the number of fitted events")
                model = UMAP(
                    n_components=2,
                    n_neighbors=request.n_neighbors,
                    min_dist=request.min_dist,
                    random_state=request.seed,
                    transform_seed=request.seed,
                    n_jobs=1,
                    init="random",
                ).fit(train)
                training_output = model.embedding_
                suffixes = ["UMAP1", "UMAP2"]
                diagnostics["mapping"] = "Fitted events plus UMAP transform for remaining events"
            elif request.algorithm == "tsne":
                if request.perplexity >= len(train):
                    raise ValueError("t-SNE perplexity must be less than the fitted event count")
                tsne_train = train
                if train.shape[1] > 50:
                    tsne_train = PCA(n_components=min(50, len(train) - 1)).fit_transform(train)
                model = TSNE(
                    n_components=2,
                    perplexity=request.perplexity,
                    max_iter=request.iterations,
                    random_state=request.seed,
                    init="pca",
                    learning_rate="auto",
                    n_jobs=1,
                )
                training_output = model.fit_transform(tsne_train)
                suffixes = ["tSNE1", "tSNE2"]
                diagnostics.update(
                    kl_divergence=float(model.kl_divergence_),
                    iterations=int(model.n_iter_),
                    mapping="Fitted events only; remaining events are undefined (NaN)",
                )
                if len(train) < sum(counts):
                    warnings.append(
                        "t-SNE has no out-of-sample projection. Only fitted event IDs receive "
                        "coordinates; all other events remain undefined."
                    )
            elif request.algorithm == "phenograph":
                from .graph_clustering import fit

                labels, graph_diagnostics = fit(
                    train,
                    neighbors=request.n_neighbors,
                    min_cluster_size=request.min_cluster_size,
                    resolution=request.graph_resolution,
                    seed=request.seed,
                    restarts=request.louvain_restarts,
                )
                training_output = labels[:, None]
                suffixes = ["Community"]
                diagnostics.update(graph_diagnostics)
                if graph_diagnostics["unassigned_fitted_count"]:
                    warnings.append(
                        f"{graph_diagnostics['unassigned_fitted_count']:,} fitted events belong "
                        "to discarded small communities; their label is 0 (unassigned)"
                    )
                if len(train) < sum(counts):
                    warnings.append(
                        "PhenoGraph labels apply only to the fitted subset. "
                        "Other eligible events remain undefined "
                        "and are not assigned to communities."
                    )
            else:
                if len(train) < request.grid_size**2:
                    raise ValueError("FlowSOM needs at least as many fitted events as SOM nodes")
                model = FlowSOMEstimator(
                    metacluster_model=ConsensusMetacluster,
                    xdim=request.grid_size,
                    ydim=request.grid_size,
                    n_clusters=request.n_clusters,
                    rlen=request.epochs,
                    seed=request.seed,
                )
                model.metacluster_model.seed = request.seed
                model.fit(train)
                meta = model.predict(train)
                training_output = np.column_stack([model.cluster_labels_ + 1, meta + 1])
                suffixes = ["SOM node", "Metacluster"]
                diagnostics.update(
                    codes=model.codes.tolist(),
                    node_metaclusters=(model.metacluster_model.labels_ + 1).tolist(),
                    grid_size=request.grid_size,
                    consensus_resamples=100,
                    mapping="Nearest SOM node and consensus metacluster for every eligible event",
                )
            if not np.all(np.isfinite(training_output)):
                raise ValueError("The analysis produced nonfinite fitted coordinates")
            progress("Mapping results to original event IDs", 0.74)
            columns = [f"{request.name} · {s} [{payload['id'][:6]}]" for s in suffixes]
            if any(len(c) > 160 for c in columns):
                raise ValueError("Analysis name is too long for output parameter names")
            data, training_offset, cluster_counts = [], 0, {}
            for i, ((sample, event_ids, values, population_count), chosen) in enumerate(
                zip(inputs, selected, strict=True)
            ):
                output = np.full((sample.event_count, len(columns)), np.nan, dtype=np.float64)
                output[event_ids[chosen]] = training_output[
                    training_offset : training_offset + len(chosen)
                ]
                training_offset += len(chosen)
                if request.algorithm not in {"tsne", "phenograph"}:
                    remaining = np.ones(len(values), dtype=bool)
                    remaining[chosen] = False
                    rows = np.flatnonzero(remaining)
                    for chunk in np.array_split(rows, max(1, (len(rows) + 24999) // 25000)):
                        if not len(chunk):
                            continue
                        batch = np.ascontiguousarray((values[chunk][:, usable] - mean) / scale)
                        if request.algorithm == "flowsom":
                            meta = model.predict(batch)
                            mapped = np.column_stack([model.cluster_labels_ + 1, meta + 1])
                        else:
                            mapped = model.transform(batch)
                        if not np.all(np.isfinite(mapped)):
                            raise ValueError("The analysis produced nonfinite mapped coordinates")
                        output[event_ids[chunk]] = mapped
                mapped_count = int(np.all(np.isfinite(output), axis=1).sum())
                digest = save_events(
                    store.analysis_path(workspace.id, payload["id"], sample.id), output
                )
                fit_digest = save_array(
                    store.fitted_ids_path(workspace.id, payload["id"], sample.id),
                    event_ids[chosen].astype(np.int64),
                )
                data.append(
                    AnalysisData(
                        sample_id=sample.id,
                        event_count=sample.event_count,
                        population_count=population_count,
                        finite_count=len(event_ids),
                        fitted_count=len(chosen),
                        mapped_count=mapped_count,
                        sha256=digest,
                        fitted_ids_sha256=fit_digest,
                    )
                )
                if request.algorithm == "flowsom":
                    labels, totals = np.unique(output[event_ids, 1].astype(int), return_counts=True)
                    cluster_counts[sample.id] = dict(
                        zip(labels.astype(str), totals.tolist(), strict=True)
                    )
                elif request.algorithm == "phenograph":
                    labels, totals = np.unique(output[event_ids[chosen], 0], return_counts=True)
                    cluster_counts[sample.id] = dict(
                        zip(labels.astype(int).astype(str), totals.tolist(), strict=True)
                    )
                progress("Saving event-aligned results", 0.76 + 0.2 * (i + 1) / len(inputs))
            if cluster_counts:
                diagnostics["cluster_counts"] = cluster_counts
        libraries = ["numpy", "scikit-learn", "scipy"]
        if request.algorithm in {"umap", "flowsom"}:
            libraries += ["numba", "umap-learn" if request.algorithm == "umap" else "flowsom"]
        if request.algorithm == "phenograph":
            libraries += ["numba", "igraph"]
        result = AnalysisResult(
            id=payload["id"],
            request=request,
            created_at=now(),
            input_hash=payload["input_hash"],
            input_snapshot=[_sample_signature(workspace, request, s) for s in request.inputs],
            columns=columns,
            data=data,
            diagnostics=diagnostics,
            versions={library: version(library) for library in libraries},
            warnings=warnings,
            duration_seconds=time.monotonic() - started,
        )
        atomic_json(directory / "result.json", result.model_dump())
        progress("Analysis complete", 1)
    except Exception as exc:
        atomic_json(directory / "error.json", {"error": f"{type(exc).__name__}: {exc}"})
    finally:
        stop.set()
        if store:
            store.close()
