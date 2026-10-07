"""Create small, explicitly synthetic packed acquisitions for native desktop validation."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np
from import_fixture import fcs_dataset

ROOT = Path(__file__).resolve().parents[1]


def main():
    directory = ROOT / "artifacts/fcs-packed-fixture"
    directory.mkdir(parents=True, exist_ok=True)
    values = [[row % 8, row * 17, row] for row in range(31)]
    expected = np.array([[row % 8, row * 8.5, row * 0.25] for row in range(31)], dtype="<f8")
    valid = directory / "packed-known-synthetic.fcs"
    valid.write_bytes(
        fcs_dataset(
            values,
            ["X", "Y", "Time"],
            data_type="I",
            widths=[3, 10, 5],
            metadata={
                "P2G": "2",
                "TIMESTEP": "0.25",
                "COM": "CytoForge synthetic validation fixture",
            },
        )
    )
    invalid = directory / "unverified-big-endian-synthetic.fcs"
    invalid.write_bytes(
        fcs_dataset([[1]], ["X"], data_type="I", widths=[3], order=">", raw_data=b"\x01")
    )
    output = io.BytesIO()
    np.lib.format.write_array_header_1_0(
        output, {"descr": "<f8", "fortran_order": False, "shape": expected.shape}
    )
    output.write(expected.tobytes())
    truth = dict(
        synthetic=True,
        events=31,
        parameter_names=["X", "Y", "Time"],
        parameter_widths=[3, 10, 5],
        acquired_event_rows=expected.tolist(),
        expected_numpy_sha256=hashlib.sha256(output.getvalue()).hexdigest(),
        gate=dict(
            name="Known packed range",
            channel="X",
            bounds=[2, 5],
            count=12,
            event_ids=[2, 3, 4, 10, 11, 12, 18, 19, 20, 26, 27, 28],
        ),
        files={
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in [valid, invalid]
        },
        expected_failure="verified instrument layout",
    )
    (directory / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    print(f"Prepared synthetic native fixtures: {directory.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
