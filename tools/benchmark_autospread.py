"""Measure a complete spreading calculation against independent base-R fits."""

import hashlib
import json
import multiprocessing as mp
import os
import resource
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
from cytoforge import autospread
from cytoforge.models import Channel, Compensation, Sample, Workspace
from cytoforge.science import Engine, save_events
from cytoforge.store import Store
from scipy.special import ndtri

ROOT = Path(__file__).resolve().parents[1]
R_DRIVER = r"""
args <- commandArgs(trailingOnly=TRUE)
data <- as.matrix(read.csv(args[1], header=FALSE))
x <- sign(data[,1]) * (sqrt(abs(data[,1])+1)-1)
coefficients <- sapply(2:ncol(data),function(j) {
  sigma <- data[,j]; first <- lm(sigma ~ x)
  v <- sigma^2 - coef(first)[1]^2
  adjusted <- sign(v) * (sqrt(abs(v)+1)-1)
  unname(coef(lm(adjusted ~ x - 1))[1])
})
write.table(coefficients,args[2],row.names=FALSE,col.names=FALSE,sep=",")
"""


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def measure(directory, workspace_id, request, expected):
    store = Store(Path(directory))
    try:
        doc = store.get(workspace_id)
        request = autospread.AutoSpreadRequest.model_validate(request)
        baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        started = time.perf_counter()
        result = autospread.calculate(doc, request, Engine(store))
        elapsed = time.perf_counter() - started
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        np.testing.assert_allclose(result.matrix[0][1:], expected, rtol=2e-9, atol=2e-8)
        control = result.controls[0]
        assert control["used_count"] == doc.samples[0].event_count and control["bin_count"] == 256
        assert digest(store.data_path(doc.id, doc.samples[0].id)) == doc.samples[0].sha256
        assert store.get(doc.id).revision == doc.revision
        record = dict(
            status="passed",
            events=control["used_count"],
            outputs=8,
            detectors=12,
            seconds=elapsed,
            additional_peak_rss_mib=max(0, peak - baseline) / 1024,
            coefficient_max_absolute_error=float(
                np.max(np.abs(np.asarray(result.matrix[0][1:]) - expected))
            ),
            all_selected_events_used=True,
            raw_data_unchanged=True,
            workspace_unchanged=True,
        )
        (Path(directory) / "measurement.json").write_text(json.dumps(record, indent=2))
    finally:
        store.close()


def main():
    runtime = ROOT / ".cache/references/r-runtime"
    env = dict(os.environ, **json.loads((runtime / "environment.json").read_text()))
    measurements = []
    with tempfile.TemporaryDirectory(prefix="autospread-benchmark-", dir=ROOT / ".tmp") as owned:
        work = Path(owned)
        driver = work / "oracle.r"
        driver.write_text(R_DRIVER)
        for n in (131072, 1048576):
            directory = work / str(n)
            store = Store(directory)
            F = np.geomspace(1, 100000, 256)
            signal = np.sqrt(F + 1) - 1
            z = ndtri((np.arange(256) + 0.5) / 256)
            repeats = n // 256 // 256
            count = n // 256
            rank = (count - 1) * 0.84
            lo, hi = int(np.floor(rank)), int(np.ceil(rank))
            q84 = (
                z[lo // repeats] * (hi - rank) + z[hi // repeats] * (rank - lo)
                if hi != lo
                else z[lo // repeats]
            )
            sigma = np.column_stack([(30 + j) + (0.4 + 0.08 * j) * signal for j in range(1, 8)])
            oracle = np.column_stack([F, sigma * q84])
            np.savetxt(work / "quantiles.csv", oracle, delimiter=",", fmt="%.17g")
            subprocess.run(
                [
                    str(runtime / "root/usr/lib/R/bin/exec/R"),
                    "--vanilla",
                    "--slave",
                    "-f",
                    str(driver),
                    "--args",
                    str(work / "quantiles.csv"),
                    str(work / "expected.csv"),
                ],
                env=env,
                check=True,
                capture_output=True,
            )
            expected = np.loadtxt(work / "expected.csv", delimiter=",")
            spectra = np.column_stack(
                [np.eye(8), np.random.default_rng(121).uniform(0.05, 0.6, (8, 4))]
            )
            background = np.arange(12, dtype=float) - 4
            detectors, outputs = [f"D{i + 1}" for i in range(12)], [f"F{i + 1}" for i in range(8)]
            matrix = Compensation(
                name="Benchmark spectral",
                kind="spectral",
                detectors=detectors,
                outputs=outputs,
                matrix=spectra.tolist(),
                background=background.tolist(),
                weights=list(range(1, 13)),
            )
            doc = Workspace(name="Synthetic spreading benchmark", compensations=[matrix])
            sample = Sample(
                name="F1 control", event_count=n, channels=[Channel(name=d) for d in detectors]
            )
            measured = np.empty((n, 12))
            for i in range(256):
                latent = np.empty((count, 8))
                latent[:, 0] = F[i]
                for j in range(1, 8):
                    latent[:, j] = sigma[i, j - 1] * np.repeat(np.roll(z, 13 * j), repeats)
                measured[i * count : (i + 1) * count] = latent @ spectra + background
            sample.sha256 = save_events(store.data_path(doc.id, sample.id), measured)
            del measured, latent
            doc.samples.append(sample)
            doc = store.create(doc)
            store.close()
            request = autospread.AutoSpreadRequest(
                revision=doc.revision,
                matrix_id=matrix.id,
                controls=[dict(output="F1", sample_id=sample.id)],
            )
            process = mp.get_context("spawn").Process(
                target=measure, args=(str(directory), doc.id, request.model_dump(), expected)
            )
            process.start()
            deadline = time.monotonic() + 90
            while process.is_alive() and time.monotonic() < deadline:
                process.join(0.25)
            if process.is_alive():
                process.terminate()
                process.join(5)
                raise RuntimeError("Owned spreading benchmark exceeded its deadline")
            assert process.exitcode == 0, process.exitcode
            measurements.append(json.loads((directory / "measurement.json").read_text()))
    names = [
        "backend/cytoforge/autospread.py",
        "backend/cytoforge/science.py",
        "backend/cytoforge/models.py",
        "backend/cytoforge/quality.py",
        "backend/cytoforge/analysis.py",
        "backend/cytoforge/store.py",
        "tools/benchmark_autospread.py",
    ]
    record = dict(
        status="passed",
        scope="Source Linux CPU, one control, 8 outputs / 12 detectors, all 256 quantiles",
        timed_scope=(
            "Checksum verification, raw parent, weighted unmixing, quantiles "
            "and all seven spreading fits"
        ),
        excluded=[
            "fixture_generation",
            "desktop interaction",
            "worker startup",
            "other operating systems",
        ],
        biological_accuracy_verified=False,
        closed_source_parity_verified=False,
        measurements=measurements,
        generated_test_data_removed=True,
        r_oracle_sha256=hashlib.sha256(R_DRIVER.encode()).hexdigest(),
        source_sha256={name: digest(ROOT / name) for name in names},
    )
    (ROOT / "artifacts/autospread-benchmark.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
