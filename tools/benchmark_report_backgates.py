"""Check million-event backgate counts, visible sampling and streamed event identities."""

import hashlib
import json
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np
from cytoforge import reports
from cytoforge.models import (
    Channel,
    Gate,
    GateDimension,
    LayoutDefinition,
    PlotDefinition,
    ReportElement,
    Sample,
    ThreeDView,
    Workspace,
)
from cytoforge.plotting import plot_payload
from cytoforge.science import Engine, save_events
from cytoforge.store import Store
from cytoforge.three_dimensional import CHUNK_EVENTS, point_chunk, prepare

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def measured(call):
    durations = []
    for _ in range(5):
        started = time.perf_counter()
        result = call()
        durations.append(time.perf_counter() - started)
    return result, statistics.median(durations)


def main():
    inputs = {
        str(p.relative_to(ROOT)): digest(p) for p in (ROOT / "backend/cytoforge").rglob("*.py")
    }
    timings = {}
    with tempfile.TemporaryDirectory(prefix="backgate-benchmark-", dir=ROOT / ".tmp") as owned:
        store = Store(Path(owned))
        try:
            ids = np.arange(1024 * 1024, dtype=np.uint64)
            values = np.c_[ids % 1024, ids // 1024, ids % 16].astype(float)
            truth = (
                (values[:, 0] >= 256)
                & (values[:, 0] < 768)
                & (values[:, 1] >= 256)
                & (values[:, 1] < 768)
                & (values[:, 2] >= 4)
                & (values[:, 2] < 12)
            )
            assert np.count_nonzero(truth) == 131072
            sample = Sample(
                name="Complete labelled grid",
                channels=[Channel(name=c) for c in "XYZ"],
                event_count=len(ids),
            )
            gate = Gate(
                sample_id=sample.id,
                name="Known interest",
                kind="hyperrectangle",
                dimensions=[
                    GateDimension(channel=c, minimum=low, maximum=high)
                    for c, low, high in [("X", 256, 768), ("Y", 256, 768), ("Z", 4, 12)]
                ],
            )
            doc = Workspace(name="Owned backgate benchmark", samples=[sample], gates=[gate])
            path = store.data_path(doc.id, sample.id)
            sample.sha256 = save_events(path, values)
            doc = store.create(doc)
            original = doc.model_dump_json()
            engine = Engine(store)
            np.testing.assert_array_equal(engine.mask(doc, sample, gate.id), truth)
            for mode in ["histogram", "cdf", "density", "scatter"]:
                options = dict(
                    x="X",
                    y=None if mode in {"histogram", "cdf"} else "Y",
                    mode=mode,
                    bins=32,
                    bounds=[0, 1023] * (1 if mode in {"histogram", "cdf"} else 2),
                )
                baseline = plot_payload(doc, engine, sample.id, **options)
                result, seconds = measured(
                    lambda options=options: plot_payload(
                        doc, engine, sample.id, backgate_id=gate.id, **options
                    )
                )
                assert {
                    k: v for k, v in result.items() if not k.startswith("backgate_")
                } == baseline
                assert (
                    result["count"] == result["finite_count"] == result["visible_count"] == len(ids)
                )
                assert result["backgate_count"] == result["backgate_visible_count"] == 131072
                assert result["backgate_displayed_count"] == 6000 and result["backgate_sampling"]
                if mode != "scatter":
                    np.testing.assert_array_equal(
                        result["counts"],
                        np.full(
                            1024 if mode == "density" else 32, 1024 if mode == "density" else 32768
                        ),
                    )
                timings[f"{mode}_cached_median_seconds"] = seconds
            view = ThreeDView(z="Z")
            cloud, seconds = measured(
                lambda: prepare(
                    doc,
                    engine,
                    sample.id,
                    "X",
                    "Y",
                    view,
                    bounds=[0, 1023, 0, 1023, 0, 15],
                    backgate_id=gate.id,
                )
            )
            timings["three_d_metadata_cached_median_seconds"] = seconds
            assert cloud[0]["backgate_count"] == cloud[0]["backgate_displayed_count"] == 131072
            event_hash, backgate_hash = hashlib.sha256(), hashlib.sha256()
            started = time.perf_counter()
            for start in range(0, len(ids), CHUNK_EVENTS):
                points = point_chunk(cloud, start)
                np.testing.assert_array_equal(
                    points["backgate"], truth[start : start + len(points)]
                )
                event_hash.update(points["event_id"].tobytes())
                backgate_hash.update(points["event_id"][points["backgate"] > 0.5].tobytes())
            timings["complete_three_d_stream_seconds"] = time.perf_counter() - started
            assert event_hash.hexdigest() == hashlib.sha256(ids.tobytes()).hexdigest()
            assert backgate_hash.hexdigest() == hashlib.sha256(ids[truth].tobytes()).hexdigest()
            plot = PlotDefinition(
                sample_id=sample.id,
                x="X",
                y="Y",
                mode="density",
                bounds=[0, 1023] * 2,
                bins=32,
                backgate_id=gate.id,
                show_gates=False,
            )
            layout = LayoutDefinition(
                name="Known backgate report",
                elements=[ReportElement(kind="plot", plot=plot, width_mm=180, height_mm=140)],
            )
            started = time.perf_counter()
            page = reports.render(
                doc, engine, reports.ReportRequest(revision=doc.revision, definition=layout)
            )
            timings["density_vector_report_seconds"] = time.perf_counter() - started
            assert page["exportable"], page["issues"]
            layer = page["manifest"]["elements"][0]["layers"][0]
            assert (
                layer["backgate"]["count"] == 131072
                and layer["backgate"]["displayed_count"] == 6000
            )
            assert "cytoforge-backgate-0" in page["svg"] and "<image" not in page["svg"]
            assert store.get(doc.id).model_dump_json() == original and digest(path) == sample.sha256
        finally:
            store.close()
    assert all(digest(ROOT / name) == checksum for name, checksum in inputs.items())
    output = dict(
        status="passed",
        events=len(ids),
        known_backgate_events=131072,
        scientific_payloads_unchanged=True,
        complete_three_d_event_identities_verified=True,
        complete_three_d_highlight_membership_verified=True,
        source_and_workspace_unchanged=True,
        generated_test_data_removed=True,
        scope="Source calculations and vector report; HTTP, native GUI and hardware GPU excluded. "
        "Medians are five cached calls; timings depend on host scheduling.",
        timings=timings,
        report_svg_bytes=len(page["svg"].encode()),
        source_sha256=inputs,
    )
    (ROOT / "artifacts/report-backgate-benchmark.json").write_text(
        json.dumps(output, indent=2) + "\n"
    )
    print(
        json.dumps({k: output[k] for k in ["status", "events", "known_backgate_events", "timings"]})
    )


if __name__ == "__main__":
    main()
