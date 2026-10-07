"""Independent PDF parser verifies native vectors and attached graph denominators."""

import argparse
import hashlib
import json
from pathlib import Path

import pymupdf

parser = argparse.ArgumentParser()
parser.add_argument("--evidence", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
evidence = json.loads(Path(args.evidence).read_text())
assert evidence["status"] == "passed"
path = Path(evidence["pdf"]["path"])
assert hashlib.sha256(path.read_bytes()).hexdigest() == evidence["pdf"]["sha256"]
pdf = pymupdf.open(path)
assert len(pdf) == len(evidence["modes"])
attachments = pdf.embfile_names()
assert len(attachments) == 1
manifest = json.loads(pdf.embfile_get(attachments[0]))
assert manifest == evidence["manifest"]
pages = []
for index, page in enumerate(pdf):
    assert abs(page.rect.width - 210 * 72 / 25.4) < 0.8
    assert abs(page.rect.height - 297 * 72 / 25.4) < 0.8
    assert not page.get_images(full=True), "Scientific graph rasterized in native PDF"
    paths = page.get_drawings()
    assert len(paths) > 10, "Native graph vectors missing"
    panel = manifest["pages"][index]["elements"][0]
    assert panel["mode"] == evidence["modes"][index]
    layer = panel["layers"][0]
    assert layer["population_count"] == 6 and layer["finite_count"] == 5
    assert layer["sample_sha256"] == evidence["source_sha256"]
    assert layer["graph_options"]["palette"] == "viridis"
    if index == 0:
        values = [0, 0, 1, 2, 3]
        exact_counts = [sum(value <= edge for value in values) for edge in layer["edges"]]
        assert layer["cdf_denominator"] == 5 and layer["cdf_counts"] == exact_counts
        assert layer["displayed_values"] == [count * 20 for count in exact_counts]
        assert layer["visible_count"] == 4 and layer["outside_view"] == 1
    if index in {1, 2}:
        assert layer["probability_denominator"] == layer["density_count"] == 5
        assert layer["contour_vertices"] > 0 and not layer["contours_truncated"]
        assert all(
            item["estimated_probability"] + 1e-12 >= item["probability"]
            for item in layer["probability_levels"]
        )
    if panel["mode"] == "3d":
        assert panel["bounds"] == [0, 3, 0, 3, 0, 3]
        assert layer["visible_count"] == layer["displayed_count"] == 5
        assert layer["sampling"] is None
        import struct

        assert (
            layer["event_ids_sha256"]
            == hashlib.sha256(struct.pack("<5Q", 0, 1, 2, 3, 4)).hexdigest()
        )
        view = layer["three_d"]
        assert view["z"] == "Z" and view["color_by"] == "C" and view["size_by"] == "S"
        assert view["yaw"] == 0.7 and view["pitch"] == -0.4 and view["pan"] == [0.1, -0.2]
        assert layer["color_bounds"] == [0, 30] and layer["size_bounds"] == [0, 300]
        assert set(layer["source"]["parameters"]) == {"X", "Y", "Z", "C", "S"}
    pages.append(dict(mode=panel["mode"], vector_paths=len(paths), raster_images=0))
result = dict(
    status="passed",
    pdf=str(path),
    sha256=evidence["pdf"]["sha256"],
    pages=pages,
    attached_manifest=True,
    cdf_counts_independently_checked=True,
    three_d_camera_and_original_event_ids_checked="3d" in evidence["modes"],
)
Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
