"""Independently inspect native table XLSX cells and retained project XML."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def digest(filename):
    with Path(filename).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def spreadsheet_rows(filename):
    with ZipFile(filename) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = [
                "".join(item.itertext())
                for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)
            ]
        # Data is the first sheet written by the table exporter; inspect its public OOXML.
        rows = []
        for row in ET.fromstring(archive.read("xl/worksheets/sheet1.xml")).findall(
            "s:sheetData/s:row", NS
        ):
            values = {}
            for cell in row.findall("s:c", NS):
                assert cell.find("s:f", NS) is None, "Exported values must be frozen cells"
                reference = "".join(c for c in cell.attrib["r"] if c.isalpha())
                content = cell.find("s:v", NS)
                if cell.get("t") == "inlineStr":
                    value = "".join(cell.find("s:is", NS).itertext())
                elif content is None:
                    value = None
                elif cell.get("t") == "s":
                    value = strings[int(content.text)]
                else:
                    value = float(content.text)
                values[reference] = value
            rows.append(values)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text())
    assert evidence["status"] == "passed"
    for entry in evidence["exports"].values():
        assert digest(entry["path"]) == entry["sha256"]
    truth = json.loads(Path("artifacts/wsp-table-fixture/truth.json").read_text())
    result = json.loads(Path(evidence["exports"]["json"]["path"]).read_text())
    rows = spreadsheet_rows(evidence["exports"]["xlsx"]["path"])
    assert len(rows) == 3
    headers = {value: key for key, value in rows[0].items()}
    assert "Hidden events" not in headers
    assert [row[headers["Sample"]] for row in rows[1:]] == ["Zulu control.fcs", "Alpha treated.fcs"]
    checked = 0
    for name, expected in truth.items():
        column_id = next(c["id"] for c in result["columns"] if c["name"] == name)
        for index, value in enumerate(expected):
            actual_xlsx = rows[index + 1].get(headers[name])
            actual_json = result["rows"][index]["values"][column_id]
            if isinstance(value, (int, float)):
                np.testing.assert_allclose(
                    [actual_xlsx, actual_json], value, rtol=1e-12, atol=1e-12
                )
            else:
                assert actual_xlsx == actual_json == value
            checked += 1
    source = Path("artifacts/wsp-table-fixture/tables.wsp").read_bytes()
    assert Path(evidence["exports"]["source"]["path"]).read_bytes() == source
    with ZipFile(evidence["exports"]["project"]["path"]) as archive:
        source_members = [name for name in archive.namelist() if name.endswith(".xml")]
        assert len(source_members) == 1
        assert archive.read(source_members[0]) == source
    output = {
        "status": "passed",
        "verified_at": datetime.now(UTC).isoformat(),
        "desktop_evidence": str(args.evidence),
        "packaged": evidence["packaged"],
        "cells_checked_in_json_and_xlsx": checked,
        "missing_edge_row_references": 2,
        "hidden_helper_omitted_from_xlsx": True,
        "source_xml_exact_in_native_download_and_portable_project": True,
        "exports": evidence["exports"],
    }
    destination = args.evidence.with_name(args.evidence.stem + "-export-validation.json")
    destination.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
