"""Reviewed removal, refit branches and scientific undo retain immutable data."""

import pytest
from cytoforge import population_comparison as comparisons
from cytoforge import population_comparison_management as management
from cytoforge.models import TableColumn, TableDefinition, new_id
from cytoforge.store import ConflictError
from cytoforge.tables import evaluate_table
from test_population_comparison_reports import comparison_report as comparison_report


def refit(doc, engine, result, bins):
    request = result.request.model_copy(
        deep=True,
        update={
            "revision": doc.revision,
            "replace_result_id": result.id,
            "probability_bins": bins,
        },
    )
    updated = comparisons.calculate(doc, request, engine, new_id())
    doc.comparison_results.append(updated)
    return updated


def consumers(doc, root, child, layout):
    doc.tables = [
        TableDefinition(
            name="Comparison consumers",
            row_mode="populations",
            columns=[
                TableColumn(
                    name=name,
                    kind="population_comparison",
                    result_id=result.id,
                    comparison_parameter_id=root.request.parameters[0].id,
                    biology_metric="finite_count",
                    follow_replacement=follow,
                )
                for name, result, follow in [
                    ("Follow original", root, True),
                    ("Fixed original", root, False),
                    ("Fixed refit", child, False),
                ]
            ],
        )
    ]
    layout.elements[0].follow_replacement = True
    doc.layouts = [layout]

    def save(workspace):
        workspace.comparison_results = doc.comparison_results
        workspace.tables = doc.tables
        workspace.layouts = doc.layouts

    return save


def test_removal_review_distinguishes_retained_history_from_missing_bindings(comparison_report):
    doc, engine, root, layout, _ = comparison_report
    child = refit(doc, engine, root, 8)
    doc = engine.store.mutate(
        doc.id, "Save consumers", consumers(doc, root, child, layout), doc.revision
    )
    preview = management.removal_preview(doc, child.id)
    assert [r["id"] for r in preview["results"]] == [child.id]
    assert {c["name"]: c["effect"] for c in preview["tables"][0]["columns"]} == {
        "Follow original": "previous_refit",
        "Fixed refit": "unavailable",
    }
    assert preview["layouts"][0]["elements"][0]["effect"] == "previous_refit"
    with pytest.raises(ValueError, match="dependent"):
        management.remove_result(doc, child.id, review_hash=preview["review_hash"])
    management.remove_result(doc, child.id, cascade=True, review_hash=preview["review_hash"])
    assert [r.id for r in doc.comparison_results] == [root.id]
    output = evaluate_table(doc, engine, doc.tables[0])
    row = next(r for r in output["rows"] if r["gate_id"] == root.request.inputs[0].gate_id)
    columns = doc.tables[0].columns
    assert row["values"][columns[0].id] == row["values"][columns[1].id] == 5
    assert row["values"][columns[2].id] is None
    assert "unavailable" in row["status"][columns[2].id]


def test_removing_parent_reviews_every_refit_branch_and_one_undo_restores_all(comparison_report):
    doc, engine, root, layout, _ = comparison_report
    child = refit(doc, engine, root, 8)
    grandchild = refit(doc, engine, child, 16)
    other_branch = refit(doc, engine, root, 32)
    doc = engine.store.mutate(
        doc.id, "Save refit branches", consumers(doc, root, child, layout), doc.revision
    )
    sources = {s.id: engine.store.data_path(doc.id, s.id).read_bytes() for s in doc.samples}
    artifacts = {
        r.id: engine.store.comparison_path(doc.id, r.id).read_bytes()
        for r in doc.comparison_results
    }
    preview = management.removal_preview(doc, root.id)
    assert {r["id"] for r in preview["results"]} == {
        root.id,
        child.id,
        grandchild.id,
        other_branch.id,
    }
    removed = engine.store.mutate(
        doc.id,
        "Reviewed removal",
        lambda w: management.remove_result(
            w,
            root.id,
            cascade=True,
            review_hash=preview["review_hash"],
        ),
        doc.revision,
    )
    assert not removed.comparison_results and removed.tables and removed.layouts
    restored = engine.store.move_history(doc.id, -1, removed.revision)
    assert restored.comparison_results == doc.comparison_results
    assert restored.tables == doc.tables and restored.layouts == doc.layouts
    assert all(
        engine.store.data_path(doc.id, sid).read_bytes() == data for sid, data in sources.items()
    )
    assert all(
        engine.store.comparison_path(doc.id, rid).read_bytes() == data
        for rid, data in artifacts.items()
    )


@pytest.mark.parametrize("change", ["rename", "new_consumer", "new_refit"])
def test_changes_after_review_require_a_new_removal_review(comparison_report, change):
    doc, engine, result, layout, _ = comparison_report
    preview = management.removal_preview(doc, result.id)
    if change == "rename":
        result.request.name = "Changed after review"
    elif change == "new_consumer":
        doc.layouts.append(layout)
    else:
        refit(doc, engine, result, 8)
    # Review hash protects dependency changes even before a caller advances its revision.
    with pytest.raises(ConflictError, match="review"):
        management.remove_result(doc, result.id, cascade=True, review_hash=preview["review_hash"])
    with pytest.raises(ConflictError, match="review"):
        management.remove_result(doc, result.id, cascade=True)


def test_cosmetic_rename_keeps_scientific_snapshot_and_artifact_valid(comparison_report):
    doc, engine, result, _, _ = comparison_report
    renamed = engine.store.mutate(
        doc.id,
        "Rename comparison",
        lambda w: setattr(w.comparison_results[0].request, "name", "Reviewed name"),
        doc.revision,
    )
    updated = renamed.comparison_results[0]
    assert updated.input_hash == result.input_hash and updated.data == result.data
    assert not comparisons.is_stale(renamed, updated)
    comparisons.load_artifact(engine.store, renamed.id, updated)


def test_invalid_result_and_corrupt_lineage_cycles_do_not_hang_removal_review(comparison_report):
    doc, engine, root, _, _ = comparison_report
    with pytest.raises(KeyError, match="not found"):
        management.removal_preview(doc, "a" * 32)
    child = refit(doc, engine, root, 8)
    root.request.replace_result_id = child.id
    preview = management.removal_preview(doc, root.id)
    assert {r["id"] for r in preview["results"]} == {root.id, child.id}
