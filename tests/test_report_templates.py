"""Portable compositions must bind to destination data without changing either acquisition."""

import json

import numpy as np
import pytest
from cytoforge import report_templates as templates
from cytoforge import reports
from cytoforge.models import (
    Channel,
    Compensation,
    Gate,
    Group,
    LayoutDefinition,
    PlateDefinition,
    PlotDefinition,
    PlotDimension,
    PlotLayer,
    ReportBatch,
    ReportElement,
    ReportPage,
    Sample,
    TableColumn,
    TableDefinition,
    ThreeDView,
    Workspace,
    new_id,
)
from cytoforge.science import Engine, save_events
from cytoforge.store import ConflictError


@pytest.fixture
def template_data(store):
    documents = []
    for destination in [False, True]:
        samples, gates = [], []
        doc = Workspace(name="Destination" if destination else "Source")
        for index in range(2):
            values = np.arange(6 if destination else 5, dtype=float) + index
            sample = Sample(
                name=f"{'Destination' if destination else 'Source'} {index}",
                channels=[Channel(name=name) for name in ["X", "Y", "Z"]],
                event_count=len(values),
                tags={"Treatment": str(index), "Private donor": "Do not export metadata"},
            )
            sample.sha256 = save_events(
                store.data_path(doc.id, sample.id), np.c_[values, values + 10, values + 20]
            )
            parent = Gate(sample_id=sample.id, name="Parent", kind="range", x="X", bounds=[0, 8])
            gate = Gate(
                sample_id=sample.id,
                parent_id=parent.id,
                name="Positive",
                kind="range",
                x="X",
                bounds=[2, 6] if destination else [1, 3],
            )
            samples.append(sample)
            gates.extend([parent, gate])
        doc.samples, doc.gates = samples, gates
        doc.groups = [Group(name="Cohort", sample_ids=[s.id for s in samples])]
        doc.tables = [
            TableDefinition(
                name="Summary",
                columns=[
                    TableColumn(name="Events"),
                    TableColumn(name="Median", statistic="median", channel="X"),
                ],
            )
        ]
        doc.compensations = [
            Compensation(
                name="Fixed",
                detectors=["X", "Y", "Z"],
                matrix=(np.eye(3) * (2 if destination else 1)).tolist(),
            )
        ]
        documents.append(store.get(store.create(doc).id))
    return *documents, Engine(store)


def composition(source, **changes):
    group = new_id()
    plot = PlotDefinition(
        sample_id=source.samples[0].id,
        gate_id=source.gates[1].id,
        coordinate_gate_id=source.gates[1].id,
        x="X",
        bins=16,
        bounds=[0, 8],
        overlays=[
            PlotLayer(
                sample_id=source.samples[1].id, gate_id=source.gates[3].id, locked_control=True
            )
        ],
    )
    elements = [
        ReportElement(kind="plot", plot=plot, group_id=group),
        ReportElement(
            kind="text",
            sample_id=source.samples[0].id,
            gate_id=source.gates[1].id,
            group_id=group,
            y_mm=130,
            height_mm=15,
            text=f"Literal {source.samples[0].id}; {{{{stat:count}}}} / {{{{stat:median:X}}}}",
        ),
        ReportElement(
            kind="table",
            table_id=source.tables[0].id,
            column_ids=[source.tables[0].columns[1].id],
            page=1,
        ),
    ]
    return LayoutDefinition(
        name="Reusable composition",
        pages=[ReportPage(), ReportPage(width_mm=279.4, height_mm=215.9)],
        elements=elements,
        **changes,
    )


def request_for(source, target, definition):
    template = templates.exported(
        source, templates.TemplateExport(revision=source.revision, definition=definition)
    )
    return templates.TemplateImport(
        revision=target.revision,
        id=new_id(),
        name="Imported composition",
        template=template,
        mappings={
            templates.binding_key("sample", s.id): t.id
            for s, t in zip(source.samples, target.samples, strict=True)
            if templates.binding_key("sample", s.id)
            in {binding["key"] for binding in template["bindings"]}
        },
    )


def originals(store, doc):
    return {sample.id: store.data_path(doc.id, sample.id).read_bytes() for sample in doc.samples}


def test_reviewed_cross_workspace_template_uses_destination_populations_and_artifacts_once(
    store, template_data
):
    source, target, engine = template_data
    definition = composition(source)
    source_before, target_before = source.model_dump_json(), target.model_dump_json()
    source_bytes, target_bytes = originals(store, source), originals(store, target)
    request = request_for(source, target, definition)
    review = templates.preview(target, request, engine)
    assert review["can_apply"], review["issues"]
    assert all(row["status"] == "mapped" for row in review["bindings"] if row["active"])
    imported = LayoutDefinition.model_validate(review["definition"])
    assert imported.id == request.id and imported.id != definition.id
    assert imported.template_origin.template_sha256 == request.template.sha256
    assert imported.template_origin.source_workspace_id == source.id
    assert imported.template_origin.source_layout_id == definition.id
    assert (
        imported.template_origin.bindings[templates.binding_key("sample", source.samples[0].id)]
        == target.samples[0].id
    )
    assert imported.pages == definition.pages
    assert (
        imported.elements[0].group_id
        == imported.elements[1].group_id
        != definition.elements[0].group_id
    )
    assert imported.elements[0].plot.sample_id == target.samples[0].id
    assert imported.elements[0].plot.gate_id == target.gates[1].id
    assert imported.elements[0].plot.overlays[0].sample_id == target.samples[1].id
    assert imported.elements[0].plot.overlays[0].locked_control
    assert imported.elements[2].table_id == target.tables[0].id
    assert imported.elements[2].column_ids == [target.tables[0].columns[1].id]
    assert source.samples[0].id in imported.elements[1].text  # Literal captions are not references.
    page = reports.render(
        target, engine, reports.ReportRequest(revision=target.revision, definition=imported)
    )
    assert page["manifest"]["elements"][0]["layers"][0]["population_count"] == 4
    assert "4 / 3.50" in page["svg"]
    request.review_hash = review["review_hash"]
    changed = templates.apply(store, target.id, request, engine)
    assert changed.revision == target.revision + 1
    assert changed.layouts[-1].id == request.id
    assert store.get(source.id).model_dump_json() == source_before
    assert originals(store, source) == source_bytes and originals(store, target) == target_bytes
    undone = store.move_history(target.id, -1, changed.revision)
    assert not undone.layouts
    redone = store.move_history(target.id, 1, undone.revision)
    assert redone.layouts[-1].model_dump() == imported.model_dump()
    assert target.model_dump_json() == target_before


def test_export_is_data_free_bounded_and_survives_numeric_default_revalidation(template_data):
    source, target, _ = template_data
    request = request_for(source, target, composition(source))
    content = request.template.model_dump_json().encode()
    assert b"Do not export metadata" not in content
    assert b"event_count" not in content and b'sha256":"' in content
    assert templates.parsed(content).model_dump() == request.template.model_dump()
    assert templates.checked_template(request.template)[0].sha256 == request.template.sha256


@pytest.mark.parametrize("edit", ["unsigned", "missing", "duplicate", "extra", "owner"])
def test_template_integrity_and_complete_owned_reference_inventory(template_data, edit):
    source, target, _ = template_data
    template = request_for(source, target, composition(source)).template.model_dump()
    if edit == "unsigned":
        template["definition"]["name"] = "Altered"
    elif edit == "missing":
        template["bindings"].pop()
    elif edit == "duplicate":
        template["bindings"].append(template["bindings"][0])
    elif edit == "extra":
        template["bindings"].append(
            dict(
                key=f"sample/{new_id()}",
                kind="sample",
                source_id=new_id(),
                owner_id=None,
                name="Unused",
                population_path=[],
                details={},
            )
        )
    else:
        gate = next(b for b in template["bindings"] if b["kind"] == "population")
        gate["owner_id"] = source.samples[1].id
    if edit != "unsigned":
        template["sha256"] = reports.digest({k: v for k, v in template.items() if k != "sha256"})
    with pytest.raises(ValueError):
        templates.parsed(json.dumps(template).encode())


def test_ambiguous_populations_require_explicit_choice_and_never_fall_back_to_all_events(
    store, template_data
):
    source, target, engine = template_data
    duplicate = target.gates[1].model_copy(update={"id": new_id()})
    target = store.mutate(
        target.id, "Add duplicate path", lambda doc: doc.gates.append(duplicate), target.revision
    )
    request = request_for(source, target, composition(source))
    review = templates.preview(target, request, engine)
    assert not review["can_apply"]
    row = next(r for r in review["bindings"] if r["source_id"] == source.gates[1].id)
    assert row["status"] == "ambiguous" and row["target"] is None
    request.mappings[row["key"]] = target.gates[1].id
    assert templates.preview(target, request, engine)["can_apply"]
    request.mappings[row["key"]] = target.gates[3].id
    assert not templates.preview(target, request, engine)["can_apply"]


@pytest.mark.parametrize("change", ["revision", "mapping", "source_bytes"])
def test_changed_review_cannot_import_a_different_destination_and_preserves_saved_data(
    store, template_data, change
):
    source, target, engine = template_data
    request = request_for(source, target, composition(source))
    request.review_hash = templates.preview(target, request, engine)["review_hash"]
    if change == "revision":
        store.mutate(
            target.id,
            "Edit experiment",
            lambda doc: setattr(doc, "description", "Changed"),
            target.revision,
        )
    elif change == "mapping":
        request.mappings[templates.binding_key("sample", source.samples[0].id)] = target.samples[
            1
        ].id
    else:
        path = store.data_path(target.id, target.samples[0].id)
        raw = np.load(path)
        raw[0, 0] = 400
        save_events(path, raw)
    with pytest.raises((ValueError, ConflictError)):
        templates.apply(store, target.id, request, engine)
    assert not store.get(target.id).layouts


def test_channel_mapping_updates_native_ratio_3d_and_live_statistics_without_rewriting_labels(
    store, template_data
):
    source, target, engine = template_data
    definition = composition(source)
    definition.elements[0].plot.overlays = []
    definition.elements[0].plot.mode = "3d"
    definition.elements[0].plot.y = "Y"
    definition.elements[0].plot.bounds = [0, 8, 0, 30, 0, 40]
    definition.elements[0].plot.x_dimension = PlotDimension(
        channel="X", compensation_ref=source.compensations[0].id
    )
    definition.elements[0].plot.three_d = ThreeDView(
        z="Z", color_by="Y", size_by="X", z_dimension=PlotDimension(channel="Z"), yaw=0.4
    )
    request = request_for(source, target, definition)
    request.mappings[templates.binding_key("channel", "X", source.samples[0].id)] = "Z"
    review = templates.preview(target, request, engine)
    assert review["can_apply"], review["issues"]
    plot = review["definition"]["elements"][0]["plot"]
    assert plot["x"] == plot["x_dimension"]["channel"] == "Z"
    assert plot["x_dimension"]["compensation_ref"] == target.compensations[0].id
    assert plot["three_d"]["size_by"] == "Z" and plot["three_d"]["yaw"] == 0.4
    assert "{{stat:median:Z}}" in review["definition"]["elements"][1]["text"]
    definition.elements[0].plot.mode = "histogram"
    definition.elements[0].plot.y = None
    definition.elements[0].plot.bounds = [0, 1]
    definition.elements[0].plot.three_d = None
    definition.elements[0].plot.x = "Ratio"
    definition.elements[0].plot.x_dimension = PlotDimension(
        channel="Ratio", ratio_channels=("X", "Y")
    )
    request = request_for(source, target, definition)
    request.mappings[templates.binding_key("channel", "X", source.samples[0].id)] = "Z"
    review = templates.preview(target, request, engine)
    assert review["can_apply"], review["issues"]
    plot = review["definition"]["elements"][0]["plot"]
    assert plot["x"] == plot["x_dimension"]["channel"] == "Ratio"
    assert plot["x_dimension"]["ratio_channels"] == ["Z", "Y"]


def test_explicit_all_events_is_a_reviewed_choice_not_an_automatic_missing_population_fallback(
    template_data,
):
    source, target, engine = template_data
    request = request_for(source, target, composition(source))
    request.mappings[templates.binding_key("population", source.gates[1].id)] = None
    review = templates.preview(target, request, engine)
    assert review["can_apply"]
    assert review["definition"]["elements"][0]["plot"]["gate_id"] is None
    assert review["definition"]["elements"][0]["plot"]["coordinate_gate_id"] is None


@pytest.mark.parametrize("destination", ["X", "Z"])
def test_live_statistic_parameter_swaps_are_simultaneous(template_data, destination):
    source, target, engine = template_data
    definition = composition(source)
    definition.elements[1].text = "{{stat:median:X}} / {{stat:median:Y}} / literal X Y"
    request = request_for(source, target, definition)
    request.mappings[templates.binding_key("channel", "X", source.samples[0].id)] = "Y"
    request.mappings[templates.binding_key("channel", "Y", source.samples[0].id)] = destination
    review = templates.preview(target, request, engine)
    assert review["can_apply"], review["issues"]
    assert review["definition"]["elements"][1]["text"] == (
        "{{stat:median:Y}} / {{stat:median:" + destination + "}} / literal X Y"
    )


@pytest.mark.parametrize("scope", ["template", "destination"])
def test_batch_rebinding_preserves_or_explicitly_rebuilds_acquisition_specific_filters(
    template_data, scope
):
    source, target, engine = template_data
    first, second = source.samples
    definition = composition(
        source,
        batch=ReportBatch(
            mode="sample",
            group_id=source.groups[0].id,
            sample_ids=[first.id],
            overrides={f"sample:{first.id}": {first.id: first.id}},
            population_overrides={f"sample:{first.id}": {source.gates[1].id: source.gates[1].id}},
        ),
    )
    request = request_for(source, target, definition)
    request.batch_scope = scope
    request.batch_sample_ids = [target.samples[1].id]
    review = templates.preview(target, request, engine)
    assert review["can_apply"], review["issues"]
    batch = review["definition"]["batch"]
    assert batch["group_id"] == target.groups[0].id
    if scope == "template":
        assert batch["sample_ids"] == [target.samples[0].id]
        assert batch["overrides"] == {
            f"sample:{target.samples[0].id}": {target.samples[0].id: target.samples[0].id}
        }
        assert batch["population_overrides"] == {
            f"sample:{target.samples[0].id}": {target.gates[1].id: target.gates[1].id}
        }
    else:
        assert batch["sample_ids"] == [target.samples[1].id]
        assert not batch["overrides"] and not batch["population_overrides"]


def test_plate_and_pooled_group_bind_to_destination_data(store, template_data):
    source, target, engine = template_data
    for doc in (source, target):
        doc.plates.append(PlateDefinition(name="Plate", assignments={"A01": [doc.samples[0].id]}))
    definition = LayoutDefinition(
        name="Cohort and plate",
        elements=[
            ReportElement(
                kind="plot",
                plot=PlotDefinition(
                    sample_id=source.samples[0].id,
                    group_id=source.groups[0].id,
                    pooled=True,
                    x="X",
                ),
            ),
            ReportElement(kind="plate", plate_id=source.plates[0].id, iterate=False),
        ],
    )
    request = request_for(source, target, definition)
    review = templates.preview(target, request, engine)
    assert review["can_apply"], review["issues"]
    imported = LayoutDefinition.model_validate(review["definition"])
    assert imported.elements[0].plot.group_id == target.groups[0].id
    assert imported.elements[1].plate_id == target.plates[0].id
    page = reports.render(
        target,
        engine,
        reports.ReportRequest(revision=target.revision, definition=imported, validate_sources=True),
    )
    assert page["exportable"], page["issues"]
    assert page["manifest"]["elements"][0]["layers"][0]["population_count"] == 12
    plate = page["manifest"]["elements"][1]["data"]
    assert plate["wells"][0]["values"][target.plates[0].columns[0].id] == 6
    assert originals(store, source) == originals(store, store.get(source.id))


def test_unused_keyword_overrides_remain_saved_and_are_explicitly_reviewed(template_data):
    source, target, engine = template_data
    sample = source.samples[0].id
    definition = composition(
        source,
        batch=ReportBatch(
            mode="keyword",
            iterator_keyword="Treatment",
            overrides={"keyword:Absent treatment": {sample: sample}},
        ),
    )
    request = request_for(source, target, definition)
    request.batch_scope = "template"
    review = templates.preview(target, request, engine)
    assert review["can_apply"], review["issues"]
    assert review["definition"]["batch"]["overrides"] == {
        "keyword:Absent treatment": {target.samples[0].id: target.samples[0].id}
    }
    assert any(
        issue["severity"] == "warning" and "no destination iteration" in issue["message"]
        for issue in review["issues"]
    )


@pytest.mark.parametrize("damage", ["duplicate", "oversized", "invalid_encoding", "not_json"])
def test_portable_parser_rejects_ambiguous_or_unbounded_files(template_data, damage):
    source, target, _ = template_data
    content = request_for(source, target, composition(source)).template.model_dump_json().encode()
    if damage == "duplicate":
        content = content.replace(b'{"format":', b'{"format":"bad","format":', 1)
    elif damage == "oversized":
        content = b" " * (templates.MAX_TEMPLATE_BYTES + 1)
    elif damage == "invalid_encoding":
        content = b"\xff"
    else:
        content = b"{"
    with pytest.raises(ValueError):
        templates.parsed(content)
