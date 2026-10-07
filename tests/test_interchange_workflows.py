from pathlib import Path

import numpy as np
import pytest
from cytoforge.analysis import gate_signature, input_hash, transform_signature
from cytoforge.interchange import (
    parse_document,
)
from cytoforge.models import (
    AnalysisInput,
    AnalysisRequest,
    Compensation,
    Gate,
    GateDimension,
    Transform,
)

FIXTURES = Path(__file__).parent / "fixtures/interchange"


def import_reference(client, format="gatingml"):
    doc = client.post("/api/workspaces", json={"name": "Interchange"}).json()
    name = "data1.fcs" if format == "gatingml" else "data_set_simple_line_100.fcs"
    response = client.post(
        f"/api/workspaces/{doc['id']}/import?revision=0",
        files={"files": (name, (FIXTURES / format / name).read_bytes())},
    )
    assert response.status_code == 200, response.text
    return response.json()["workspace"]


def preview(client, doc, filename, format="gatingml"):
    path = FIXTURES / format / (f"gml/{filename}" if format == "gatingml" else filename)
    response = client.post(
        f"/api/workspaces/{doc['id']}/interchange/preview?revision={doc['revision']}",
        files={"file": (filename, path.read_bytes())},
    )
    assert response.status_code == 200, response.text
    return response.json(), path.read_bytes()


def apply(client, doc, plan, **options):
    return client.post(
        f"/api/workspaces/{doc['id']}/interchange/apply",
        json={
            "preview_id": plan["preview_id"],
            "revision": plan["revision"],
            "mappings": [
                {
                    "source_id": plan["document"]["sources"][0]["id"],
                    "sample_ids": [doc["samples"][0]["id"]],
                }
            ],
            **options,
        },
    )


def test_import_report_source_archive_and_undo(client):
    import io
    import zipfile

    doc = import_reference(client)
    base = f"/api/workspaces/{doc['id']}"
    history = client.get(f"{base}/history").json()
    plan, source = preview(client, doc, "gml_all_gates.xml")
    assert client.get(base).json()["revision"] == doc["revision"]
    assert client.get(f"{base}/history").json() == history
    assert plan["document"]["sources"][0]["compatible_sample_ids"] == [doc["samples"][0]["id"]]
    response = apply(client, doc, plan)
    assert response.status_code == 200, response.text
    doc = response.json()
    record = doc["interchanges"][0]
    assert len(record["gate_ids"]) == len(doc["gates"])
    assert client.get(f"{base}/interchange/{record['id']}/source").content == source
    assert (
        client.get(f"{base}/interchange/{record['id']}/report").json()["sha256"] == record["sha256"]
    )
    sid = doc["samples"][0]["id"]
    exported = client.get(f"{base}/samples/{sid}/export/gatingml")
    assert exported.status_code == 200, exported.text
    parse_document(exported.content, "export.xml")
    # Ratio coordinates are plotted directly with their original matrix and scale.
    ratio = next(g for g in doc["gates"] if any(d["ratio_channels"] for d in g["dimensions"]))
    dim = ratio["dimensions"][0]
    plot = client.get(
        f"{base}/samples/{sid}/plot",
        params={
            "x": dim["channel"],
            "coordinate_gate_id": ratio["id"],
            "mode": "histogram",
        },
    )
    assert plot.status_code == 200, plot.text
    assert plot.json()["finite_count"] > 0
    assert any(o["id"] == ratio["id"] for o in plot.json()["overlays"])
    archive = client.get(f"{base}/export/project")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as saved:
        assert saved.read(f"interchanges/{record['id']}.xml") == source
    restored = client.post(
        "/api/import/project", files={"file": ("study.cytoforge", archive.content)}
    )
    assert restored.status_code == 200, restored.text
    restored_doc = restored.json()
    assert (
        client.get(
            f"/api/workspaces/{restored_doc['id']}/interchange/{record['id']}/source"
        ).content
        == source
    )
    undone = client.post(f"{base}/undo", json={"revision": doc["revision"]}).json()
    assert undone["interchanges"] == [] and undone["gates"] == []
    redone = client.post(f"{base}/redo", json={"revision": undone["revision"]}).json()
    assert redone["interchanges"] == doc["interchanges"]


def test_stale_preview_is_atomic(client):
    doc = import_reference(client)
    base = f"/api/workspaces/{doc['id']}"
    plan, _ = preview(client, doc, "gml_range_gate.xml")
    changed = client.patch(base, json={"revision": doc["revision"], "name": "Renamed"}).json()
    assert apply(client, doc, plan).status_code == 409
    assert client.get(base).json() == changed


def test_flowjo_review_counts_and_matching(client):
    doc = import_reference(client, "flowjo")
    plan, source = preview(client, doc, "single_ellipse_51_events.wsp", "flowjo")
    assert plan["document"]["sources"][0]["suggested_sample_ids"] == [doc["samples"][0]["id"]]
    assert plan["requires_acknowledgement"]
    rejected = apply(client, doc, plan)
    assert rejected.status_code == 422
    assert client.get(f"/api/workspaces/{doc['id']}").json()["gates"] == []
    accepted = apply(client, doc, plan, allow_partial=True)
    assert accepted.status_code == 200, accepted.text
    doc = accepted.json()
    row = doc["interchanges"][0]["report"]["counts"][0]
    assert row["count"] == row["reported_count"] == 51
    xml = client.get(
        f"/api/workspaces/{doc['id']}/interchange/{doc['interchanges'][0]['id']}/source"
    )
    assert xml.content == source


def test_fixed_gate_matrix_is_independent_and_fingerprinted(dataset):
    doc, sample, _, engine = dataset
    matrix = Compensation(name="Fixed", detectors=["X", "Y"], matrix=[[1, 0.5], [0, 1]])
    doc.compensations.append(matrix)
    gate = Gate(
        sample_id=sample.id,
        name="Fixed basis",
        kind="hyperrectangle",
        dimensions=[
            GateDimension(channel="Y", compensation_ref=matrix.id, minimum=0.2, maximum=2),
        ],
    )
    doc.gates.append(gate)
    before = engine.mask(doc, sample, gate.id).copy()
    sample.compensation_id = matrix.id
    doc.revision += 1
    np.testing.assert_array_equal(before, engine.mask(doc, sample, gate.id))
    sample.compensation_id = None
    request = AnalysisRequest(
        revision=doc.revision,
        name="Fingerprint",
        algorithm="pca",
        channels=["X", "Y"],
        inputs=[AnalysisInput(sample_id=sample.id, gate_id=gate.id)],
    )
    fingerprint = input_hash(doc, request)
    matrix.matrix[0][1] = 0.2
    assert input_hash(doc, request) != fingerprint


def test_historical_fingerprint_defaults_stay_stable(dataset):
    doc, sample, _, _ = dataset
    spec = Transform(kind="asinh")
    assert transform_signature(spec) == {
        "kind": "asinh",
        "cofactor": 150,
        "t": 262144,
        "w": 0.5,
        "m": 4.5,
        "a": 0,
    }
    gate = Gate(sample_id=sample.id, name="Old gate", kind="range", x="X", bounds=[0, 2])
    old_keys = {
        "id",
        "sample_id",
        "parent_id",
        "kind",
        "x",
        "y",
        "x_transform",
        "y_transform",
        "bounds",
        "vertices",
        "center",
        "radii",
        "angle",
        "quadrant",
        "operation",
        "operands",
    }
    assert set(gate_signature(gate)) == old_keys


def test_incompatible_target_rolls_back(client):
    doc = client.post("/api/demo").json()
    plan, _ = preview(client, doc, "gml_all_gates.xml")
    before = client.get(f"/api/workspaces/{doc['id']}").json()
    response = apply(client, doc, plan)
    assert response.status_code == 422
    assert client.get(f"/api/workspaces/{doc['id']}").json() == before


def test_bounded_scale_and_ratio_roundtrip(dataset, store):
    from cytoforge.gatingml import export_gatingml
    from cytoforge.models import Workspace
    from cytoforge.science import save_events, transform

    doc, sample, events, engine = dataset
    clipped = Transform(kind="gml_linear", t=1, bound_min=0, bound_max=1)
    np.testing.assert_allclose(
        transform(events[:, 0], clipped), [0, 0, 1, 1, 1, 0, 1, np.nan], equal_nan=True
    )
    doc.gates = [
        Gate(
            name="Clamped scale",
            sample_id=sample.id,
            kind="hyperrectangle",
            dimensions=[
                GateDimension(channel="X", transform=clipped, minimum=0, maximum=1),
            ],
        ),
        Gate(
            name="Bounded ratio",
            sample_id=sample.id,
            kind="hyperrectangle",
            dimensions=[
                GateDimension(
                    channel="Ratio",
                    ratio_channels=("X", "Y"),
                    ratio_bound_min=0.2,
                    ratio_bound_max=0.9,
                    minimum=0.2,
                    maximum=1,
                ),
            ],
        ),
    ]
    expected = [
        [True, True, False, False, False, True, False, False],
        [True, False, True, True, True, True, True, False],
    ]
    for gate, mask in zip(doc.gates, expected, strict=True):
        np.testing.assert_array_equal(engine.mask(doc, sample, gate.id), mask)
    source = parse_document(export_gatingml(doc, sample), "bounds.xml").sources[0]
    restored = Workspace(
        name="Bounded",
        samples=[sample],
        gates=[g.model_copy(update={"sample_id": sample.id}) for g in source.gates],
        compensations=source.matrices,
    )
    save_events(store.data_path(restored.id, sample.id), events)
    for gate, mask in zip(restored.gates, expected, strict=True):
        np.testing.assert_array_equal(engine.mask(restored, sample, gate.id), mask)


def test_native_boolean_xor_complement_and_container_roundtrip(dataset, store):
    from cytoforge.gatingml import export_gatingml
    from cytoforge.models import Workspace
    from cytoforge.science import save_events

    doc, sample, events, engine = dataset
    a = Gate(name="A", sample_id=sample.id, kind="range", x="X", bounds=[0, 3])
    b = Gate(name="B", sample_id=sample.id, kind="range", x="Y", bounds=[0, 3])
    c = Gate(name="Parent container", sample_id=sample.id, kind="container")
    doc.gates = [
        a,
        b,
        c,
        Gate(
            name="XOR",
            sample_id=sample.id,
            kind="boolean",
            operation="xor",
            operands=[a.id, b.id],
            parent_id=c.id,
        ),
        Gate(
            name="Outside", sample_id=sample.id, kind="range", x="X", bounds=[0, 3], complement=True
        ),
        Gate(
            name="Complemented operand",
            sample_id=sample.id,
            kind="boolean",
            operation="and",
            operands=[a.id, b.id],
            operand_complements=[False, True],
        ),
    ]
    source = parse_document(export_gatingml(doc, sample), "boolean.xml").sources[0]
    restored = Workspace(
        name="Boolean",
        samples=[sample],
        gates=[g.model_copy(update={"sample_id": sample.id}) for g in source.gates],
        compensations=source.matrices,
    )
    save_events(store.data_path(restored.id, sample.id), events)
    for gate in doc.gates:
        copy = next(g for g in restored.gates if g.provenance["external_id"] == f"gate_{gate.id}")
        np.testing.assert_array_equal(
            engine.mask(doc, sample, gate.id), engine.mask(restored, sample, copy.id)
        )


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("test_data_diamond_biex_rect.wsp", 50605),
        ("test_data_diamond_asinh_rect.wsp", 50559),
        ("test_data_diamond_asinh_rect2.wsp", 50699),
    ],
)
def test_flowjo_transformed_diamond_counts(filename, expected, store):
    from cytoforge.interchange import ImportApply, ImportMapping, apply_document
    from cytoforge.models import Workspace, new_id
    from cytoforge.science import Engine, parse_fcs, save_events

    root = FIXTURES / "flowjo"
    sample, values, matrix, _ = parse_fcs(root / "test_data_diamond_01.fcs", "Diamond")
    doc = Workspace(
        name="FlowJo transformed", samples=[sample], compensations=[matrix] if matrix else []
    )
    save_events(store.data_path(doc.id, sample.id), values)
    plan = parse_document((root / filename).read_bytes(), filename)
    record = apply_document(
        doc,
        plan,
        ImportApply(
            revision=0,
            preview_id=new_id(),
            allow_partial=True,
            mappings=[ImportMapping(source_id=plan.sources[0].id, sample_ids=[sample.id])],
        ),
        Engine(store),
    )
    row = next(r for r in record.report["counts"] if r["name"] == "upper_right")
    assert row["count"] == expected


@pytest.mark.parametrize(
    "filename", ["8_color_ICS_with_ellipse.wsp", "8_color_ICS_boolean_gate_testing.wsp"]
)
def test_real_panel_flowkit_event_membership(filename, store):
    import warnings

    import flowkit
    from cytoforge.interchange import ImportApply, ImportMapping, apply_document
    from cytoforge.models import Workspace, new_id
    from cytoforge.science import Engine, parse_fcs, save_events

    root = FIXTURES / "flowjo"
    plan = parse_document((root / filename).read_bytes(), filename)
    source = plan.sources[0]
    sample, values, matrix, _ = parse_fcs(root / source.file_name, source.file_name)
    doc = Workspace(
        name="Real eight color panel", samples=[sample], compensations=[matrix] if matrix else []
    )
    save_events(store.data_path(doc.id, sample.id), values)
    engine = Engine(store)
    record = apply_document(
        doc,
        plan,
        ImportApply(
            revision=0,
            preview_id=new_id(),
            allow_partial=True,
            mappings=[ImportMapping(source_id=source.id, sample_ids=[sample.id])],
        ),
        engine,
    )
    with warnings.catch_warnings():
        # The XML references three samples; this test intentionally maps only one.
        warnings.simplefilter("ignore", UserWarning)
        oracle = flowkit.Workspace(str(root / filename), fcs_samples=str(root / source.file_name))
    oracle.analyze_samples(sample_id=source.file_name)
    for gate in doc.gates:
        reference = oracle.get_gate_membership(
            source.file_name, gate.name, gate_path=("root", *gate.provenance["external_path"][:-1])
        )
        checked = doc.model_copy(update={"revision": doc.revision + 1})
        np.testing.assert_array_equal(
            engine.mask(checked, sample, gate.id),
            reference,
            err_msg="/".join(gate.provenance["external_path"]),
        )
    # Stored FlowJo counts differ from both implementations. Keep this visible.
    assert any(i["code"] == "gate-count-mismatch" for i in record.report["issues"])


def test_failed_import_does_not_poison_next_revision_cache(client, monkeypatch):
    doc = client.post("/api/workspaces", json={"name": "Rollback cache"}).json()
    base = f"/api/workspaces/{doc['id']}"
    response = client.post(
        f"{base}/import?revision=0",
        files={
            "files": ("control.csv", b"X,Y\n0,0\n1,1\n2,2\n3,0\n0,3\n"),
        },
    )
    doc = response.json()["workspace"]
    sid = doc["samples"][0]["id"]
    gate = Gate(sample_id=sid, name="Existing", kind="range", x="Y", bounds=[0.2, 1.5])
    doc = client.post(
        f"{base}/gates", json={"revision": doc["revision"], "gate": gate.model_dump()}
    ).json()
    xml = b"""<Workspace flowJoVersion="10.0"
      xmlns:g="http://www.isac-net.org/std/Gating-ML/v2.0/gating"
      xmlns:t="http://www.isac-net.org/std/Gating-ML/v2.0/transformations"
      xmlns:d="http://www.isac-net.org/std/Gating-ML/v2.0/datatypes">
      <SampleList><Sample><DataSet sampleID="1" uri="control.csv"/>
        <t:spilloverMatrix t:id="fixed" name="Imported fixed" prefix="Comp-">
          <t:parameters><d:parameter d:name="X"/><d:parameter d:name="Y"/></t:parameters>
          <t:spillover><t:coefficient t:value="1"/><t:coefficient t:value="0.5"/></t:spillover>
          <t:spillover><t:coefficient t:value="0"/><t:coefficient t:value="1"/></t:spillover>
        </t:spilloverMatrix>
        <SampleNode sampleID="1" name="control.csv" count="5"><Subpopulations>
          <Population name="Imported"><Gate><g:RectangleGate>
            <g:dimension g:min="-10" g:max="10"><d:fcs-dimension d:name="Comp-Y"/></g:dimension>
          </g:RectangleGate></Gate></Population>
        </Subpopulations></SampleNode>
      </Sample></SampleList></Workspace>"""
    response = client.post(
        f"{base}/interchange/preview?revision={doc['revision']}", files={"file": ("fixed.wsp", xml)}
    )
    assert response.status_code == 200, response.text
    plan = response.json()

    # The candidate uses a different compensation. Fail after evaluating its counts.
    def fail_xml_write(*args):
        raise ValueError("Simulated original XML persistence failure")

    monkeypatch.setattr(client.app.state.store, "interchange_path", fail_xml_write)
    assert apply(client, doc, plan).status_code == 422
    assert client.get(base).json()["revision"] == doc["revision"]
    renamed = client.patch(
        base, json={"revision": doc["revision"], "name": "After rollback"}
    ).json()
    assert renamed["revision"] == doc["revision"] + 1
    counts = client.get(f"{base}/samples/{sid}/counts").json()
    assert next(c for c in counts if c["id"] == gate.id)["count"] == 1
