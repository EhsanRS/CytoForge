"""Exact time bins on one million original events with analytic reference values."""

import json
import platform
import time
from pathlib import Path

import numpy as np
from cytoforge import kinetics
from cytoforge.models import AnalysisInput, Channel, KineticsRequest, Sample, Workspace, new_id
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

root = Path(__file__).resolve().parents[1]
store = Store(root / ".tmp" / f"benchmark-kinetics-{time.time_ns()}")
reports = []
try:
    for acquisitions in (1, 16):
        doc = Workspace(name="Kinetics analytic performance")
        for i in range(acquisitions):
            count = 1000000 // acquisitions
            bins = np.arange(count) % 256
            # Stable sorting returns ordinary monotonic acquisition time;
            # all events in a bin have the same known signal.
            times = np.sort(bins).astype(float) + 0.5
            events = np.column_stack([times, 4 * times + 8])
            sample = Sample(
                name=f"Acquisition {i}",
                event_count=count,
                channels=[Channel(name="Time"), Channel(name="Signal")],
            )
            sample.sha256 = save_events(store.data_path(doc.id, sample.id), events)
            doc.samples.append(sample)
        doc = store.create(doc)
        request = KineticsRequest(
            revision=doc.revision,
            inputs=[AnalysisInput(sample_id=s.id) for s in doc.samples],
            channel="Signal",
            time_min=0,
            time_max=256,
            bins=256,
            statistic="mean",
            threshold=520,
            smoothing="gaussian",
            smoothing_width=5,
            gaussian_sigma=1,
        )
        engine = Engine(store)
        elapsed = []
        for _ in range(3):
            started = time.perf_counter()
            result, arrays = kinetics.calculate(doc, request, engine, new_id())
            elapsed.append(time.perf_counter() - started)
            for fit, data in zip(result.fits, result.data, strict=True):
                np.testing.assert_allclose(
                    [b.raw_value for b in fit.bins], 4 * (np.arange(256) + 0.5) + 8, rtol=1e-12
                )
                assert data.represented_count == data.event_count
                assert sum(b.responder_count for b in fit.bins) == int(
                    (arrays[data.sample_id][:, 1] > 520).sum()
                )
                kinetics.validate_data(result, data, arrays[data.sample_id])
        started = time.perf_counter()
        encoded = result.model_dump_json()
        reports.append(
            dict(
                events=1000000,
                acquisitions=acquisitions,
                time_bins=256,
                smoothing="gaussian",
                first_seconds=elapsed[0],
                warm_median_seconds=float(np.median(elapsed[1:])),
                all_seconds=elapsed,
                report_bytes=len(encoded.encode()),
                json_seconds=time.perf_counter() - started,
                original_event_output_bytes=sum(a.nbytes for a in arrays.values()),
            )
        )
    evidence = dict(
        machine=platform.platform(),
        reports=reports,
        notes=(
            "Synthetic analytic signals on one host. Full original events; independent "
            "unsmoothed curve and strict responder counts checked after every run. Timings exclude "
            "input generation, file writes, validation, HTTP and rendering. OS pages may be warm. "
            "This is not biological or FlowJo parity evidence."
        ),
    )
    (root / "artifacts/kinetics-benchmark.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence, indent=2))
finally:
    store.close()
