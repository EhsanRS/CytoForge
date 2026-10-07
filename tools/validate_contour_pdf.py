"""Independently inspect native PDF geometry, raster output and attached provenance."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pymupdf

parser = argparse.ArgumentParser()
parser.add_argument("--evidence", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
evidence = json.loads(Path(args.evidence).read_text())
assert evidence["status"] == "passed"
source = Path(evidence["pdf"]["path"])
assert hashlib.sha256(source.read_bytes()).hexdigest() == evidence["pdf"]["sha256"]
pdf = pymupdf.open(source)
assert len(pdf) == evidence["pdf"]["pages"] == 4
assert len(pdf.embfile_names()) == 1
manifest = json.loads(pdf.embfile_get(pdf.embfile_names()[0]))
assert manifest == evidence["manifest"]
pages = []
for index, page in enumerate(pdf):
    assert abs(page.rect.width - 210 * 72 / 25.4) < 0.8
    assert abs(page.rect.height - 297 * 72 / 25.4) < 0.8
    assert not page.get_images(full=True)
    layer = manifest["pages"][index]["elements"][0]["layers"][0]
    assert layer["probability_denominator"] == evidence["expected_counts"][index]
    assert layer["contour_geometry_levels"] and layer["contour_geometry"]
    paths = [
        drawing
        for drawing in page.get_drawings()
        if drawing["type"] == "s"
        and drawing["color"] is not None
        and np.allclose(drawing["color"], np.array([8, 126, 139]) / 255, atol=0.005)
    ]
    assert paths and all(p["rect"].width > 0.1 and p["rect"].height > 0.1 for p in paths)
    if index in {0, 1}:
        assert len(paths) == 25, "Sparse probability boundaries missing from native PDF"
        for drawing in paths:
            assert drawing["rect"].width > 1 and drawing["rect"].height > 1
    if index == 2:
        assert len(paths) == 2, "The eight-cell region requires outer and hole boundaries"
        outer, hole = sorted(paths, key=lambda p: p["rect"].width, reverse=True)
        assert abs(outer["rect"].width / hole["rect"].width - 3) < 0.01
        assert abs(outer["rect"].height / hole["rect"].height - 3) < 0.01
        assert outer["rect"].contains(hole["rect"])
    if index == 3:
        assert {c["geometry"] for c in layer["contour_geometry_levels"]} == {
            "bin_cells",
            "interpolated_centers",
        }
    # Check the independently rasterized publication too, not only its path count.
    raster = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), colorspace=pymupdf.csRGB)
    pixels = np.frombuffer(raster.samples, dtype=np.uint8).reshape(raster.height, raster.width, 3)
    colored = (pixels[:, :, 0] < 100) & (pixels[:, :, 1] > 90) & (pixels[:, :, 2] > 100)
    assert int(colored.sum()) > 100
    pages.append(
        dict(
            index=index,
            mode=manifest["pages"][index]["elements"][0]["mode"],
            probability_boundary_paths=len(paths),
            minimum_boundary_width=min(p["rect"].width for p in paths),
            minimum_boundary_height=min(p["rect"].height for p in paths),
            colored_publication_pixels=int(colored.sum()),
            raster_images=0,
        )
    )
result = dict(
    status="passed",
    pdf=str(source),
    sha256=evidence["pdf"]["sha256"],
    pages=pages,
    attached_provenance_checked=True,
    sparse_boundaries_visible=True,
    hole_geometry_independently_checked=True,
    smoothed_peak_geometry_checked=True,
    scope="Independent PyMuPDF vector inspection and rasterization of the actual native export",
)
Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
