"""ISAC's unmodified GatingML fixtures provide independent event-level truth."""

from pathlib import Path

import numpy as np
import pytest
from cytoforge.gatingml import export_gatingml
from cytoforge.interchange import parse_document
from cytoforge.models import Workspace
from cytoforge.science import Engine, parse_fcs, save_events
from cytoforge.store import Store

FIXTURES = Path(__file__).parent / "fixtures/interchange/gatingml"
GML_FILES = sorted(p for p in (FIXTURES / "gml").glob("*.xml") if "attr_testing" not in p.name)


@pytest.mark.parametrize("path", GML_FILES, ids=lambda p: p.stem)
def test_isac_event_membership(path, tmp_path):
    sample, events, comp, _ = parse_fcs(FIXTURES / "data1.fcs", "Data1")
    plan = parse_document(path.read_bytes(), path.name)
    source = plan.sources[0]
    gates = [g.model_copy(update={"sample_id": sample.id}) for g in source.gates]
    doc = Workspace(
        name="ISAC conformance",
        samples=[sample],
        gates=gates,
        compensations=source.matrices + ([comp] if comp else []),
    )
    store = Store(tmp_path)
    try:
        save_events(store.data_path(doc.id, sample.id), events)
        engine = Engine(store)
        compared = 0
        for gate in gates:
            truth = FIXTURES / "truth" / f"Results_{gate.name}.txt"
            if truth.exists():
                expected = np.loadtxt(truth, dtype=bool)
                np.testing.assert_array_equal(
                    engine.mask(doc, sample, gate.id),
                    expected,
                    err_msg=f"{path.name}: {gate.name}",
                )
                compared += 1
        if path.stem == "gml_ellipse2_gate":
            import flowkit

            strategy = flowkit.Session(gating_strategy=str(path))
            reference_sample = flowkit.Sample(str(FIXTURES / "data1.fcs"))
            strategy.add_samples(reference_sample)
            strategy.analyze_samples()
            np.testing.assert_array_equal(
                engine.mask(doc, sample, gates[0].id),
                strategy.get_gate_membership(reference_sample.id, gates[0].name),
            )
        else:
            assert compared > 0
        # Validate export against the XSD and the independently known event masks.
        exported = export_gatingml(doc, sample)
        restored_source = parse_document(exported, "roundtrip.xml").sources[0]
        restored = Workspace(
            name="Restored strategy",
            samples=[sample],
            gates=[g.model_copy(update={"sample_id": sample.id}) for g in restored_source.gates],
            compensations=restored_source.matrices + ([comp] if comp else []),
        )
        save_events(store.data_path(restored.id, sample.id), events)
        for gate in gates:
            copy = next(g for g in restored.gates if g.name == gate.name)
            np.testing.assert_array_equal(
                engine.mask(doc, sample, gate.id), engine.mask(restored, sample, copy.id)
            )
    finally:
        store.close()


def test_invalid_schema_is_rejected():
    path = FIXTURES / "gml/gml_range_gate_attr_testing.xml"
    with pytest.raises(ValueError, match="GatingML"):
        parse_document(path.read_bytes(), path.name)


@pytest.mark.parametrize(
    "payload",
    [
        b'<!DOCTYPE Workspace [<!ENTITY ex SYSTEM "file:///etc/passwd">]><Workspace>&ex;</Workspace>',
        b'<!DOCTYPE Workspace [<!ENTITY ex "a">]><Workspace>&ex;</Workspace>',
        b'<Workspace version="10.0"><SampleList></Workspace>',
    ],
)
def test_unsafe_xml_is_rejected(payload):
    with pytest.raises(ValueError):
        parse_document(payload, "unsafe.xml")
