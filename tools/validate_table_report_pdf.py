"""Inspect actual printed table text and physical pages (uv run --with pymupdf)."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pymupdf

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--evidence", default="artifacts/desktop-table-report-smoke.json")
parser.add_argument("--output", default="artifacts/table-report-pdf-validation.json")
args = parser.parse_args()
proof = json.loads(Path(args.evidence).read_text())
assert proof["status"] == "passed"
filename = Path(proof["pdf"]["path"])
with filename.open("rb") as handle:
    digest = hashlib.file_digest(handle, "sha256").hexdigest()
assert digest == proof["pdf"]["sha256"]
doc = pymupdf.open(filename)
assert len(doc) == 16
checks = []
raw_samples = set()
for index, page in enumerate(doc):
    text = page.get_text()
    assert "Continued full-cohort analysis" in text and "Full cohort truth" in text
    assert f"page {index + 1}/16" in text
    if index < 8:
        assert "Sample" in text and "Population" in text and "All events" in text
        for i in range(1, 9):
            if f"Acquisition {i}.csv" in text:
                raw_samples.add(i)
    elif index < 14:
        assert "Donor" in text and "Treatment=" in text and "(n=1)" in text
    else:
        assert "Measure" in text and "Signal" in text and "holm" in text
    expected = [215.9, 279.4] if 8 <= index < 14 else [210, 297]
    assert abs(page.rect.width - expected[0] * 72 / 25.4) < 0.8
    assert abs(page.rect.height - expected[1] * 72 / 25.4) < 0.8
    checks.append(dict(page=index + 1, text_characters=len(text), printed_headers=True))
    if index in {0, 8, 15}:
        page.get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3)).save(
            f"artifacts/screenshots/continued-report-page-{index + 1}.png"
        )
assert raw_samples == set(range(1, 9))
assert "Adjusted p" in doc[15].get_text() and "0.0093" in doc[15].get_text()
report = dict(
    status="passed",
    validated_at=datetime.now(UTC).isoformat(),
    pdf_sha256=digest,
    native_evidence=args.evidence,
    pymupdf_version=pymupdf.version[0],
    actual_printed_pages=len(doc),
    all_acquisitions_printed=True,
    pivot_headers_and_finite_counts_printed=True,
    corrected_comparison_printed=True,
    physical_sizes_checked=True,
    pages=checks,
)
Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({k: v for k, v in report.items() if k != "pages"}, indent=2))
