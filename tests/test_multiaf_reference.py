"""Independent R equations, QR identifiability and physical multi-AF source truth."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from cytoforge import autospill
from cytoforge.models import Channel, Sample, Workspace
from cytoforge.science import Engine, compensate, save_events

FIXTURES = Path(__file__).parent / "fixtures/multiaf"
TRUTH = json.loads((FIXTURES / "truth.json").read_text())
REFERENCE = json.loads((FIXTURES / "reference.json").read_text())


def controls(store, case):
    settings = TRUTH[case]
    doc = Workspace(name=f"Independent multiple AF {case}")
    with np.load(FIXTURES / "controls.npz", allow_pickle=False) as arrays:
        for index, name in enumerate(settings["outputs"]):
            values = arrays[f"{case}_{index}"]
            sample = Sample(
                name=name,
                event_count=len(values),
                channels=[Channel(name=n) for n in settings["detectors"]],
            )
            sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
            doc.samples.append(sample)
    doc = store.create(doc)
    request = autospill.AutoSpillRequest(
        revision=doc.revision,
        kind="spectral",
        detectors=settings["detectors"],
        auto_cleanup=False,
        af_outputs=[settings["outputs"][i] for i in settings["af_indices"]],
        background=settings["background"],
        weights=settings["weights"],
        trim_fraction=settings["trim_fraction"],
        biex_length=4096 if settings["biex"] == "biex4096" else 256,
        controls=[
            dict(name=output, primary_detector=settings["detectors"][peak], sample_id=sample.id)
            for output, peak, sample in zip(
                settings["outputs"], settings["peaks"], doc.samples, strict=True
            )
        ],
    )
    return doc, request, Engine(store)


@pytest.mark.parametrize("case", list(TRUTH))
def test_two_af_refinement_and_span_review_match_independent_r(store, case):
    doc, request, engine = controls(store, case)
    result = autospill.calculate(doc, request, engine)
    expected = REFERENCE["cases"][case]
    assert result.compensation.outputs == TRUTH[case]["outputs"]
    for actual, name in [
        (result.diagnostics["initial_matrix"], "initial"),
        (result.compensation.matrix, "matrix"),
        (result.diagnostics["residual_slopes"], "residual"),
        ([c["reconstruction_slopes"] for c in result.diagnostics["controls"]], "reconstruction"),
        ([c["reconstruction_rms"] for c in result.diagnostics["controls"]], "reconstruction_rms"),
        (
            [c["reconstruction_relative_rms"] for c in result.diagnostics["controls"]],
            "reconstruction_relative_rms",
        ),
    ]:
        np.testing.assert_allclose(
            np.asarray(actual).squeeze(), np.asarray(expected[name]).squeeze(), atol=2e-9, rtol=2e-9
        )
    trace = [
        [
            r["iteration"],
            int(r["scale"] == "biex"),
            r["damping"],
            r["error_sd"],
            r["max_error"],
            r["error_change"],
            r["condition_number"],
        ]
        for r in result.diagnostics["convergence"]
    ]
    np.testing.assert_allclose(trace, expected["convergence"], atol=2e-8, rtol=2e-8)
    assert result.diagnostics["stop_reason"] == expected["stop_reason"]
    review = result.diagnostics["autofluorescence_sources"]
    assert [r["output"] for r in review] == [
        TRUTH[case]["outputs"][i] for i in TRUTH[case]["af_indices"]
    ]
    actual_review = [
        [
            r["orthogonal_fraction"],
            r["separation_angle_degrees"],
            r["weighted_cosine"],
            result.compensation.outputs.index(r["closest_output"]),
        ]
        for r in review
    ]
    np.testing.assert_allclose(actual_review, expected["af_review"], atol=2e-9, rtol=2e-9)


def test_two_af_numerical_oracle_is_byte_pinned_and_exact_controls_recover_known_sources(store):
    for filename, key in [("controls.npz", "controls_sha256"), ("truth.json", "truth_sha256")]:
        assert hashlib.sha256((FIXTURES / filename).read_bytes()).hexdigest() == REFERENCE[key]
    doc, request, engine = controls(store, "exact")
    matrix = autospill.calculate(doc, request, engine).compensation
    np.testing.assert_allclose(matrix.matrix, TRUTH["exact"]["signature"], atol=2e-12)
    with np.load(FIXTURES / "controls.npz", allow_pickle=False) as arrays:
        for index in range(4):
            np.testing.assert_allclose(
                compensate(arrays[f"exact_{index}"], matrix),
                arrays[f"exact_{index}_latent"],
                atol=8e-11,
            )
