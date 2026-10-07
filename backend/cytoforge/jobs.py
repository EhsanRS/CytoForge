"""Durable, bounded process jobs. A completed job never mutates a workspace implicitly."""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import shutil
import threading

import numpy as np

from . import (
    autospill,
    autospread,
    cellcycle,
    kinetics,
    population_comparison,
    proliferation,
    quality,
)
from .analysis import atomic_json, input_hash, is_stale, run_analysis, validate_request
from .biology import population_paths, preserve_table_populations, replacement_target
from .compensation import assign_matrix
from .fileio import mapped_array
from .models import (
    AnalysisRequest,
    AnalysisResult,
    CellCycleRequest,
    CellCycleResult,
    Channel,
    ComputedParameter,
    Gate,
    KineticsRequest,
    KineticsResult,
    PopulationComparisonRequest,
    PopulationComparisonResult,
    ProliferationRequest,
    ProliferationResult,
    QualityApply,
    QualityRequest,
    QualityResult,
    Transform,
    Workspace,
    new_id,
)
from .science import Engine
from .store import ConflictError, Store, now

ACTIVE = {"queued", "running"}
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "applied"}
PLATFORMS = {
    "autospill": (autospill, autospill.AutoSpillResult, autospill.run_autospill),
    "autospread": (autospread, autospread.AutoSpreadResult, autospread.run_autospread),
    "acquisition_qc": (quality, QualityResult, quality.run_quality),
    "cell_cycle": (cellcycle, CellCycleResult, cellcycle.run_cellcycle),
    "proliferation": (proliferation, ProliferationResult, proliferation.run_proliferation),
    "kinetics": (kinetics, KineticsResult, kinetics.run_kinetics),
    "population_comparison": (
        population_comparison,
        PopulationComparisonResult,
        population_comparison.run_population_comparison,
    ),
}


class JobManager:
    def __init__(self, store: Store, workers: int = 2):
        self.store, self.workers = store, workers
        self.directory = store.root / "jobs"
        self.directory.mkdir(exist_ok=True)
        self.context = mp.get_context("spawn")
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.records: dict[str, dict] = {}
        self.processes = {}
        self.thread = None
        for directory in self.directory.iterdir():
            if len(directory.name) != 32 or any(
                c not in "0123456789abcdef" for c in directory.name
            ):
                continue
            try:
                record = json.loads((directory / "state.json").read_text())
                if record["id"] != directory.name:
                    continue
                if record["status"] in ACTIVE:
                    record.update(
                        status="interrupted",
                        stage="Engine stopped before the job completed. Run it again to restart.",
                        finished_at=now(),
                    )
                    atomic_json(directory / "state.json", record)
                self.records[directory.name] = record
            except (OSError, ValueError, KeyError):
                # A damaged job cannot affect scientific snapshots or other jobs.
                continue

    def start(self):
        self.thread = threading.Thread(
            target=self._schedule, name="analysis-scheduler", daemon=True
        )
        self.thread.start()

    def _save(self, record):
        atomic_json(self.directory / record["id"] / "state.json", record)

    def _cleanup_comparison_scratch(self, record):
        if not self._is_comparison(record):
            return
        identifiers = [record.get("workspace_id"), record.get("id")]
        if any(
            not isinstance(value, str)
            or len(value) != 32
            or any(c not in "0123456789abcdef" for c in value)
            for value in identifiers
        ):
            record["cleanup_error"] = "Invalid comparison identity in the job record"
            return
        root = self.store.root.resolve()
        scratch = self.directory / record["id"] / "comparison-scratch"
        partial = (
            self.store.root / "comparisons" / record["workspace_id"] / f"{record['id']}.npz.partial"
        )
        if any(not path.parent.resolve().is_relative_to(root) for path in [scratch, partial]):
            record["cleanup_error"] = (
                "Comparison temporary paths are outside the job's data directory"
            )
            return
        failures = []
        for path, directory in [(scratch, True), (partial, False)]:
            try:
                if directory:
                    if path.exists():
                        shutil.rmtree(path)
                else:
                    # Final count artifacts stay available for reviewed results and undo.
                    path.unlink(missing_ok=True)
            except OSError as error:
                failures.append(str(error))
        if failures:
            record["cleanup_error"] = "Could not remove comparison temporary files: " + "; ".join(
                failures
            )
        else:
            record.pop("cleanup_error", None)

    def submit(
        self,
        workspace: Workspace,
        request: AnalysisRequest
        | QualityRequest
        | CellCycleRequest
        | ProliferationRequest
        | KineticsRequest
        | PopulationComparisonRequest
        | autospill.AutoSpillRequest
        | autospread.AutoSpreadRequest,
    ) -> dict:
        if workspace.revision != request.revision:
            raise ConflictError("Workspace changed. Reload before starting the analysis.")
        platform = PLATFORMS.get(request.algorithm)
        module = platform[0] if platform else None
        (module.validate_request if module else validate_request)(workspace, request)
        if isinstance(request, (CellCycleRequest, ProliferationRequest, KineticsRequest)):
            replacement_target(workspace, request, module.input_snapshot(workspace, request))
        if isinstance(request, PopulationComparisonRequest):
            population_comparison.replacement_target(workspace, request)
        with self.lock:
            if self.stop.is_set():
                raise ValueError("The analysis engine is stopping")
            if sum(r["status"] in ACTIVE for r in self.records.values()) >= 20:
                raise ValueError("There are already 20 queued or running analyses")
            identifier = new_id()
            directory = self.directory / identifier
            directory.mkdir()
            atomic_json(
                directory / "input.json",
                {
                    "id": identifier,
                    "workspace": workspace.model_dump(),
                    "request": request.model_dump(),
                    "input_hash": (module.input_hash if module else input_hash)(workspace, request),
                    "data_dir": str(self.store.root),
                    "parent_pid": os.getpid(),
                },
            )
            record = {
                "id": identifier,
                "workspace_id": workspace.id,
                "request": request.model_dump(),
                "status": "queued",
                "stage": "Waiting for an analysis worker",
                "progress": 0,
                "created_at": now(),
                "started_at": None,
                "finished_at": None,
                "error": None,
            }
            self._save(record)
            self.records[identifier] = record
            return dict(record, result=None, stale=False, can_apply=False)

    def _schedule(self):
        while not self.stop.wait(0.1):
            with self.lock:
                for identifier, process in list(self.processes.items()):
                    record = self.records[identifier]
                    directory = self.directory / identifier
                    try:
                        progress = json.loads((directory / "progress.json").read_text())
                        record.update(progress)
                    except (OSError, ValueError):
                        pass
                    if process.is_alive():
                        continue
                    process.join()
                    self.processes.pop(identifier)
                    self._cleanup_comparison_scratch(record)
                    try:
                        if process.exitcode != 0:
                            raise ValueError(f"Analysis worker exited with code {process.exitcode}")
                        if (directory / "error.json").exists():
                            raise ValueError(
                                json.loads((directory / "error.json").read_text())["error"]
                            )
                        self._result(record)
                        record.update(
                            status="succeeded",
                            progress=1,
                            stage="Ready for interval review"
                            if self._is_quality(record)
                            else "Ready to add to workspace",
                        )
                    except (OSError, ValueError, KeyError) as exc:
                        record.update(status="failed", error=str(exc), stage="Analysis failed")
                    record["finished_at"] = now()
                    self._save(record)
                for record in self.records.values():
                    if record["status"] != "queued" or len(self.processes) >= self.workers:
                        continue
                    platform = PLATFORMS.get(record["request"]["algorithm"])
                    process = self.context.Process(
                        target=platform[2] if platform else run_analysis,
                        args=(str(self.directory / record["id"]),),
                        name=f"cytoforge-{record['request']['algorithm']}",
                        daemon=True,
                    )
                    try:
                        process.start()
                        self.processes[record["id"]] = process
                        record.update(
                            status="running", stage="Starting analysis worker", started_at=now()
                        )
                    except (OSError, RuntimeError) as exc:
                        record.update(status="failed", error=str(exc), finished_at=now())
                    self._save(record)

    @staticmethod
    def _is_quality(record):
        return record["request"].get("algorithm") == "acquisition_qc"

    @staticmethod
    def _is_cellcycle(record):
        return record["request"].get("algorithm") == "cell_cycle"

    @staticmethod
    def _is_proliferation(record):
        return record["request"].get("algorithm") == "proliferation"

    @staticmethod
    def _is_autospill(record):
        return record["request"].get("algorithm") == "autospill"

    @staticmethod
    def _is_autospread(record):
        return record["request"].get("algorithm") == "autospread"

    @staticmethod
    def _is_kinetics(record):
        return record["request"].get("algorithm") == "kinetics"

    @staticmethod
    def _is_comparison(record):
        return record["request"].get("algorithm") == "population_comparison"

    def _result(self, record):
        platform = PLATFORMS.get(record["request"]["algorithm"])
        model = platform[1] if platform else AnalysisResult
        return model.model_validate_json(
            (self.directory / record["id"] / "result.json").read_text()
        )

    def get(self, workspace: Workspace, identifier: str, full=True) -> dict:
        with self.lock:
            record = self.records.get(identifier)
            if not record or record["workspace_id"] != workspace.id:
                raise KeyError("Analysis job not found")
            response = dict(record, result=None, stale=False, can_apply=False)
            if record["status"] in {"succeeded", "applied"}:
                if self._is_autospread(record):
                    result = self._result(record)
                    matrix = next(
                        (c for c in workspace.compensations if c.id == result.request.matrix_id),
                        None,
                    )
                    saved = matrix.provenance.get("autospread") if matrix else None
                    applied = saved is not None and saved.get("id") == identifier
                    response.update(
                        status="applied" if applied else "succeeded",
                        stage="Saved with matrix" if applied else "Ready to review spreading",
                        result=result.model_dump(),
                        stale=autospread.is_stale(workspace, result),
                    )
                    response["can_apply"] = not applied and not response["stale"]
                    if not full:
                        response["result"]["input_snapshot"] = {}
                        for control in response["result"]["controls"]:
                            control["pairs"] = []
                    return response
                if self._is_autospill(record):
                    result = self._result(record)
                    saved = next((c for c in workspace.compensations if c.id == identifier), None)
                    if saved is not None:
                        result = autospill.saved_result(saved)
                    response.update(
                        status="applied" if saved else "succeeded",
                        stage="Saved in workspace" if saved else "Ready to review compensation",
                        result=result.model_dump(),
                        stale=autospill.is_stale(workspace, result),
                    )
                    response["can_apply"] = saved is None and not response["stale"]
                    if not full:
                        response["result"]["input_snapshot"] = {}
                        response["result"]["compensation"]["provenance"] = {}
                        response["result"]["diagnostics"] = {
                            key: result.diagnostics[key]
                            for key in ("converged", "stop_reason", "final_max_error", "iterations")
                        }
                    return response
                field = {
                    "acquisition_qc": "quality_results",
                    "cell_cycle": "cell_cycle_results",
                    "proliferation": "proliferation_results",
                    "kinetics": "kinetics_results",
                    "population_comparison": "comparison_results",
                }.get(record["request"]["algorithm"], "analyses")
                saved = next((r for r in getattr(workspace, field) if r.id == identifier), None)
                result = saved or self._result(record)
                response["request"] = result.request.model_dump()
                if saved is not None:
                    response.update(status="applied", stage="Saved in workspace")
                elif self._is_comparison(record):
                    response.update(status="succeeded", stage="Ready to add to workspace")
                response["result"] = result.model_dump()
                qc = self._is_quality(record)
                cc = self._is_cellcycle(record)
                pr = self._is_proliferation(record)
                kin = self._is_kinetics(record)
                comparison = self._is_comparison(record)
                if not full and comparison:
                    response["result"]["input_snapshot"] = {}
                    response["result"]["row_count"] = len(result.rows)
                    response["result"]["rows"] = []
                    response["result"]["joint_rows"] = []
                elif not full and qc:
                    response["result"]["bins"] = []
                    response["result"]["diagnostics"] = {}
                    response["result"]["input_snapshot"] = {}
                elif not full and kin:
                    response["result"]["input_snapshot"] = {}
                    for fit in response["result"]["fits"]:
                        fit["bins"] = []
                elif not full and (cc or pr):
                    response["result"]["input_snapshot"] = {}
                    for fit in response["result"]["fits"]:
                        for key in ("edges", "observed", "components", "weights"):
                            fit[key] = []
                        fit["diagnostics"].pop("residuals", None)
                elif not full:
                    response["result"]["diagnostics"] = {
                        k: v
                        for k, v in result.diagnostics.items()
                        if k not in {"loadings", "codes", "pca_mean", "node_metaclusters"}
                    }
                platform = PLATFORMS.get(record["request"]["algorithm"])
                response["stale"] = (platform[0].is_stale if platform else is_stale)(
                    workspace, result
                )
                response["can_apply"] = response["status"] == "succeeded" and (
                    not response["stale"]
                    if platform
                    else record["request"]["revision"] == workspace.revision
                )
                if response["can_apply"] and (cc or pr or kin) and result.request.replace_result_id:
                    try:
                        replacement_target(
                            workspace,
                            result.request,
                            platform[0].input_snapshot(workspace, result.request),
                        )
                    except (ValueError, KeyError, StopIteration) as exc:
                        response.update(can_apply=False, apply_blocker=str(exc))
                if response["can_apply"] and comparison and result.request.replace_result_id:
                    try:
                        population_comparison.replacement_target(workspace, result.request)
                    except ValueError as exc:
                        response.update(can_apply=False, apply_blocker=str(exc))
            return response

    def list(
        self,
        workspace: Workspace,
        qc=False,
        cc=False,
        pr=False,
        asp=False,
        kin=False,
        comp=False,
        spread=False,
    ) -> list[dict]:
        with self.lock:
            records = sorted(
                (
                    r
                    for r in self.records.values()
                    if r["workspace_id"] == workspace.id
                    and self._is_quality(r) == qc
                    and self._is_cellcycle(r) == cc
                    and self._is_proliferation(r) == pr
                    and self._is_autospill(r) == asp
                    and self._is_autospread(r) == spread
                    and self._is_kinetics(r) == kin
                    and self._is_comparison(r) == comp
                ),
                key=lambda r: r["created_at"],
            )
            return [self.get(workspace, r["id"], full=False) for r in reversed(records[-100:])]

    def cancel(self, workspace: Workspace, identifier: str) -> dict:
        with self.lock:
            record = self.records.get(identifier)
            if not record or record["workspace_id"] != workspace.id:
                raise KeyError("Analysis job not found")
            if record["status"] not in ACTIVE:
                raise ValueError("This analysis is no longer queued or running")
            process = self.processes.pop(identifier, None)
            if process:
                process.terminate()
                process.join(2)
                if process.is_alive():
                    process.kill()
                    process.join(2)
            if process is None or not process.is_alive():
                self._cleanup_comparison_scratch(record)
            record.update(status="cancelled", stage="Cancelled by user", finished_at=now())
            self._save(record)
            return self.get(workspace, identifier)

    def apply(self, workspace_id: str, identifier: str, expected: int) -> Workspace:
        with self.lock:
            workspace = self.store.get(workspace_id)
            record = self.get(workspace, identifier)
            if self._is_quality(record):
                raise ValueError("Review acquisition intervals before applying this QC job")
            if self._is_cellcycle(record):
                raise ValueError("Review the cell-cycle fit before applying this job")
            if self._is_proliferation(record):
                raise ValueError("Review the proliferation fit before applying this job")
            if self._is_kinetics(record):
                raise ValueError("Review kinetics curves and time ranges before applying this job")
            if self._is_comparison(record):
                raise ValueError("Review population comparison results before applying this job")
            if self._is_autospill(record):
                raise ValueError("Review compensation and select target samples before applying")
            if self._is_autospread(record):
                raise ValueError("Review spreading before saving its report with the matrix")
            if record["status"] != "succeeded":
                raise ValueError("Only a successful analysis can be added to the workspace")
            if not record["can_apply"] or record["stale"]:
                raise ConflictError(
                    "Workspace changed after this analysis started. "
                    "Run it again with current inputs."
                )
            result = AnalysisResult.model_validate(record["result"])
            for data in result.data:
                path = self.store.analysis_path(workspace_id, result.id, data.sample_id)
                with path.open("rb") as handle:
                    digest = hashlib.file_digest(handle, "sha256").hexdigest()
                if digest != data.sha256:
                    raise ValueError("Analysis output failed its integrity check")
                with mapped_array(path) as values:
                    if (
                        values.shape != (data.event_count, len(result.columns))
                        or values.dtype.kind != "f"
                    ):
                        raise ValueError("Analysis output shape does not match event identities")
                    from .analysis import validate_result_data

                    validate_result_data(self.store, workspace_id, result, data, values)

            pheno_memberships = {}
            if result.request.algorithm == "phenograph" and result.request.create_cluster_gates:
                for data in result.data:
                    with mapped_array(
                        self.store.analysis_path(workspace_id, result.id, data.sample_id)
                    ) as values:
                        pheno_memberships[data.sample_id] = [
                            int(label)
                            for label in np.unique(values[:, 0])
                            if np.isfinite(label) and label > 0
                        ]
                if sum(map(len, pheno_memberships.values())) > 2000:
                    raise ValueError(
                        "This result would create more than 2,000 community gates; "
                        "rerun with fewer inputs or a larger minimum community size"
                    )

            def change(doc):
                if is_stale(doc, result):
                    raise ConflictError("The scientific inputs changed. Rerun this analysis.")
                doc.analyses.append(result)
                raw_parent = None
                if not result.request.compensated:
                    from .acquired_gates import acquired_gate_copies

                    _, raw_parent, _ = acquired_gate_copies(
                        doc, result.request.name, dict(analysis_id=result.id)
                    )
                for source in result.request.inputs:
                    sample = next(s for s in doc.samples if s.id == source.sample_id)
                    if raw_parent:
                        raw_parent(source.gate_id)
                    if set(result.columns) & {c.name for c in sample.channels}:
                        raise ValueError("Analysis output parameter names already exist")
                    for index, name in enumerate(result.columns):
                        if result.request.algorithm == "phenograph":
                            sample.channels.append(
                                Channel(
                                    name=name,
                                    label=name,
                                    range=max(1, len(result.diagnostics["community_sizes"]) + 1),
                                    transform=Transform(kind="linear"),
                                )
                            )
                        else:
                            sample.channels.append(Channel(name=name, label=name, range=1))
                        sample.computed_parameters.append(
                            ComputedParameter(name=name, analysis_id=result.id, index=index)
                        )
                    if (
                        result.request.algorithm == "flowsom"
                        and result.request.create_cluster_gates
                    ):
                        for cluster in range(1, result.request.n_clusters + 1):
                            doc.gates.append(
                                Gate(
                                    sample_id=sample.id,
                                    parent_id=raw_parent(source.gate_id)
                                    if raw_parent
                                    else source.gate_id,
                                    name=f"{result.request.name} · Cluster {cluster}",
                                    kind="range",
                                    x=result.columns[1],
                                    bounds=[cluster - 0.5, cluster + 0.5],
                                    color=["#38d9ba", "#719bff", "#edb96c", "#b595f6", "#ef8b9b"][
                                        (cluster - 1) % 5
                                    ],
                                )
                            )
                    elif (
                        result.request.algorithm == "phenograph"
                        and result.request.create_cluster_gates
                    ):
                        for cluster in pheno_memberships[sample.id]:
                            doc.gates.append(
                                Gate(
                                    sample_id=sample.id,
                                    parent_id=raw_parent(source.gate_id)
                                    if raw_parent
                                    else source.gate_id,
                                    name=f"{result.request.name} · Community {cluster}",
                                    kind="range",
                                    x=result.columns[0],
                                    bounds=[cluster - 0.5, cluster + 0.5],
                                    color=["#38d9ba", "#719bff", "#edb96c", "#b595f6", "#ef8b9b"][
                                        (cluster - 1) % 5
                                    ],
                                    provenance=dict(
                                        analysis_id=result.id,
                                        community=cluster,
                                        membership_basis="fitted_phenograph_community",
                                        input_hash=result.input_hash,
                                    ),
                                )
                            )

            document = self.store.mutate(
                workspace_id, f"Add analysis {result.request.name}", change, expected
            )
            record = self.records[identifier]
            record.update(status="applied", stage="Added to workspace")
            self._save(record)
            return document

    def apply_cellcycle(self, workspace_id: str, identifier: str, expected: int) -> Workspace:
        return self._apply_biology(workspace_id, identifier, expected, "cell_cycle")

    def apply_proliferation(self, workspace_id: str, identifier: str, expected: int) -> Workspace:
        return self._apply_biology(workspace_id, identifier, expected, "proliferation")

    def apply_kinetics(self, workspace_id: str, identifier: str, expected: int) -> Workspace:
        return self._apply_biology(workspace_id, identifier, expected, "kinetics")

    def apply_comparison(self, workspace_id: str, identifier: str, expected: int) -> Workspace:
        with self.lock:
            workspace = self.store.get(workspace_id)
            record = self.get(workspace, identifier)
            if not self._is_comparison(record) or record["status"] != "succeeded":
                raise ValueError("Only a successful, reviewed population comparison can be saved")
            if not record["can_apply"] or record["stale"]:
                raise ConflictError("Scientific inputs changed. Run the comparison again.")
            result = PopulationComparisonResult.model_validate(record["result"])
            population_comparison.load_artifact(self.store, workspace_id, result)

            def change(doc):
                if population_comparison.is_stale(doc, result):
                    raise ConflictError("Scientific inputs changed. Run the comparison again.")
                population_comparison.verify_sources(doc, result.request, Engine(self.store))
                population_comparison.replacement_target(doc, result.request)
                doc.comparison_results.append(result)

            document = self.store.mutate(
                workspace_id, f"Add population comparison {result.request.name}", change, expected
            )
            self.records[identifier].update(status="applied", stage="Saved in workspace")
            self._save(self.records[identifier])
            return document

    def apply_autospread(self, workspace_id, identifier, expected):
        with self.lock:
            workspace = self.store.get(workspace_id)
            record = self.get(workspace, identifier)
            if not self._is_autospread(record) or record["status"] != "succeeded":
                raise ValueError("Only a successful, reviewed spreading job can be saved")
            result = autospread.AutoSpreadResult.model_validate(record["result"])
            if record["stale"]:
                raise ConflictError("Matrix or control populations changed; recalculate spreading")

            def change(doc):
                if autospread.is_stale(doc, result):
                    raise ConflictError(
                        "Matrix or control populations changed; recalculate spreading"
                    )
                autospread.verify_data(doc, result.input_snapshot, self.store)
                matrix = next(c for c in doc.compensations if c.id == result.request.matrix_id)
                matrix.provenance["autospread"] = result.model_dump()

            document = self.store.mutate(
                workspace_id, f"Save {result.request.name}", change, expected
            )
            self.records[identifier].update(status="applied", stage="Saved with matrix")
            self._save(self.records[identifier])
            return document

    def apply_autospill(
        self,
        workspace_id,
        identifier,
        expected,
        sample_ids,
        acknowledge_unconverged=False,
        save_cleanup_gates=True,
    ):
        with self.lock:
            workspace = self.store.get(workspace_id)
            record = self.get(workspace, identifier)
            if not self._is_autospill(record) or record["status"] != "succeeded":
                raise ValueError("Only a successful, reviewed AutoSpill job can be applied")
            result = autospill.AutoSpillResult.model_validate(record["result"])
            if record["stale"]:
                raise ConflictError("Acquired controls or cleanup gates changed. Recalculate.")
            if (
                not result.diagnostics["converged"]
                or result.diagnostics.get("reconstruction_within_tolerance") is False
            ) and not acknowledge_unconverged:
                raise ValueError(
                    "Acknowledge the unmet residual or detector reconstruction tolerance "
                    "before saving this matrix"
                )

            def change(doc):
                if autospill.is_stale(doc, result):
                    raise ConflictError("Acquired controls or cleanup gates changed. Recalculate.")
                autospill.verify_control_data(doc, result.request, self.store)
                matrix = result.compensation.model_copy(deep=True)
                matrix.provenance["unconverged_acknowledged"] = bool(acknowledge_unconverged)
                if save_cleanup_gates:
                    created, populations = autospill.save_control_populations(doc, result)
                    matrix.provenance["cleanup_gate_ids"] = created
                    matrix.provenance["control_populations"] = populations
                doc.compensations.append(matrix)
                for sample_id in set(sample_ids):
                    sample = next((s for s in doc.samples if s.id == sample_id), None)
                    if sample is None:
                        raise ValueError("A target sample no longer exists")
                    assign_matrix(sample, matrix)

            document = self.store.mutate(
                workspace_id, f"Apply {result.request.name}", change, expected
            )
            self.records[identifier].update(status="applied", stage="Saved in workspace")
            self._save(self.records[identifier])
            return document

    def _apply_biology(self, workspace_id, identifier, expected, algorithm):
        module, model, _ = PLATFORMS[algorithm]
        cc = algorithm == "cell_cycle"
        kin = algorithm == "kinetics"
        label = "cell-cycle" if cc else "kinetics" if kin else "proliferation"
        field = (
            "cell_cycle_results" if cc else "kinetics_results" if kin else "proliferation_results"
        )
        with self.lock:
            workspace = self.store.get(workspace_id)
            record = self.get(workspace, identifier)
            if record["request"]["algorithm"] != algorithm or record["status"] != "succeeded":
                raise ValueError(f"Only a successful {label} fit can be saved")
            result = model.model_validate(record["result"])
            if record["stale"]:
                raise ConflictError(f"Scientific inputs changed. Fit {label} again.")
            for data in result.data:
                module.load_data(self.store, workspace_id, result, data)

            def change(doc):
                if module.is_stale(doc, result):
                    raise ConflictError(f"Scientific inputs changed. Fit {label} again.")
                target = replacement_target(
                    doc, result.request, module.input_snapshot(doc, result.request)
                )
                paths = population_paths(doc) if target is not None else None
                if target is not None:
                    history = {r.id for r in getattr(doc, field) if r.columns == target.columns}
                    for table in doc.tables:
                        for column in table.columns:
                            if (
                                column.platform == label
                                and column.follow_replacement
                                and column.result_id in history
                            ):
                                column.result_id = result.id
                getattr(doc, field).append(result)
                for source in result.request.inputs:
                    sample = next(s for s in doc.samples if s.id == source.sample_id)
                    if target is not None:
                        for parameter in sample.computed_parameters:
                            if parameter.analysis_id == target.id:
                                parameter.analysis_id = result.id
                        for channel in sample.channels:
                            if channel.name in target.columns and channel.label.startswith(
                                target.request.name + " "
                            ):
                                channel.label = (
                                    result.request.name + channel.label[len(target.request.name) :]
                                )
                        for gate in doc.gates:
                            if (
                                gate.sample_id == sample.id
                                and gate.provenance.get(f"{algorithm}_id") == target.id
                            ):
                                gate.provenance[f"{algorithm}_id"] = result.id
                                if gate.name.startswith(target.request.name + " · "):
                                    gate.name = (
                                        result.request.name + gate.name[len(target.request.name) :]
                                    )
                    elif set(result.columns) & {c.name for c in sample.channels}:
                        raise ValueError(
                            f"{label.title()} output parameter names already exist; rename the fit"
                        )
                    for index, name in enumerate([] if target is not None else result.columns):
                        sample.channels.append(
                            Channel(
                                name=name,
                                label=name,
                                range=len(result.columns)
                                if index == len(result.columns) - 1
                                else 1,
                            )
                        )
                        sample.computed_parameters.append(
                            ComputedParameter(name=name, analysis_id=result.id, index=index)
                        )
                    if kin:
                        kinetics.sync_gates(doc, result, sample, source, target)
                        continue
                    if (
                        result.request.create_phase_gates
                        if cc
                        else result.request.create_generation_gates
                    ) and not any(
                        g.sample_id == sample.id
                        and g.provenance.get(f"{algorithm}_id") == result.id
                        for g in doc.gates
                    ):
                        labels = (
                            cellcycle.PHASES
                            if cc
                            else [f"Generation {i}" for i in range(result.request.generations + 1)]
                        )
                        for index, phase in enumerate(labels, 1 if cc else 0):
                            doc.gates.append(
                                Gate(
                                    sample_id=sample.id,
                                    parent_id=source.gate_id,
                                    name=f"{result.request.name} · {phase}",
                                    kind="range",
                                    x=result.columns[-1],
                                    bounds=[index - 0.5, index + 0.5],
                                    color=["#719bff", "#38d9ba", "#edb96c", "#b595f6", "#ef8b9b"][
                                        (index - 1 if cc else index) % 5
                                    ],
                                    provenance={
                                        f"{algorithm}_id": result.id,
                                        "assignment": "Maximum phase probability"
                                        if cc
                                        else "Maximum generation probability",
                                    },
                                )
                            )
                if paths is not None:
                    preserve_table_populations(doc, paths)

            document = self.store.mutate(
                workspace_id, f"Save {label} fit {result.request.name}", change, expected
            )
            self.records[identifier].update(status="applied", stage="Fit saved to workspace")
            self._save(self.records[identifier])
            return document

    def apply_quality(self, workspace_id: str, identifier: str, review: QualityApply) -> Workspace:
        with self.lock:
            workspace = self.store.get(workspace_id)
            record = self.get(workspace, identifier)
            if not self._is_quality(record) or record["status"] != "succeeded":
                raise ValueError("Only a successful QC job can create cleanup populations")
            if record["stale"]:
                raise ConflictError("Scientific inputs changed. Run acquisition QC again.")
            result = QualityResult.model_validate(record["result"])
            if any(b >= len(result.bins) for b in review.excluded_bins):
                raise ValueError("Review references an acquisition bin that does not exist")
            quality.load_data(self.store, workspace_id, result)

            def change(doc):
                if quality.is_stale(doc, result):
                    raise ConflictError("Scientific inputs changed. Run acquisition QC again.")
                doc.quality_results.append(result)
                retained = Gate(
                    sample_id=result.request.sample_id,
                    parent_id=result.request.gate_id,
                    name=review.name,
                    kind="quality",
                    quality_id=result.id,
                    quality_excluded_bins=review.excluded_bins,
                    quality_exclusions=review.exclusions,
                    provenance={"quality_method": quality.METHOD_VERSION, "reviewed_at": now()},
                )
                doc.gates.append(retained)
                if review.create_rejected:
                    doc.gates.append(
                        retained.model_copy(
                            update={
                                "id": new_id(),
                                "name": f"{review.name[:148]} · Rejected",
                                "quality_keep": False,
                                "color": "#ef8b9b",
                            }
                        )
                    )

            document = self.store.mutate(
                workspace_id, f"Review acquisition QC · {review.name}", change, review.revision
            )
            record = self.records[identifier]
            record.update(status="applied", stage="Reviewed cleanup populations added")
            self._save(record)
            return document

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(2)
        with self.lock:
            for identifier, process in list(self.processes.items()):
                process.terminate()
                process.join(2)
                if process.is_alive():
                    process.kill()
                    process.join(2)
                if not process.is_alive():
                    self._cleanup_comparison_scratch(self.records[identifier])
                self.records[identifier].update(
                    status="interrupted",
                    stage="Engine stopped. Run this analysis again.",
                    finished_at=now(),
                )
                self._save(self.records[identifier])
            self.processes.clear()
            for record in self.records.values():
                if record["status"] == "queued":
                    record.update(
                        status="interrupted",
                        stage="Engine stopped before this job started",
                        finished_at=now(),
                    )
                    self._save(record)
