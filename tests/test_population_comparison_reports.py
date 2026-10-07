"""Publication layouts preserve compared populations, controls, history and source bytes."""

from xml.etree import ElementTree as ET

import numpy as np
import pytest
from cytoforge import population_comparison as comparisons
from cytoforge import reports
from cytoforge.models import (
    AnalysisInput,
    ComparisonPresentation,
    Gate,
    LayoutDefinition,
    ReportBatch,
    ReportElement,
    new_id,
)
from test_population_comparison import fixture
from test_population_comparison_tables import commit_fixture


@pytest.fixture
def comparison_report(store):
    values = [
        np.repeat([0, 1, 2], [4, 4, 2]),
        np.repeat([0, 1, 2, 3], [2, 3, 1, 4]),
        np.ones(10),
        np.ones(10),
    ]
    doc, request, engine = fixture(store, [np.c_[v, np.arange(10)] for v in values])
    gates = [
        Gate(sample_id=s.id, name="Positive", kind="range", x="X", bounds=[0, 2])
        for s in doc.samples[1:]
    ]
    doc.gates.extend(gates)
    request.inputs = [AnalysisInput(sample_id=g.sample_id, gate_id=g.id) for g in gates[:2]]
    # Match the real workflow: saved gates and a validated job request precede calculation.
    doc = commit_fixture(store, doc)
    request = type(request).model_validate(request.model_dump())
    request.revision = doc.revision
    result = comparisons.calculate(doc, request, engine, new_id())
    doc.comparison_results.append(result)
    doc = commit_fixture(store, doc)
    element = ReportElement(
        kind="population_comparison",
        result_id=result.id,
        sample_id=gates[0].sample_id,
        gate_id=gates[0].id,
        comparison_parameter_id=request.parameters[0].id,
        comparison_view=ComparisonPresentation(
            control_color="#ff0000", target_color="#123abc", show_individuals=True
        ),
        iterate=False,
        follow_replacement=False,
        title="{{sample}} · {{population}}",
        width_mm=160,
        height_mm=110,
    )
    layout = LayoutDefinition(name="Saved population comparison", elements=[element])
    return doc, engine, doc.comparison_results[0], layout, gates


def render(doc, engine, layout, **settings):
    return reports.render(
        doc,
        engine,
        reports.ReportRequest(
            revision=doc.revision,
            definition=layout,
            **settings,
        ),
    )


@pytest.mark.parametrize("mode", ["histogram", "cdf", "difference"])
def test_publication_figures_retain_actual_sources_settings_and_vector_geometry(
    comparison_report, mode
):
    doc, engine, result, layout, gates = comparison_report
    layout.elements[0].comparison_view.mode = mode
    layout.elements[0].comparison_view.smoothing = 1.25
    layout.elements[0].comparison_view.difference_scale = 2
    page = render(doc, engine, layout, validate_sources=True)
    assert page["exportable"], page["issues"]
    frame = page["manifest"]["elements"][0]
    assert frame["kind"] == "population_comparison" and frame["result_id"] == result.id
    assert frame["gate_id"] == gates[0].id and frame["row"]["finite_count"] == 5
    assert frame["row"]["control_finite_count"] == 10
    assert frame["input_snapshot"] == result.input_snapshot
    assert frame["presentation"]["mode"] == mode and frame["presentation"]["difference_scale"] == 2
    root = ET.fromstring(page["svg"])
    assert not root.findall(".//{*}image")
    curves = root.findall(".//{*}polyline")
    assert len(curves) == (1 if mode == "difference" else 3)
    assert any(t.get("fill") == "#ff0000" for t in root.findall(".//{*}text"))
    ids = [e.get("id") for e in root.iter() if e.get("id")]
    assert len(ids) == len(set(ids))
    assert render(doc, engine, layout, validate_sources=True)["svg"] == page["svg"]


def test_batch_mapping_uses_only_compared_target_populations_and_keeps_control_cohort(
    comparison_report,
):
    doc, engine, result, layout, gates = comparison_report
    layout.elements[0].iterate = True
    layout.batch = ReportBatch(mode="sample", sample_ids=[g.sample_id for g in gates[:2]])
    planned = reports.plan(doc, layout, engine)
    assert planned["page_count"] == 2
    for index, expected in enumerate([5, 10]):
        page = render(doc, engine, layout, page=index, validate_sources=True)
        assert page["exportable"], page["issues"]
        frame = page["manifest"]["elements"][0]
        assert frame["target_index"] == index and frame["gate_id"] == gates[index].id
        assert frame["row"]["finite_count"] == expected
        assert frame["input_hash"] == result.input_hash
        assert (
            frame["input_snapshot"]["settings"]["controls"]
            == result.input_snapshot["settings"]["controls"]
        )
    layout.batch.sample_ids = [gates[2].sample_id]
    page = render(doc, engine, layout, validate_sources=True)
    assert not page["exportable"]
    assert page["manifest"]["elements"][0]["unavailable"]
    assert any("not compared" in p["message"] for p in page["issues"])
    layout.export_policy = "placeholders"
    assert render(doc, engine, layout)["exportable"]


def test_ambiguous_population_paths_require_reviewed_batch_override(comparison_report):
    doc, engine, _, layout, gates = comparison_report
    duplicate = Gate(
        sample_id=gates[1].sample_id, name="Positive", kind="range", x="X", bounds=[2, 4]
    )
    doc.gates.append(duplicate)
    doc.revision += 1
    layout.elements[0].iterate = True
    layout.batch = ReportBatch(mode="sample", sample_ids=[gates[1].sample_id])
    page = render(doc, engine, layout)
    assert not page["exportable"] and any("ambiguous" in p["message"] for p in page["issues"])
    key = reports.plan(doc, layout, engine)["iterations"][0]["key"]
    layout.batch.population_overrides[key] = {gates[0].id: gates[1].id}
    page = render(doc, engine, layout, validate_sources=True)
    assert page["exportable"], page["issues"]
    assert page["manifest"]["elements"][0]["row"]["finite_count"] == 10
    layout.batch.population_overrides[key] = {gates[0].id: duplicate.id}
    page = render(doc, engine, layout)
    assert not page["exportable"] and any("not compared" in p["message"] for p in page["issues"])


def test_reviewed_refits_and_explicit_historical_policy_preserve_old_figures(comparison_report):
    doc, engine, result, layout, gates = comparison_report
    doc = engine.store.mutate(
        doc.id,
        "Adjust positive population",
        lambda w: setattr(w.gates[0], "bounds", [0, 1]),
        doc.revision,
    )
    request = result.request.model_copy(
        deep=True, update={"revision": doc.revision, "replace_result_id": result.id}
    )
    replacement = comparisons.calculate(doc, request, engine, new_id())
    doc.comparison_results.append(replacement)
    old = render(doc, engine, layout, validate_sources=True)
    assert not old["exportable"] and old["manifest"]["elements"][0]["row"]["finite_count"] == 5
    layout.export_policy = "snapshots"
    assert render(doc, engine, layout, validate_sources=True)["exportable"]
    layout.elements[0].follow_replacement = True
    updated = render(doc, engine, layout, validate_sources=True)
    frame = updated["manifest"]["elements"][0]
    assert updated["exportable"] and frame["result_id"] == replacement.id
    assert frame["row"]["finite_count"] == 2 and not frame["stale"]
    assert len(doc.comparison_results) == 2


@pytest.mark.parametrize("source", ["control", "target", "artifact"])
def test_export_source_audit_rejects_changed_bytes_after_panel_cache_is_primed(
    comparison_report, source
):
    doc, engine, result, layout, _ = comparison_report
    assert render(doc, engine, layout)["exportable"]
    path = (
        engine.store.comparison_path(doc.id, result.id)
        if source == "artifact"
        else engine.store.data_path(
            doc.id,
            doc.samples[0 if source == "control" else 1].id,
        )
    )
    content = bytearray(path.read_bytes())
    content[-1] ^= 1
    path.write_bytes(content)
    layout.export_policy = "snapshots"
    page = render(doc, engine, layout, validate_sources=True)
    assert not page["exportable"] and any("SHA-256" in p["message"] for p in page["issues"])


def test_legacy_report_elements_serialize_without_new_optional_fields():
    element = ReportElement(kind="text")
    assert "comparison_view" not in element.model_dump()
    assert "comparison_parameter_id" not in element.model_dump()
    with pytest.raises(ValueError, match="saved result"):
        ReportElement(kind="population_comparison")
