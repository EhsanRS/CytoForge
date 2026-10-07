import io

import flowio
import numpy as np
import pytest
from cytoforge.models import Compensation, Gate, Transform, Workspace, new_id
from cytoforge.science import (
    ArrayCache,
    compensate,
    export_fcs,
    parse_fcs,
    polygon_mask,
    transform,
    validate_matrix,
)
from pydantic import ValidationError


@pytest.mark.parametrize("kind", ["linear", "log", "logicle", "hyperlog", "asinh"])
def test_transform_round_trip(kind):
    values = np.array([-10000, -50, 0, 1, 300, 50000, 262144], dtype=float)
    if kind == "log":
        values = values[values > 0]
    spec = Transform(kind=kind)
    actual = transform(transform(values, spec), spec, inverse=True)
    np.testing.assert_allclose(actual, values, rtol=1e-10, atol=1e-7)
    assert np.all(np.diff(transform(values, spec)) > 0)


def test_logicle_reference_values():
    # GatingML defaults place zero at W / (M + A), and T at 1.
    actual = transform(np.array([0, 262144]), Transform(kind="logicle"))
    np.testing.assert_allclose(actual, [0.5 / 4.5, 1.0], atol=1e-12)


def test_fcs_preprocessed_detector_ranges_match_gain_and_time_units(tmp_path):
    buffer = io.BytesIO()
    flowio.create_fcs(
        buffer,
        [100, 25, 200, 50],
        ["X", "Time"],
        metadata_dict={"p1g": "2", "p1r": "1000", "p2r": "2000", "timestep": "0.1"},
    )
    path = tmp_path / "gain-time.fcs"
    path.write_bytes(buffer.getvalue())
    sample, events, _, _ = parse_fcs(path, "Gain and time")
    np.testing.assert_allclose(events, [[50, 2.5], [100, 5]])
    assert sample.channels[0].range == 500
    assert sample.channels[1].range == 200


@pytest.mark.parametrize("parameters", [{"w": 3}, {"a": -1}, {"m": 0}, {"t": 0}])
def test_invalid_transform_parameters(parameters):
    with pytest.raises(ValidationError):
        Transform(kind="logicle", **parameters)


def test_negative_values_log_transform():
    actual = transform(np.array([-1, 0, 10]), Transform(kind="log"))
    assert np.isnan(actual[:2]).all()
    assert actual[2] == 1


def test_compensation_row_source_convention_and_spectral_unmixing():
    true = np.array([[100, 200], [-10, 15], [0, 0]], float)
    matrix = Compensation(name="Spillover", detectors=["X", "Y"], matrix=[[1, 0.2], [0.1, 1]])
    measured = true @ np.array(matrix.matrix)
    np.testing.assert_allclose(compensate(measured, matrix), true, atol=1e-12)
    spectra = Compensation(
        name="Spectra",
        detectors=["D1", "D2", "D3"],
        outputs=["F1", "F2"],
        kind="spectral",
        matrix=[[1, 0.2, 0.1], [0.1, 1, 0.3]],
    )
    np.testing.assert_allclose(
        compensate(true @ np.array(spectra.matrix), spectra), true, atol=1e-12
    )


def test_singular_and_unstable_compensation_rejected():
    for values in [[[1, 1], [1, 1]], [[1, 0], [0, 1e-12]]]:
        with pytest.raises(ValueError):
            validate_matrix(Compensation(name="Invalid", detectors=["X", "Y"], matrix=values))


def test_polygon_boundary_and_concave_shape():
    vertices = [(0, 0), (3, 0), (3, 1), (1, 1), (1, 3), (0, 3)]
    points = np.array([[0, 0], [1, 2], [2, 2], [3, 1], [0.5, 0.5], [np.nan, 0]])
    assert polygon_mask(points[:, 0], points[:, 1], vertices).tolist() == [
        True,
        True,
        False,
        True,
        True,
        False,
    ]


def add_gate(store, doc, **kwargs):
    gate = Gate(sample_id=doc.samples[0].id, **kwargs)
    return store.mutate(
        doc.id, "Create gate", lambda state: state.gates.append(gate), doc.revision
    ), gate


def test_hierarchical_masks_boolean_and_quadrant_partition(store, dataset):
    doc, sample, _, engine = dataset
    doc, parent = add_gate(
        store, doc, name="Parent", kind="rectangle", x="X", y="Y", bounds=[0, 4, 0, 4]
    )
    assert engine.mask(doc, sample, parent.id).tolist() == [
        False,
        True,
        True,
        True,
        True,
        True,
        False,
        False,
    ]
    doc, child = add_gate(
        store, doc, name="Child", parent_id=parent.id, kind="range", x="X", bounds=[1, 10]
    )
    assert engine.mask(doc, sample, child.id).sum() == 3
    doc, complement = add_gate(
        store,
        doc,
        name="Complement",
        parent_id=parent.id,
        kind="boolean",
        operation="not",
        operands=[child.id],
    )
    assert engine.mask(doc, sample, complement.id).sum() == 2
    masks = []
    for quadrant in [1, 2, 3, 4]:
        doc, gate = add_gate(
            store,
            doc,
            name=f"Q{quadrant}",
            kind="quadrant",
            parent_id=parent.id,
            x="X",
            y="Y",
            bounds=[1, 1],
            quadrant=quadrant,
        )
        masks.append(engine.mask(doc, sample, gate.id))
    assert np.all(np.sum(masks, axis=0) == engine.mask(doc, sample, parent.id))
    counts = {r["id"]: r for r in engine.gate_counts(doc, sample)}
    assert counts[child.id]["percent_parent"] == 60


def test_cycle_and_cross_sample_references_rejected(dataset):
    doc, sample, _, _ = dataset
    first = Gate(sample_id=sample.id, name="A", kind="range", x="X", bounds=[0, 2])
    second = Gate(
        sample_id=sample.id, name="B", kind="range", x="Y", bounds=[0, 2], parent_id=first.id
    )
    first.parent_id = second.id
    with pytest.raises(ValidationError, match="cycle"):
        Workspace.model_validate(
            doc.model_dump() | {"gates": [first.model_dump(), second.model_dump()]}
        )
    with pytest.raises(ValidationError, match="same sample"):
        Workspace.model_validate(
            doc.model_dump() | {"gates": [first.model_dump() | {"parent_id": new_id()}]}
        )


def test_empty_statistics_and_finite_filtering(store, dataset):
    doc, sample, _, engine = dataset
    doc, gate = add_gate(store, doc, name="Empty", kind="range", x="X", bounds=[100, 200])
    empty = engine.summary(doc, sample, gate.id, "X")
    assert empty["count"] == 0 and empty["median"] is None
    root = engine.summary(doc, sample, None, "X")
    assert root["count"] == 8 and root["finite_count"] == 7 and root["median"] == 1
    assert root["geometric_mean"] is None


def test_plot_counts_use_all_events_and_bounds_are_explicit(dataset):
    doc, sample, _, engine = dataset
    result = engine.plot(
        doc, sample, "X", "Y", Transform(), Transform(), bins=16, bounds=[-5, 5, -5, 5]
    )
    assert result["count"] == 8 and result["finite_count"] == 7
    assert sum(result["counts"]) == 7 and result["visible_count"] == 7
    result = engine.plot(doc, sample, "X", None, Transform(), Transform(), bins=16, bounds=[0, 2])
    assert sum(result["counts"]) == 4


def test_cache_bounded_and_immutable():
    cache = ArrayCache(max_bytes=80)
    first = cache.put((1,), np.zeros(10))
    assert not first.flags.writeable
    cache.put((2,), np.ones(10))
    assert cache.bytes == 80 and cache.get((1,)) is None
    cache.put((3,), np.ones(100))
    assert cache.bytes == 80


def test_fcs_round_trip_preserves_values(dataset):
    _, sample, values, _ = dataset
    output = export_fcs(values[:-1], sample, "Parent")
    parsed = flowio.FlowData(io.BytesIO(output))
    np.testing.assert_allclose(parsed.as_array(), values[:-1], rtol=1e-6)
    assert parsed.channels[1]["pnn"] == "X"
