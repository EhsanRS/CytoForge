"""Reviewed comparison removal preserves bindings, immutable artifacts and undo history."""

import hashlib
import json
from functools import cache

from .population_comparison import saved_result
from .store import ConflictError


def removal_preview(workspace, identifier):
    result = saved_result(workspace, identifier)
    if result is None:
        raise KeyError("Saved population comparison not found")
    identifiers = {identifier}
    while True:
        descendants = {
            r.id for r in workspace.comparison_results if r.request.replace_result_id in identifiers
        }
        if descendants <= identifiers:
            break
        identifiers |= descendants

    @cache
    def effect(reference, follow):
        if reference in identifiers:
            return "unavailable"
        resolved = saved_result(workspace, reference, follow)
        return "previous_refit" if resolved and resolved.id in identifiers else None

    tables = []
    for table in workspace.tables:
        columns = [
            dict(id=c.id, name=c.name, result_id=c.result_id, effect=affected)
            for c in table.columns
            if c.kind == "population_comparison"
            if (affected := effect(c.result_id, c.follow_replacement))
        ]
        if columns:
            tables.append(dict(id=table.id, name=table.name, columns=columns))
    layouts = []
    for layout in workspace.layouts:
        elements = [
            dict(id=e.id, title=e.title, result_id=e.result_id, effect=affected)
            for e in layout.elements
            if e.kind == "population_comparison"
            if (affected := effect(e.result_id, e.follow_replacement))
        ]
        if elements:
            layouts.append(dict(id=layout.id, name=layout.name, elements=elements))
    preview = dict(
        workspace_id=workspace.id,
        revision=workspace.revision,
        result_id=identifier,
        name=result.request.name,
        results=[
            dict(id=r.id, name=r.request.name)
            for r in workspace.comparison_results
            if r.id in identifiers
        ],
        tables=tables,
        layouts=layouts,
    )
    return dict(
        preview,
        review_hash=hashlib.sha256(json.dumps(preview, sort_keys=True).encode()).hexdigest(),
    )


def remove_result(workspace, identifier, *, cascade=False, review_hash=None):
    preview = removal_preview(workspace, identifier)
    if review_hash != preview["review_hash"]:
        raise ConflictError("Comparison dependencies changed; review the removal again")
    if not cascade and (len(preview["results"]) > 1 or preview["tables"] or preview["layouts"]):
        raise ValueError("Review dependent tables, figures and later refits before removal")
    removed = {r["id"] for r in preview["results"]}
    # Saved bindings stay visible as unavailable, or return to the previous retained refit.
    # Store snapshots retain the result records and artifacts for one-step undo.
    workspace.comparison_results = [r for r in workspace.comparison_results if r.id not in removed]
