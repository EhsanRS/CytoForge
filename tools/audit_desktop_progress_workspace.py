"""Read-only check of the retained original desktop workspace and acquisition bytes."""

import argparse
import hashlib
import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from cytoforge.models import Workspace

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reference_path, output = (ROOT / args.reference).resolve(), (ROOT / args.output).resolve()
    assert reference_path.is_relative_to(ROOT) and output.is_relative_to(ROOT)
    reference = json.loads(reference_path.read_text())
    data = ROOT / ".tmp/desktop-progress/data"
    with sqlite3.connect(f"file:{data / 'workspaces.sqlite'}?mode=ro", uri=True) as database:
        row = database.execute(
            "SELECT s.document, w.revision, w.updated_at FROM workspaces w "
            "JOIN snapshots s ON s.workspace_id=w.id AND s.seq=w.cursor WHERE w.id=?",
            (reference["workspace_id"],),
        ).fetchone()
    assert row is not None
    workspace = Workspace.model_validate_json(row[0])
    workspace.revision, workspace.updated_at = row[1:]
    canonical = subprocess.run(
        [
            "node",
            "-e",
            "const fs=require('node:fs'),crypto=require('node:crypto');"
            "process.stdout.write(crypto.createHash('sha256').update(JSON.stringify("
            "JSON.parse(fs.readFileSync(0,'utf8')))).digest('hex'));",
        ],
        input=workspace.model_dump_json(),
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    acquired = []
    for sample in workspace.samples:
        path = data / "events" / workspace.id / f"{sample.id}.npy"
        with path.open("rb") as handle:
            checksum = hashlib.file_digest(handle, "sha256").hexdigest()
        acquired.append(
            dict(
                sample_id=sample.id, sha256=checksum, matches_acquisition=checksum == sample.sha256
            )
        )
    record = dict(
        checked_at=datetime.now(UTC).isoformat(),
        workspace_id=workspace.id,
        revision=workspace.revision,
        samples=len(workspace.samples),
        saved_comparisons=len(workspace.comparison_results),
        workspace_json_sha256=canonical,
        expected_sha256=reference["workspace_json_sha256"],
        serialization="Native JSON.stringify of validated model with current revision/timestamp",
        scope="Read-only SQLite URI and event-file digests; no Store or profile writes",
        unchanged=canonical == reference["workspace_json_sha256"],
        acquired_event_files_checked=acquired,
    )
    output.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({key: record[key] for key in ["revision", "samples", "unchanged"]}))
    assert all(row["matches_acquisition"] for row in acquired)


if __name__ == "__main__":
    main()
