"""Verify the self-contained engine, including spawned native analysis workers."""

import argparse
import hashlib
import io
import json
import math
import os
import queue
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
import numpy as np

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--binary", type=Path)
parser.add_argument("--algorithms", default="pca,umap,tsne,flowsom")
args = parser.parse_args()
binary = args.binary or root / "artifacts/engine/cytoforge-engine" / (
    "cytoforge-engine.exe" if os.name == "nt" else "cytoforge-engine"
)
session_dir = root / ".tmp" / f"engine-smoke-{time.time_ns()}"
session_dir.mkdir(parents=True)
lines = queue.Queue()
standalone_env = os.environ.copy()
for name in ["PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"]:
    standalone_env.pop(name, None)
engine = subprocess.Popen(
    [
        str(binary),
        "--port",
        "0",
        "--data-dir",
        str(session_dir / "data"),
        "--parent-pid",
        str(os.getpid()),
    ],
    cwd=root,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env=standalone_env,
)


def capture_output():
    with (session_dir / "engine.log").open("w") as log:
        for line in engine.stdout:
            log.write(line)
            log.flush()
            lines.put(line)


threading.Thread(target=capture_output, daemon=True).start()
with binary.open("rb") as binary_handle:
    binary_sha256 = hashlib.file_digest(binary_handle, "sha256").hexdigest()
evidence = {"binary": str(binary), "binary_sha256": binary_sha256, "analyses": []}
try:
    startup = json.loads(lines.get(timeout=30))
    assert startup["event"] == "cytoforge-starting", startup
    with httpx.Client(base_url=f"http://127.0.0.1:{startup['port']}", timeout=30) as client:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if client.get("/api/health").is_success:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Standalone engine did not become healthy")
        client.headers["X-CytoForge-Token"] = client.get("/api/bootstrap").json()["token"]
        doc = client.post("/api/workspaces", json={"name": "Packaged scientific smoke"}).json()
        # Deterministic varied input avoids importing the source package to create evidence.
        csv = "X,Y,Z\n" + "\n".join(
            f"{(i % 13) + (i % 3) * 8},{(i * 7 % 17) - (i % 3) * 8},{(i * 11 % 19) + (i % 5)}"
            for i in range(128)
        )
        response = client.post(
            f"/api/workspaces/{doc['id']}/import?revision=0",
            files={"files": ("packaged.csv", csv, "text/csv")},
        )
        response.raise_for_status()
        doc = response.json()["workspace"]
        sample_id = doc["samples"][0]["id"]
        for algorithm in args.algorithms.split(","):
            request = {
                "revision": doc["revision"],
                "name": f"Packaged {algorithm}",
                "algorithm": algorithm,
                "inputs": [{"sample_id": sample_id}],
                "channels": ["X", "Y", "Z"],
                "max_events": 64,
                "n_neighbors": 8,
                "perplexity": 8,
                "iterations": 300,
                "grid_size": 4,
                "n_clusters": 3,
                "epochs": 2,
            }
            response = client.post(f"/api/workspaces/{doc['id']}/jobs", json=request)
            response.raise_for_status()
            identifier = response.json()["id"]
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                job = client.get(f"/api/workspaces/{doc['id']}/jobs/{identifier}").json()
                if job["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.2)
            assert job["status"] == "succeeded", job
            response = client.post(
                f"/api/workspaces/{doc['id']}/jobs/{identifier}/apply",
                json={"revision": doc["revision"]},
            )
            response.raise_for_status()
            doc = response.json()
            assert job["result"]["data"][0]["mapped_count"] == (
                64 if algorithm in {"tsne", "phenograph"} else 128
            )
            evidence["analyses"].append(
                {
                    "algorithm": algorithm,
                    "duration_seconds": job["result"]["duration_seconds"],
                    "versions": job["result"]["versions"],
                    "data": job["result"]["data"],
                }
            )
            print(
                f"Packaged {algorithm}: spawned worker, mapped event IDs, and applied outputs",
                flush=True,
            )
        # Exercise spectral control calculation in the frozen application, without
        # importing source modules to prepare the references.
        reference_rows = [[1, 0.2, 0.1], [0.1, 1, 0.3], [0.2, 0.3, 1]]
        background = [2, 3, 4]
        control_ids = []
        for label, sources in [
            ("Unstained", [0, 0, 30]),
            ("F1", [100, 0, 30]),
            ("F2", [0, 200, 30]),
            ("Known mixture", [12, 20, 6]),
        ]:
            measured = [
                b + sum(t * reference_rows[k][j] for k, t in enumerate(sources))
                for j, b in enumerate(background)
            ]
            text = "X,Y,Z\n" + (",".join(map(str, measured)) + "\n") * 120
            response = client.post(
                f"/api/workspaces/{doc['id']}/import?revision={doc['revision']}",
                files={"files": (f"{label}.csv", text, "text/csv")},
            )
            response.raise_for_status()
            doc = response.json()["workspace"]
            control_ids.append(doc["samples"][-1]["id"])
        response = client.post(
            f"/api/workspaces/{doc['id']}/compensations/calculate",
            json={
                "revision": doc["revision"],
                "name": "Frozen spectral controls",
                "kind": "spectral",
                "detectors": ["X", "Y", "Z"],
                "background": background,
                "weights": [1, 2, 0.5],
                "controls": [
                    {
                        "name": "Fluor 1",
                        "positive": {"sample_id": control_ids[1]},
                        "negative": {"sample_id": control_ids[0]},
                    },
                    {
                        "name": "Fluor 2",
                        "positive": {"sample_id": control_ids[2]},
                        "negative": {"sample_id": control_ids[0]},
                    },
                ],
                "autofluorescence": {"name": "AF", "population": {"sample_id": control_ids[0]}},
            },
        )
        response.raise_for_status()
        preview = response.json()
        matrix = preview["compensation"]
        for expected, actual in zip(reference_rows, matrix["matrix"], strict=True):
            assert all(
                math.isclose(a, b, abs_tol=1e-10) for a, b in zip(expected, actual, strict=True)
            )
        response = client.post(
            f"/api/workspaces/{doc['id']}/compensations",
            json={
                "revision": doc["revision"],
                "compensation": matrix,
                "sample_ids": [control_ids[3]],
            },
        )
        response.raise_for_status()
        doc = response.json()
        medians = []
        for channel, expected in zip(["Fluor 1", "Fluor 2", "AF"], [12, 20, 6], strict=True):
            response = client.get(
                f"/api/workspaces/{doc['id']}/samples/{control_ids[3]}/statistics",
                params={"channel": channel},
            )
            response.raise_for_status()
            stats = response.json()
            assert stats["finite_count"] == 120 and math.isclose(
                stats["median"], expected, abs_tol=1e-10
            )
            medians.append(stats["median"])
        evidence["spectral_controls"] = {
            "condition": preview["diagnostics"]["condition_number"],
            "recovered_medians": medians,
        }
        print(
            "Packaged spectral controls recovered known fluorophore/AF intensities "
            "with background and weights.",
            flush=True,
        )
        archive = client.get(f"/api/workspaces/{doc['id']}/export/project")
        archive.raise_for_status()
        restored = client.post(
            "/api/import/project", files={"file": ("smoke.cytoforge", archive.content)}
        )
        restored.raise_for_status()
        assert len(restored.json()["analyses"]) == len(evidence["analyses"])
        response = client.get(
            f"/api/workspaces/{restored.json()['id']}/samples/{control_ids[3]}/statistics",
            params={"channel": "AF"},
        )
        response.raise_for_status()
        assert math.isclose(response.json()["median"], 6, abs_tol=1e-10)
        evidence["archive_restore"] = True
        # Exercise packaged lxml and the bundled ISAC schemas without source imports.
        fixtures = root / "tests/fixtures/interchange/gatingml"
        reference = client.post("/api/workspaces", json={"name": "Packaged interchange"})
        reference.raise_for_status()
        refdoc = reference.json()
        base = f"/api/workspaces/{refdoc['id']}"
        imported = client.post(
            f"{base}/import?revision=0",
            files={"files": ("data1.fcs", (fixtures / "data1.fcs").read_bytes())},
        )
        imported.raise_for_status()
        refdoc = imported.json()["workspace"]
        xml = (fixtures / "gml/gml_all_gates.xml").read_bytes()
        preview = client.post(
            f"{base}/interchange/preview?revision={refdoc['revision']}",
            files={"file": ("gml_all_gates.xml", xml)},
        )
        preview.raise_for_status()
        plan = preview.json()
        result = client.post(
            f"{base}/interchange/apply",
            json={
                "revision": plan["revision"],
                "preview_id": plan["preview_id"],
                "mappings": [{"source_id": "template", "sample_ids": [refdoc["samples"][0]["id"]]}],
            },
        )
        result.raise_for_status()
        refdoc = result.json()
        record = refdoc["interchanges"][0]
        truth_matches = 0
        for row in record["report"]["counts"]:
            truth = fixtures / "truth" / f"Results_{row['name']}.txt"
            if truth.exists():
                assert row["count"] == sum(
                    line.strip() == "1" for line in truth.read_text().splitlines()
                )
                truth_matches += 1
        exported = client.get(f"{base}/samples/{refdoc['samples'][0]['id']}/export/gatingml")
        exported.raise_for_status()
        archive = client.get(f"{base}/export/project")
        archive.raise_for_status()
        restored = client.post(
            "/api/import/project", files={"file": ("interchange.cytoforge", archive.content)}
        )
        restored.raise_for_status()
        source = client.get(
            f"/api/workspaces/{restored.json()['id']}/interchange/{record['id']}/source"
        )
        source.raise_for_status()
        assert source.content == xml
        evidence["interchange"] = {
            "gates": len(refdoc["gates"]),
            "truth_count_matches": truth_matches,
            "schema_validated_export": True,
            "original_xml_restored": True,
        }
        print(
            f"Packaged GatingML validated {truth_matches} reference populations "
            "and restored source XML.",
            flush=True,
        )
        # Verify a frozen QC worker and exact reviewed event populations without
        # importing any source engine module into this smoke-test process.
        qc_doc = client.post("/api/workspaces", json={"name": "Frozen acquisition QC"}).json()
        qc_base = f"/api/workspaces/{qc_doc['id']}"
        rows = ["Time,Signal,Area,Height"]
        timestamp = 0
        for i in range(2000):
            signal = "NaN" if i == 73 else 100 + i % 50 / 5 + (200 if 500 <= i < 550 else 0)
            height = 50 + i % 7
            rows.append(f"{timestamp},{signal},{height * (4 if i == 5 else 2)},{height}")
            timestamp += 0.01 if 800 <= i < 850 else 0.001
        imported = client.post(
            f"{qc_base}/import?revision=0",
            files={"files": ("known-qc.csv", "\n".join(rows), "text/csv")},
        )
        imported.raise_for_status()
        qc_doc = imported.json()["workspace"]
        sid = qc_doc["samples"][0]["id"]
        response = client.post(
            f"{qc_base}/quality/jobs",
            json={
                "revision": qc_doc["revision"],
                "sample_id": sid,
                "channels": ["Signal"],
                "time_channel": "Time",
                "use_transforms": False,
                "bin_events": 50,
                "min_bin_events": 10,
                "pulse_area": "Area",
                "pulse_height": "Height",
            },
        )
        response.raise_for_status()
        qc_id = response.json()["id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            response = client.get(f"{qc_base}/quality/jobs/{qc_id}")
            response.raise_for_status()
            qc_job = response.json()
            if qc_job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.1)
        assert qc_job["status"] == "succeeded", qc_job
        assert [b["index"] for b in qc_job["result"]["bins"] if b["suggested"]] == [10, 16]
        response = client.post(
            f"{qc_base}/quality/jobs/{qc_id}/apply",
            json={
                "revision": qc_doc["revision"],
                "excluded_bins": [10, 16],
                "exclusions": ["nonfinite", "time", "pulse"],
            },
        )
        response.raise_for_status()
        counts = client.get(f"{qc_base}/samples/{sid}/counts")
        counts.raise_for_status()
        assert sorted(r["count"] for r in counts.json()) == [102, 1898]
        archive = client.get(f"{qc_base}/export/project")
        archive.raise_for_status()
        restored = client.post(
            "/api/import/project", files={"file": ("qc.cytoforge", archive.content)}
        )
        restored.raise_for_status()
        restored_base = f"/api/workspaces/{restored.json()['id']}"
        counts = client.get(f"{restored_base}/samples/{sid}/counts")
        counts.raise_for_status()
        assert sorted(r["count"] for r in counts.json()) == [102, 1898]
        response = client.get(f"{restored_base}/quality/{qc_id}")
        response.raise_for_status()
        assert not response.json()["stale"]
        evidence["quality"] = {
            "known_signal_interval": 10,
            "known_rate_interval": 16,
            "retained": 1898,
            "rejected": 102,
            "archive_restored": True,
        }
        print(
            "Packaged QC worker preserved 2,000 event IDs and restored reviewed populations.",
            flush=True,
        )
        # Independently generated latent DNA, without importing the source engine.
        import random

        generator = random.Random(871)
        rows = ["DNA,Time"]
        for i in range(6000):
            phase = i % 10
            dna = 100 if phase < 5 else generator.uniform(100, 198) if phase < 8 else 198
            rows.append(f"{generator.gauss(dna, 0.04 * dna)},{i}")
        rows.append("NaN,6000")
        response = client.post("/api/workspaces", json={"name": "Standalone DNA validation"})
        response.raise_for_status()
        dna_doc = response.json()
        dna_base = f"/api/workspaces/{dna_doc['id']}"
        response = client.post(
            f"{dna_base}/import?revision=0",
            files={"files": ("dna.csv", "\n".join(rows), "text/csv")},
        )
        response.raise_for_status()
        dna_doc = response.json()["workspace"]
        sid = dna_doc["samples"][0]["id"]
        response = client.post(
            f"{dna_base}/cell-cycle/jobs",
            json={
                "revision": dna_doc["revision"],
                "channel": "DNA",
                "inputs": [{"sample_id": sid}],
                "range_max": 280,
                "peak_ratio": {"fixed": 1.98},
                "g1_cv": {"fixed": 4},
                "linked_cv": "g2_to_g1",
            },
        )
        response.raise_for_status()
        dna_id = response.json()["id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            response = client.get(f"{dna_base}/cell-cycle/jobs/{dna_id}")
            response.raise_for_status()
            job = response.json()
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.1)
        assert job["status"] == "succeeded", job.get("error")
        fit = job["result"]["fits"][0]
        assert fit["data"]["fitted_count"] == 6000
        assert sum(fit["assigned_counts"]) == 6000
        assert all(
            abs(found - truth) < 0.025
            for found, truth in zip(fit["fractions"], [0.5, 0.3, 0.2], strict=True)
        )
        response = client.post(
            f"{dna_base}/cell-cycle/jobs/{dna_id}/apply", json={"revision": dna_doc["revision"]}
        )
        response.raise_for_status()
        counts = client.get(f"{dna_base}/samples/{sid}/counts")
        counts.raise_for_status()
        assert sorted(c["count"] for c in counts.json()) == sorted(fit["assigned_counts"])
        figure = client.get(f"{dna_base}/cell-cycle/{dna_id}/figure?sample_id={sid}")
        figure.raise_for_status()
        assert b"<svg" in figure.content
        archive = client.get(f"{dna_base}/export/project")
        archive.raise_for_status()
        response = client.post(
            "/api/import/project", files={"file": ("dna.cytoforge", archive.content)}
        )
        response.raise_for_status()
        restored = response.json()
        result = client.get(f"/api/workspaces/{restored['id']}/cell-cycle/{dna_id}")
        result.raise_for_status()
        assert not result.json()["stale"]
        assert result.json()["fits"][0]["assigned_counts"] == fit["assigned_counts"]
        evidence["cell_cycle"] = {
            "method": "djf",
            "latent_fractions": [0.5, 0.3, 0.2],
            "recovered_fractions": fit["fractions"],
            "fitted_events": 6000,
            "undefined_events": 1,
            "assigned_counts": fit["assigned_counts"],
            "archive_restored": True,
        }
        print(
            "Packaged DJF worker recovered known phase fractions, "
            "event identities and its archive.",
            flush=True,
        )
        # A separate latent CFSE reference exercises frozen control calibration
        # and the lognormal/normal convolution, without source-engine imports.
        generator = random.Random(967)
        response = client.post("/api/workspaces", json={"name": "Standalone CFSE validation"})
        response.raise_for_status()
        prolif_doc = response.json()
        prolif_base = f"/api/workspaces/{prolif_doc['id']}"
        ids = []
        for label, size in [("Dividing", 6000), ("Undivided", 4000), ("Unstained", 4000)]:
            rows = ["CFSE,Time"]
            for i in range(size):
                generation = (
                    (0 if i % 10 < 1 else 1 if i % 10 < 3 else 2 if i % 10 < 6 else 3)
                    if label == "Dividing"
                    else 0
                )
                dye = (
                    1024
                    * 0.5**generation
                    * generator.lognormvariate(0, math.sqrt(math.log1p(0.2**2)))
                    if label != "Unstained"
                    else 0
                )
                rows.append(f"{dye + generator.gauss(20, 2)},{i}")
            if label == "Dividing":
                rows.append("NaN,6000")
            response = client.post(
                f"{prolif_base}/import?revision={prolif_doc['revision']}",
                files={"files": (f"{label}.csv", "\n".join(rows), "text/csv")},
            )
            response.raise_for_status()
            prolif_doc = response.json()["workspace"]
            ids.append(prolif_doc["samples"][-1]["id"])
        response = client.post(
            f"{prolif_base}/proliferation/jobs",
            json={
                "revision": prolif_doc["revision"],
                "channel": "CFSE",
                "inputs": [{"sample_id": ids[0]}],
                "undivided_control": {"sample_id": ids[1]},
                "autofluorescence_control": {"sample_id": ids[2]},
                "control_mode": "fix_mean_cv",
                "generations": 3,
            },
        )
        response.raise_for_status()
        prolif_id = response.json()["id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            response = client.get(f"{prolif_base}/proliferation/jobs/{prolif_id}")
            response.raise_for_status()
            job = response.json()
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.1)
        assert job["status"] == "succeeded", job.get("error")
        fit = job["result"]["fits"][0]
        assert fit["data"]["fitted_count"] == sum(fit["assigned_counts"]) == 6000
        assert all(
            abs(found - truth) < 0.025
            for found, truth in zip(fit["fractions"], [0.1, 0.2, 0.3, 0.4], strict=True)
        )
        event_csv = client.get(f"{prolif_base}/proliferation/{prolif_id}/events?sample_id={ids[0]}")
        event_csv.raise_for_status()
        assert event_csv.text.strip().splitlines()[-1] == "6000,nan,nan,nan,nan,nan"
        response = client.post(
            f"{prolif_base}/proliferation/jobs/{prolif_id}/apply",
            json={"revision": prolif_doc["revision"]},
        )
        response.raise_for_status()
        prolif_doc = response.json()
        counts = client.get(f"{prolif_base}/samples/{ids[0]}/counts")
        counts.raise_for_status()
        assert sorted(c["count"] for c in counts.json()) == sorted(fit["assigned_counts"])
        response = client.patch(
            f"{prolif_base}/biology/proliferation/{prolif_id}",
            json={"revision": prolif_doc["revision"], "name": "Reviewed frozen CFSE"},
        )
        response.raise_for_status()
        assert (
            response.json()["proliferation_results"][0]["request"]["name"] == "Reviewed frozen CFSE"
        )
        prolif_doc = response.json()
        biological_table = {
            "id": "a" * 32,
            "name": "Frozen biological report",
            "row_mode": "samples",
            "sample_ids": [ids[0]],
            "columns": [
                {
                    "id": "b" * 32,
                    "name": "Precursor frequency",
                    "kind": "biology",
                    "platform": "proliferation",
                    "result_id": prolif_id,
                    "biology_metric": "precursor_frequency",
                },
                {
                    "id": "c" * 32,
                    "name": "Generation 2",
                    "kind": "biology",
                    "platform": "proliferation",
                    "result_id": prolif_id,
                    "biology_metric": "fraction",
                    "generation": 2,
                },
            ],
        }
        response = client.post(
            f"{prolif_base}/tables/save",
            json={"revision": prolif_doc["revision"], "definition": biological_table},
        )
        response.raise_for_status()
        biological = client.get(f"{prolif_base}/tables/{biological_table['id']}/evaluate")
        biological.raise_for_status()
        assert (
            abs(
                biological.json()["rows"][0]["values"]["b" * 32]
                - fit["statistics"]["precursor_frequency"]
            )
            < 1e-12
        )
        assert abs(biological.json()["rows"][0]["values"]["c" * 32] - fit["fractions"][2]) < 1e-12
        archive = client.get(f"{prolif_base}/export/project")
        archive.raise_for_status()
        response = client.post(
            "/api/import/project", files={"file": ("cfse.cytoforge", archive.content)}
        )
        response.raise_for_status()
        restored = response.json()
        result = client.get(f"/api/workspaces/{restored['id']}/proliferation/{prolif_id}")
        result.raise_for_status()
        assert not result.json()["stale"]
        assert result.json()["fits"][0]["assigned_counts"] == fit["assigned_counts"]
        copied_table = client.get(
            f"/api/workspaces/{restored['id']}/tables/{biological_table['id']}/evaluate"
        )
        copied_table.raise_for_status()
        assert copied_table.json()["rows"][0]["values"] == biological.json()["rows"][0]["values"]
        figure = client.get(
            f"/api/workspaces/{restored['id']}/proliferation/{prolif_id}/figure?sample_id={ids[0]}"
        )
        figure.raise_for_status()
        assert b"Precursor frequency" in figure.content
        evidence["proliferation"] = {
            "latent_fractions": [0.1, 0.2, 0.3, 0.4],
            "recovered_fractions": fit["fractions"],
            "fitted_events": 6000,
            "undefined_events": 1,
            "assigned_counts": fit["assigned_counts"],
            "statistics": fit["statistics"],
            "calibration": job["result"]["calibration"],
            "archive_restored": True,
            "rename_preserved_compatibility": True,
        }
        print(
            "Packaged proliferation worker calibrated controls, recovered generations, "
            "preserved event identities and restored its renamed archive.",
            flush=True,
        )
        response = client.post("/api/workspaces", json={"name": "Frozen table references"})
        response.raise_for_status()
        table_doc = response.json()
        table_base = f"/api/workspaces/{table_doc['id']}"
        medians = []
        for index, center in enumerate([1, 2, 4, 6, 8, 11]):
            rows = ["X,Y", *[f"{center - 1 + i},{i}" for i in range(5)]]
            response = client.post(
                f"{table_base}/import?revision={table_doc['revision']}",
                files={"files": (f"Sample {index}.csv", "\n".join(rows), "text/csv")},
            )
            response.raise_for_status()
            table_doc = response.json()["workspace"]
            sample_id = table_doc["samples"][-1]["id"]
            response = client.patch(
                f"{table_base}/samples/{sample_id}",
                json={
                    "revision": table_doc["revision"],
                    "name": f"Sample {index}",
                    "tags": {"Treatment": "A" if index < 3 else "B", "Donor": str(index % 3)},
                },
            )
            response.raise_for_status()
            table_doc = response.json()
            response = client.post(
                f"{table_base}/gates",
                json={
                    "revision": table_doc["revision"],
                    "gate": {
                        "id": f"{index + 1:032x}",
                        "sample_id": sample_id,
                        "name": "Cells",
                        "kind": "range",
                        "x": "Y",
                        "bounds": [1, 5],
                    },
                },
            )
            response.raise_for_status()
            table_doc = response.json()
            medians.append(center + 1.5)
        table = dict(
            id="f" * 32,
            name="Frozen response",
            row_mode="samples",
            columns=[
                dict(
                    id="1" * 32,
                    name="Median",
                    statistic="median",
                    channel="X",
                    population_path=["Cells"],
                    heatmap=True,
                ),
                dict(
                    id="2" * 32,
                    name="Double",
                    kind="formula",
                    expression='col("Median") * 2',
                    hidden=True,
                ),
                dict(id="3" * 32, name="Treatment", kind="metadata", metadata_key="Treatment"),
                dict(
                    id="4" * 32,
                    name="Relative",
                    kind="formula",
                    expression='col("Median") / mean(col("Median"))',
                ),
            ],
            pivot=dict(rows=["3" * 32], measures=["1" * 32]),
            comparison=dict(
                group_column="3" * 32,
                group_a="A",
                group_b="B",
                method="mann_whitney",
                measures=["1" * 32, "4" * 32],
            ),
        )
        response = client.post(
            f"{table_base}/tables/evaluate",
            json={"definition": table, "revision": table_doc["revision"], "limit": 1},
        )
        response.raise_for_status()
        evaluated = response.json()
        assert evaluated["total_rows"] == 6 and len(evaluated["rows"]) == 1
        table = evaluated["definition"]
        table["columns"][0]["name"] = "Signal"
        response = client.post(
            f"{table_base}/tables/save",
            json={"definition": table, "revision": table_doc["revision"]},
        )
        response.raise_for_status()
        table_doc = response.json()
        body = {"definition": table_doc["tables"][0], "revision": table_doc["revision"], "limit": 1}
        response = client.post(f"{table_base}/tables/export/json", json=body)
        response.raise_for_status()
        evaluated = response.json()
        assert all(
            math.isclose(r["values"]["1" * 32], truth, rel_tol=1e-13, abs_tol=1e-13)
            for r, truth in zip(evaluated["rows"], medians, strict=True)
        )
        assert all(
            math.isclose(r["values"]["2" * 32], 2 * truth, rel_tol=1e-13, abs_tol=1e-13)
            for r, truth in zip(evaluated["rows"], medians, strict=True)
        )
        assert len(evaluated["pivot"]["rows"]) == 2
        assert all(
            abs(c["p_value"] - 0.1) < 1e-12 and abs(c["adjusted_p_value"] - 0.2) < 1e-12
            for c in evaluated["comparisons"]
        )
        response = client.post(f"{table_base}/tables/export/xlsx", json=body)
        response.raise_for_status()
        (root / "artifacts/engine-custom-table.xlsx").write_bytes(response.content)
        with zipfile.ZipFile(io.BytesIO(response.content)) as spreadsheet:
            namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            workbook = ET.fromstring(spreadsheet.read("xl/workbook.xml"))
            assert {s.attrib["name"] for s in workbook.find("s:sheets", namespace)} == {
                "Data",
                "Cell status",
                "Pivot",
                "Comparisons",
                "Provenance",
            }
            data = ET.fromstring(spreadsheet.read("xl/worksheets/sheet1.xml"))
            assert len(data.find("s:sheetData", namespace)) == 7 and not data.findall(
                ".//s:f", namespace
            )
        archive = client.get(f"{table_base}/export/project")
        archive.raise_for_status()
        response = client.post(
            "/api/import/project", files={"file": ("table.cytoforge", archive.content)}
        )
        response.raise_for_status()
        restored = response.json()
        response = client.get(f"/api/workspaces/{restored['id']}/tables/{table['id']}/evaluate")
        response.raise_for_status()
        assert response.json()["rows"] == evaluated["rows"]
        evidence["tables"] = dict(
            rows=6,
            medians=medians,
            hidden_helpers_preserved=True,
            rename_bindings_preserved=True,
            pivots_and_comparisons=True,
            xlsx_numeric_literal_cells_and_provenance=True,
            full_rows_exported_despite_page_limit=True,
            archive_restored=True,
            biological_model_metrics=True,
        )
        print(
            "Packaged tables preserved population mappings, formula bindings, pivots, "
            "comparisons, numeric XLSX, biological statistics and restored archives.",
            flush=True,
        )
        # Independent author-R fixtures must also pass in the frozen worker.
        asp_truth = json.loads((root / "tests/fixtures/autospill/truth.json").read_text())
        asp_evidence = []
        with np.load(root / "tests/fixtures/autospill/controls.npz", allow_pickle=False) as inputs:
            for case, truth in asp_truth["cases"].items():
                response = client.post("/api/workspaces", json={"name": f"Native AutoSpill {case}"})
                response.raise_for_status()
                asp_doc = response.json()
                asp_base = f"/api/workspaces/{asp_doc['id']}"
                channels = ["D1", "D2", "AF"] + (["FSC-A", "SSC-A"] if case == "cleanup" else [])
                for i in range(3):
                    csv_input = io.StringIO()
                    np.savetxt(
                        csv_input,
                        inputs[f"{case}_{i}"],
                        delimiter=",",
                        header=",".join(channels),
                        comments="",
                    )
                    response = client.post(
                        f"{asp_base}/import?revision={asp_doc['revision']}",
                        files={"files": (f"{channels[i]}.csv", csv_input.getvalue(), "text/csv")},
                    )
                    response.raise_for_status()
                    asp_doc = response.json()["workspace"]
                asp_jobs = f"{asp_base}/compensations/autospill/jobs"
                response = client.post(
                    asp_jobs,
                    json={
                        "revision": asp_doc["revision"],
                        "name": f"Native {case}",
                        "detectors": channels[:3],
                        "auto_cleanup": case == "cleanup",
                        "af_detector": "AF" if case.startswith("af") else None,
                        "controls": [
                            dict(name=n, primary_detector=n, sample_id=s["id"])
                            for n, s in zip(channels[:3], asp_doc["samples"], strict=True)
                        ],
                    },
                )
                response.raise_for_status()
                job_id = response.json()["id"]
                wait_deadline = time.monotonic() + 60
                while time.monotonic() < wait_deadline:
                    response = client.get(f"{asp_jobs}/{job_id}")
                    response.raise_for_status()
                    job = response.json()
                    if job["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                        break
                    time.sleep(0.05)
                assert job["status"] == "succeeded", job
                result = job["result"]
                assert result["diagnostics"]["converged"], result
                coefficients = np.asarray(result["compensation"]["matrix"])
                np.testing.assert_allclose(coefficients, truth["matrix"], atol=2e-12, rtol=2e-12)
                response = client.get(f"{asp_jobs}/{job_id}/report")
                response.raise_for_status()
                assert response.json()["compensation"]["matrix"] == result["compensation"]["matrix"]
                response = client.post(
                    f"{asp_jobs}/{job_id}/apply",
                    json={
                        "revision": asp_doc["revision"],
                        "sample_ids": [s["id"] for s in asp_doc["samples"]],
                    },
                )
                response.raise_for_status()
                asp_doc = response.json()
                saved_matrix = asp_doc["compensations"][0]
                assert all(s["compensation_id"] == job_id for s in asp_doc["samples"])
                if case == "cleanup":
                    for i, n in enumerate(channels[:3]):
                        gate = next(
                            g
                            for g in asp_doc["gates"]
                            if g["provenance"].get("control_detector") == n
                        )
                        stats = client.get(
                            f"{asp_base}/samples/{asp_doc['samples'][i]['id']}/counts"
                        )
                        stats.raise_for_status()
                        count = next(r["count"] for r in stats.json() if r["id"] == gate["id"])
                        assert count == len(inputs[f"cleanup_gate_{i}"])
                archive = client.get(f"{asp_base}/export/project")
                archive.raise_for_status()
                response = client.post(
                    "/api/import/project", files={"file": (f"{case}.cytoforge", archive.content)}
                )
                response.raise_for_status()
                restored_asp = response.json()
                response = client.get(
                    f"/api/workspaces/{restored_asp['id']}/compensations/autospill/saved/{job_id}"
                )
                response.raise_for_status()
                restored_result = response.json()
                assert not restored_result["stale"]
                assert restored_result["result"]["compensation"]["matrix"] == saved_matrix["matrix"]
                asp_evidence.append(
                    dict(
                        case=case,
                        iterations=result["diagnostics"]["iterations"],
                        max_coefficient_difference=float(
                            np.max(np.abs(coefficients - truth["matrix"]))
                        ),
                        final_residual=result["diagnostics"]["final_max_error"],
                        report_downloaded=True,
                        cleanup_populations_verified=case == "cleanup",
                        archive_restored=True,
                    )
                )
        evidence["autospill"] = dict(
            reference_commit=asp_truth["commit"],
            native_transform_commit=asp_truth["native_transform"]["commit"],
            cases=asp_evidence,
        )
        print(
            "Packaged AutoSpill matched author R coefficients, reports "
            "and restored matrix provenance.",
            flush=True,
        )
        plate_definition = dict(
            id="a" * 32,
            name="Frozen plate",
            plate_key="P1",
            format=1536,
            assignments={
                "A01": [s["id"] for s in table_doc["samples"][:2]],
                "A02": [table_doc["samples"][2]["id"]],
            },
            columns=table["columns"],
            view=dict(mode="faces"),
        )
        response = client.post(
            f"{table_base}/plates/import/csv",
            json=dict(
                revision=table_doc["revision"],
                plate=plate_definition,
                text=(
                    "Well ID,Treatment,Dose\nA1,treated,8\nA02,vehicle,4\n"
                    "AF48,planned,0\nC1,duplicate,1\nC01,duplicate,2\nA0,invalid,0\n"
                ),
            ),
        )
        response.raise_for_status()
        staged = response.json()
        assert staged["staged_rows"] == 3 and len(staged["issues"]) == 2
        plate_definition = staged["plate"]
        plate_body = dict(revision=table_doc["revision"], plate=plate_definition)
        response = client.post(f"{table_base}/plates/annotations/review", json=plate_body)
        response.raise_for_status()
        review = response.json()
        assert review["sample_count"] == 3 and review["empty_wells"] == ["AF48"]
        response = client.post(
            f"{table_base}/plates/annotations/apply",
            json={**plate_body, "review_hash": review["review_hash"]},
        )
        response.raise_for_status()
        plate_doc = response.json()
        plate_body = dict(revision=plate_doc["revision"], plate=plate_doc["plates"][0])
        response = client.post(f"{table_base}/plates/evaluate", json=plate_body)
        response.raise_for_status()
        plate_result = response.json()
        assert len(plate_result["wells"]) == 1536
        assert plate_result["wells"][0]["values"]["1" * 32] == 3.0
        assert plate_result["wells"][0]["values"]["2" * 32] == 6.0
        assert plate_result["wells"][1]["values"]["1" * 32] == 5.5
        response = client.post(
            f"{table_base}/plates/evaluate",
            json={
                **plate_body,
                "plate": {
                    **plate_body["plate"],
                    "view": {
                        **plate_body["plate"]["view"],
                        "domains": {"1" * 32: [5e-324, 1e-323]},
                    },
                },
            },
        )
        response.raise_for_status()
        bounded_well = response.json()["wells"][0]
        assert bounded_well["values"]["1" * 32] == 3.0
        assert bounded_well["normalized"]["1" * 32] == 1.0
        assert bounded_well["clipped"]["1" * 32]
        response = client.post(f"{table_base}/plates/export/svg", json=plate_body)
        response.raise_for_status()
        ET.fromstring(response.content)
        assert "Head width: Signal" in response.text and "Head height: Double" in response.text
        (root / "artifacts/engine-plate.svg").write_bytes(response.content)
        response = client.post(f"{table_base}/plates/resize", json={**plate_body, "format": 96})
        response.raise_for_status()
        assert response.json()["removed_wells"] == ["AF48"]
        archive = client.get(f"{table_base}/export/project")
        archive.raise_for_status()
        response = client.post(
            "/api/import/project", files={"file": ("plate.cytoforge", archive.content)}
        )
        response.raise_for_status()
        restored = response.json()
        response = client.get(
            f"/api/workspaces/{restored['id']}/plates/{plate_definition['id']}/evaluate"
        )
        response.raise_for_status()
        assert response.json()["wells"] == plate_result["wells"]
        evidence["plates"] = dict(
            format=1536,
            equal_acquisition_weight_median=3.0,
            formula_bindings_preserved=True,
            annotations_applied_atomically=True,
            invalid_duplicate_rows_reported=True,
            empty_well_plans_preserved=True,
            face_legend_exported=True,
            subnormal_display_bounds_preserve_raw_values=True,
            resize_loss_previewed=True,
            archive_restored=True,
        )
        print(
            "Packaged plate measurements, reviewed annotations, SVG legends "
            "and archive restoration passed.",
            flush=True,
        )
        # An analytic time course independently tests the frozen kinetics worker,
        # ordinary generated gates, binary event rows and portable report restore.
        response = client.post("/api/workspaces", json={"name": "Standalone kinetics validation"})
        response.raise_for_status()
        kin_doc = response.json()
        kin_base = f"/api/workspaces/{kin_doc['id']}"
        rows = ["Time,Signal"]
        for i in range(8):
            rows.extend([f"{i + 0.5},{4 * (i + 0.5) + 8}"] * 3)
        response = client.post(
            f"{kin_base}/import?revision=0",
            files={"files": ("time-response.csv", "\n".join(rows), "text/csv")},
        )
        response.raise_for_status()
        kin_doc = response.json()["workspace"]
        kin_sample = kin_doc["samples"][0]["id"]
        response = client.post(
            f"{kin_base}/kinetics/jobs",
            json={
                "revision": kin_doc["revision"],
                "name": "Analytic time response",
                "inputs": [{"sample_id": kin_sample}],
                "channel": "Signal",
                "time_channel": "Time",
                "time_min": 0,
                "time_max": 8,
                "bins": 8,
                "statistic": "mean",
                "threshold": 20,
                "create_responder_gates": True,
            },
        )
        response.raise_for_status()
        kin_id = response.json()["id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = client.get(f"{kin_base}/kinetics/jobs/{kin_id}")
            response.raise_for_status()
            job = response.json()
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.1)
        assert job["status"] == "succeeded", job
        summary = job["result"]["fits"][0]["ranges"][0]
        assert math.isclose(summary["slope"], 4, rel_tol=1e-12)
        assert math.isclose(summary["auc"], 168, rel_tol=1e-12)
        assert summary["peak"] == 38 and summary["peak_time"] == 7.5
        assert summary["responder_count"] == 15 and summary["covered_duration"] == 7
        response = client.post(
            f"{kin_base}/kinetics/jobs/{kin_id}/apply", json={"revision": kin_doc["revision"]}
        )
        response.raise_for_status()
        counts = client.get(f"{kin_base}/samples/{kin_sample}/counts")
        counts.raise_for_status()
        assert sorted(g["count"] for g in counts.json()) == [15, 24, 24]
        events = client.get(f"{kin_base}/kinetics/{kin_id}/events?sample_id={kin_sample}")
        events.raise_for_status()
        array = np.loadtxt(io.StringIO(events.text), delimiter=",", skiprows=1)
        np.testing.assert_array_equal(array[:, 0], np.arange(24))
        np.testing.assert_array_equal(array[:, 1], np.repeat(np.arange(8) + 0.5, 3))
        archive = client.get(f"{kin_base}/export/project")
        archive.raise_for_status()
        response = client.post(
            "/api/import/project", files={"file": ("kinetics.cytoforge", archive.content)}
        )
        response.raise_for_status()
        restored = response.json()
        response = client.get(f"/api/workspaces/{restored['id']}/kinetics/{kin_id}")
        response.raise_for_status()
        assert not response.json()["stale"] and response.json()["fits"][0]["ranges"][0] == summary
        evidence["kinetics"] = dict(
            known_slope=4,
            known_auc=168,
            strict_responders=15,
            original_events=24,
            range_boundaries_verified=True,
            event_ids_verified=True,
            archive_restored=True,
        )
        print(
            "Packaged kinetics worker recovered analytic curves, strict responders "
            "and original event identities.",
            flush=True,
        )
        evidence["status"] = "passed"
        (root / "artifacts/engine-smoke.json").write_text(json.dumps(evidence, indent=2))
        print(
            "Packaged archive retained analyses, fitted event IDs and spectral control outputs.",
            flush=True,
        )
finally:
    engine.terminate()
    try:
        engine.wait(timeout=15)
    except subprocess.TimeoutExpired:
        engine.kill()
        engine.wait(timeout=5)
