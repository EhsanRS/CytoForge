"""Portable panels use actual destination model artifacts and parameter ownership."""

import numpy as np
import pytest
from cytoforge import biology, kinetics, reports
from cytoforge import report_templates as templates
from cytoforge.models import LayoutDefinition, ReportElement, new_id
from cytoforge.science import Engine, save_array
from test_biology_management import initial_model
from test_kinetics import acquisition
from test_population_comparison_reports import comparison_report
from test_report_templates import originals


def saved_biology(client, platform, index):
    store = client.app.state.store
    if platform != "kinetics":
        _, doc, sample, _, identifier = initial_model(client, platform)
        field = templates.COLLECTIONS[platform.replace("-", "_")]
        result = next(result for result in getattr(doc, field) if result.id == identifier)
        return doc, sample, result
    times = np.repeat(np.arange(8) + 0.5, 4)
    doc, sample, request = acquisition(
        store, times, (index + 1) * times + 5, time_min=0, time_max=8
    )
    doc = store.get(doc.id)
    result, arrays = kinetics.calculate(doc, request, Engine(store), new_id())
    for data in result.data:
        data.sha256 = save_array(
            store.analysis_path(doc.id, result.id, data.sample_id), arrays[data.sample_id]
        )
        kinetics.load_data(store, doc.id, result, data)
    doc = store.mutate(
        doc.id,
        "Save actual kinetics model",
        lambda value: value.kinetics_results.append(result),
        doc.revision,
    )
    return doc, sample, doc.kinetics_results[0]


@pytest.mark.parametrize("platform", ["cell-cycle", "proliferation", "kinetics"])
def test_templates_render_destination_biological_models_without_copying_or_refitting(
    client, platform
):
    store = client.app.state.store
    source, source_sample, source_model = saved_biology(client, platform, 0)
    target, target_sample, target_model = saved_biology(client, platform, 1)
    engine = Engine(store)
    definition = LayoutDefinition(
        name="Reusable fitted figure",
        elements=[
            ReportElement(
                kind="biology",
                platform=platform,
                result_id=source_model.id,
                sample_id=source_sample.id,
                follow_replacement=False,
                iterate=False,
            )
        ],
    )
    template = templates.exported(
        source, templates.TemplateExport(revision=source.revision, definition=definition)
    )
    # A portable model panel describes its binding rather than carrying fitted curves.
    assert "observed" not in str(template) and "input_snapshot" not in str(template)
    request = templates.TemplateImport(
        revision=target.revision,
        id=new_id(),
        name="Destination model figure",
        template=template,
        mappings={templates.binding_key("sample", source_sample.id): target_sample.id},
    )
    files = {
        store.analysis_path(doc.id, model.id, data.sample_id): store.analysis_path(
            doc.id, model.id, data.sample_id
        ).read_bytes()
        for doc, model in [(source, source_model), (target, target_model)]
        for data in model.data
    }
    acquired = {doc.id: originals(store, doc) for doc in (source, target)}
    source_before, target_before = source.model_dump(), target.model_dump()
    review = templates.preview(target, request, engine)
    assert review["can_apply"], review["issues"]
    imported = LayoutDefinition.model_validate(review["definition"])
    assert imported.elements[0].result_id == target_model.id
    page = reports.render(
        target,
        engine,
        reports.ReportRequest(revision=target.revision, definition=imported, validate_sources=True),
    )
    assert page["exportable"], page["issues"]
    panel = page["manifest"]["elements"][0]
    assert panel["result_id"] == target_model.id and panel["sample_id"] == target_sample.id
    assert (
        panel["input_hash"] == target_model.input_hash
        and panel["data"] == target_model.data[0].model_dump()
    )
    request.review_hash = review["review_hash"]
    changed = templates.apply(store, target.id, request, engine)
    field = templates.COLLECTIONS[platform.replace("-", "_")]
    assert getattr(changed, field)[0].model_dump() == target_before[field][0]
    assert store.get(source.id).model_dump() == source_before
    assert all(path.read_bytes() == contents for path, contents in files.items())
    assert all(originals(store, doc) == acquired[doc.id] for doc in (source, target))
    missing = target.model_copy(deep=True)
    biology.remove_model(missing, platform, target_model.id, cascade=True)
    request.review_hash = None
    assert not templates.preview(missing, request, engine)["can_apply"]


def test_compared_populations_and_coordinates_belong_to_the_destination_result(store):
    source, engine, source_model, definition, source_gates = comparison_report.__wrapped__(store)
    target, _, target_model, _, target_gates = comparison_report.__wrapped__(store)
    request = templates.TemplateImport(
        revision=target.revision,
        id=new_id(),
        name="Destination compared figure",
        template=templates.exported(
            source, templates.TemplateExport(revision=source.revision, definition=definition)
        ),
        mappings={
            templates.binding_key("sample", source_gates[0].sample_id): target_gates[0].sample_id
        },
    )
    acquired = {doc.id: originals(store, doc) for doc in (source, target)}
    files = {
        store.comparison_path(doc.id, model.id): store.comparison_path(
            doc.id, model.id
        ).read_bytes()
        for doc, model in [(source, source_model), (target, target_model)]
    }
    review = templates.preview(target, request, engine)
    assert review["can_apply"], review["issues"]
    imported = LayoutDefinition.model_validate(review["definition"])
    panel = imported.elements[0]
    assert panel.result_id == target_model.id and panel.gate_id == target_gates[0].id
    assert panel.comparison_parameter_id == target_model.request.parameters[0].id
    page = reports.render(
        target,
        engine,
        reports.ReportRequest(revision=target.revision, definition=imported, validate_sources=True),
    )
    assert page["exportable"], page["issues"]
    actual = page["manifest"]["elements"][0]
    assert actual["result_id"] == target_model.id and actual["row"]["finite_count"] == 5
    assert actual["input_snapshot"] == target_model.input_snapshot
    request.review_hash = review["review_hash"]
    changed = templates.apply(store, target.id, request, engine)
    assert changed.comparison_results[0].model_dump() == target_model.model_dump()
    assert all(path.read_bytes() == contents for path, contents in files.items())
    assert all(originals(store, doc) == acquired[doc.id] for doc in (source, target))
    request.id, request.revision, request.review_hash = new_id(), changed.revision, None
    request.mappings[
        templates.binding_key("comparison_parameter", source_model.request.parameters[0].id)
    ] = source_model.request.parameters[1].id
    denied = templates.preview(changed, request, engine)
    assert not denied["can_apply"]
    assert any(
        row["kind"] == "comparison_parameter" and row["status"] == "missing"
        for row in denied["bindings"]
    )
