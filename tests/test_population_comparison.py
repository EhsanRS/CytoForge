"""Distribution workflows over independent event populations and persisted artifacts."""

import hashlib
import json

import numpy as np
import pytest
from cytoforge import population_comparison as comparisons
from cytoforge.models import (
    AnalysisInput,
    Channel,
    ComparisonParameter,
    Gate,
    PopulationComparisonRequest,
    Sample,
    Transform,
    Workspace,
    new_id,
)
from cytoforge.science import Engine, save_events


def fixture(store, arrays=None):
    if arrays is None:
        arrays = [
            np.c_[np.repeat([0, 1, 2], [4, 4, 2]), np.arange(10)],
            np.c_[np.repeat([0, 1, 2, 3], [2, 3, 1, 4]), np.arange(10)],
        ]
    samples = [
        Sample(
            name=f"Acquisition {i}",
            channels=[Channel(name="X"), Channel(name="Y")],
            event_count=len(v),
        )
        for i, v in enumerate(arrays)
    ]
    doc = Workspace(name="Independent population comparison", samples=samples)
    for sample, values in zip(samples, arrays, strict=True):
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.create(doc)
    request = PopulationComparisonRequest(
        revision=doc.revision,
        name="Stimulated versus control",
        inputs=[AnalysisInput(sample_id=samples[1].id)],
        controls=[AnalysisInput(sample_id=samples[0].id)],
        parameters=[
            ComparisonParameter(channel="X", transform=Transform()),
            ComparisonParameter(channel="Y", transform=Transform()),
        ],
        probability_bins=4,
        minimum_bin_events=1,
    )
    return doc, request, Engine(store)


def calculate(store, arrays=None, modify=None):
    doc, request, engine = fixture(store, arrays)
    if modify:
        modify(doc, request)
    before = {s.id: store.data_path(doc.id, s.id).read_bytes() for s in doc.samples}
    result = comparisons.calculate(doc, request, engine, new_id())
    assert store.get(doc.id).model_dump() == doc.model_dump()
    assert all(store.data_path(doc.id, sid).read_bytes() == value for sid, value in before.items())
    return doc, request, engine, result


def test_source_calculation_and_frozen_histogram_counts(store):
    doc, request, _, result = calculate(store)
    row = result.rows[0]
    assert row.parameter_id == request.parameters[0].id
    assert row.selected_count == row.finite_count == row.control_finite_count == 10
    assert row.metrics["overton_cumulative_percent"] == pytest.approx(40)
    assert row.metrics["ens_percent"] == pytest.approx(44)
    assert row.role == "target"
    assert all(r.status == "unavailable" for r in result.rows if r.role == "control_baseline")
    arrays, metadata = comparisons.load_artifact(store, doc.id, result)
    assert arrays["control_0"].sum() == arrays["targets_0"][0].sum() == 10
    assert metadata["statistics_hash"] == comparisons.statistics_hash(
        result.rows, result.joint_rows
    )
    assert not comparisons.is_stale(doc, result)


def test_overlapping_control_populations_are_pooled_once(store):
    doc, request, engine = fixture(store)
    subset = Gate(
        sample_id=doc.samples[0].id,
        name="Low control",
        kind="range",
        x="X",
        x_transform=Transform(),
        bounds=[0, 2],
    )
    doc.gates.append(subset)
    request.controls.append(AnalysisInput(sample_id=subset.sample_id, gate_id=subset.id))
    result = comparisons.calculate(doc, request, engine, new_id())
    row = result.rows[0]
    assert row.control_selected_count == row.control_finite_count == 10
    data, _ = comparisons.load_artifact(store, doc.id, result)
    assert data["control_0"].sum() == 10
    assert data["individual_0"].sum(axis=1).tolist() == [10, 8]


def test_original_event_overlap_disables_independent_probability(store):
    doc, request, engine = fixture(store)
    request.inputs = [request.controls[0]]
    result = comparisons.calculate(doc, request, engine, new_id())
    row = result.rows[0]
    assert row.shared_events == 10
    assert row.metrics["ks_distance"] == 0
    assert row.metrics["ks_p_value"] is None


def test_control_baselines_exclude_entire_original_acquisition(store):
    arrays = [np.c_[np.arange(12), np.arange(12)] for _ in range(3)]
    doc, request, engine = fixture(store, arrays)
    request.inputs = [AnalysisInput(sample_id=doc.samples[2].id)]
    request.controls = [AnalysisInput(sample_id=s.id) for s in doc.samples[:2]]
    result = comparisons.calculate(doc, request, engine, new_id())
    target = [r for r in result.rows if r.role == "target"]
    baselines = [r for r in result.rows if r.role == "control_baseline"]
    assert len(target) == 2 and len(baselines) == 4
    assert all(r.control_finite_count == 24 for r in target)
    assert all(r.control_finite_count == 12 and r.shared_events == 0 for r in baselines)
    assert all(r.metrics["ks_distance"] == 0 for r in [*target, *baselines])


def test_empty_and_nonfinite_members_are_reported_per_coordinate_and_jointly(store):
    c = np.c_[np.arange(5), np.arange(5)]
    t = np.array([[1, 1], [2, np.nan], [np.nan, 3], [np.inf, 4]])
    doc, request, _, result = calculate(store, [c, t])
    target = [r for r in result.rows if r.role == "target"]
    assert [r.selected_count for r in target] == [4, 4]
    assert [r.finite_count for r in target] == [2, 3]
    assert result.joint_rows[0].selected_count == 4
    assert result.joint_rows[0].finite_count == 1
    data, _ = comparisons.load_artifact(store, doc.id, result)
    assert data["targets_0"][0].sum() == 2
    assert data["targets_1"][0].sum() == 3
    _, _, _, empty = calculate(store, [c, np.empty((0, 2))])
    assert all(r.status == "unavailable" and r.error for r in empty.rows)


def test_joint_only_differences_survive_source_workflow(store):
    spread = np.array([[-0.05, -0.2], [-0.05, 0.2], [0.05, -0.2], [0.05, 0.2]])
    c = np.repeat(np.concatenate([spread - 2, spread + 2]), 16, axis=0)
    t = c.copy()
    t[:, 1] = c[::-1, 1]
    _, _, _, result = calculate(
        store, [c, t], modify=lambda _, request: setattr(request, "probability_bins", 8)
    )
    assert all(r.metrics["ks_distance"] == 0 for r in result.rows if r.role == "target")
    assert result.joint_rows[0].probability["chi_squared"] > 0


def test_scientific_dependency_changes_and_cosmetic_renames(store):
    doc, request, engine = fixture(store)
    gate = Gate(
        sample_id=doc.samples[1].id, name="Target subset", kind="range", x="X", bounds=[0, 2]
    )
    doc.gates.append(gate)
    request.inputs[0].gate_id = gate.id
    result = comparisons.calculate(doc, request, engine, new_id())
    gate.name = "Renamed subset"
    gate.color = "#ff0066"
    doc.name = "Renamed workspace"
    assert not comparisons.is_stale(doc, result)
    doc.gates.append(
        Gate(sample_id=doc.samples[1].id, name="Unrelated", kind="range", x="Y", bounds=[0, 1])
    )
    assert not comparisons.is_stale(doc, result)
    gate.bounds = [1, 2]
    assert comparisons.is_stale(doc, result)


def test_captured_artifact_binds_every_reported_statistic(store):
    doc, _, _, result = calculate(store)
    altered = result.model_copy(deep=True)
    altered.rows[0].metrics["ens_percent"] = 99.0
    with pytest.raises(ValueError, match="statistics"):
        comparisons.load_artifact(store, doc.id, altered)
    path = store.comparison_path(doc.id, result.id)
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 1
    path.write_bytes(data)
    with pytest.raises(ValueError, match="SHA-256"):
        comparisons.load_artifact(store, doc.id, result)


def test_corrupted_acquired_bytes_are_rechecked_after_cache_use(store):
    doc, request, engine = fixture(store)
    engine.raw(doc, doc.samples[0])
    path = store.data_path(doc.id, doc.samples[0].id)
    values = bytearray(path.read_bytes())
    values[-1] ^= 1
    path.write_bytes(values)
    identifier = new_id()
    with pytest.raises(ValueError, match="SHA-256"):
        comparisons.calculate(doc, request, engine, identifier)
    assert not store.comparison_path(doc.id, identifier).exists()


def test_legacy_workspace_serialization_omits_empty_comparison_field(store):
    doc, _, _ = fixture(store)
    value = doc.model_dump()
    assert "comparison_results" not in value
    assert Workspace.model_validate(value).model_dump() == value


def test_request_and_result_schema_reject_false_source_and_event_claims(store):
    doc, request, engine = fixture(store)
    request.inputs[0].gate_id = new_id()
    with pytest.raises(ValueError, match="population"):
        comparisons.calculate(doc, request, engine, new_id())
    with pytest.raises(ValueError, match="repeated"):
        PopulationComparisonRequest.model_validate(
            {**request.model_dump(), "controls": [request.controls[0].model_dump()] * 2}
        )
    doc, _, _, result = calculate(store)
    bad = result.model_dump()
    bad["rows"][0]["finite_count"] = 999
    with pytest.raises(ValueError, match="Finite"):
        type(result).model_validate(bad)
    assert hashlib.sha256(json.dumps(doc.model_dump()).encode()).hexdigest()
