"""Independent FlowIO verification of files exported by the real desktop workflow."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import flowio
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    report = json.loads(args.evidence.read_text())
    assert report["status"] == "passed"
    exported = Path(report["fcs_export"]["path"])
    digest = hashlib.file_digest(exported.open("rb"), "sha256").hexdigest()
    assert digest == report["fcs_export"]["sha256"]
    data = flowio.FlowData(str(exported))
    expected = np.array([[10, 20, 1], [30, 40, 2]], dtype=float)
    np.testing.assert_allclose(data.as_array(), expected, atol=1e-6, rtol=1e-6)
    assert data.pnn_labels == ["X", "Y", "Time"]
    assert data.event_count == 2
    result = {
        "status": "passed",
        "verified_at": datetime.now(UTC).isoformat(),
        "desktop_evidence": str(args.evidence),
        "packaged": report["packaged"],
        "fcs_sha256": digest,
        "channels": data.pnn_labels,
        "event_count": data.event_count,
        "expected_compensated_preprocessed_events": expected.tolist(),
        "actual_export_events": data.as_array().tolist(),
    }
    output = args.evidence.with_name(args.evidence.stem + "-export-validation.json")
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
