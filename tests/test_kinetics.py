import math

import numpy as np
import pytest
from cytoforge import kinetics
from cytoforge.models import (
    AnalysisInput,
    Channel,
    Gate,
    KineticsRange,
    KineticsRequest,
    Sample,
    Transform,
    Workspace,
    new_id,
)
from cytoforge.science import Engine, save_array, save_events
from pydantic import ValidationError


def acquisition(store, times, signal, **settings):
    values = np.column_stack([times, signal]).astype(np.float64)
    sample = Sample(
        name="Independent kinetics truth",
        event_count=len(values),
        channels=[Channel(name=n, transform=Transform(kind="linear")) for n in ["Time", "Signal"]],
    )
    doc = Workspace(name="Kinetics references", samples=[sample])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.create(doc)
    request = KineticsRequest(
        revision=0,
        inputs=[AnalysisInput(sample_id=sample.id)],
        channel="Signal",
        bins=8,
        **settings,
    )
    return doc, sample, request


def calculate(store, doc, request):
    result, arrays = kinetics.calculate(doc, request, Engine(store), new_id())
    for data in result.data:
        kinetics.validate_data(result, data, arrays[data.sample_id])
    return result, arrays


@pytest.mark.parametrize("statistic", ["mean", "median", "percentile", "geometric_mean"])
def test_full_event_bin_metrics_match_independent_references(store, statistic):
    times = np.repeat(np.arange(8) + 0.5, 4)
    signal = np.concatenate([np.array([1, 2, 4, 8]) * (i + 1) for i in range(8)])
    doc, _, request = acquisition(
        store, times, signal, time_min=0, time_max=8, statistic=statistic, percentile=75
    )
    result, _ = calculate(store, doc, request)
    reference = {
        "mean": 3.75,
        "median": 3,
        "percentile": 5,
        "geometric_mean": math.sqrt(8),
    }[statistic]
    np.testing.assert_allclose(
        [b.raw_value for b in result.fits[0].bins], reference * np.arange(1, 9)
    )
    assert [b.finite_count for b in result.fits[0].bins] == [4] * 8
    assert result.data[0].represented_count == 32


def test_linear_curve_summaries_have_known_slope_peak_integral_and_duration(store):
    times = np.repeat(np.arange(8) + 0.5, 3)
    doc, _, request = acquisition(store, times, 4 * times + 8, time_min=0, time_max=8)
    result, _ = calculate(store, doc, request)
    summary = result.fits[0].ranges[0]
    assert summary.peak == 38 and summary.peak_time == 7.5
    assert summary.mean == 24
    assert summary.slope == pytest.approx(4)
    assert summary.auc == pytest.approx(168) and summary.covered_duration == 7
    assert summary.population_count == 24 and summary.curve_points == 8


def test_adjacent_ranges_assign_boundary_events_once_and_include_final_time(store):
    doc, _, request = acquisition(store, [0, 1, 2, 3, 4, 5, 6, 8], [1] * 8, time_min=0, time_max=8)
    request.ranges = [
        KineticsRange(name="Baseline", start=0, end=4),
        KineticsRange(name="Response", start=4, end=8),
    ]
    result, arrays = calculate(store, doc, request)
    fit = result.fits[0]
    data = arrays[fit.sample_id]
    first = kinetics.selection(data, fit, request.ranges[0].id)
    second = kinetics.selection(data, fit, request.ranges[1].id)
    np.testing.assert_array_equal(first, [True] * 4 + [False] * 4)
    np.testing.assert_array_equal(second, [False] * 4 + [True] * 4)
    assert not (first & second).any() and (first | second).all()
    assert fit.bins[-1].population_count == 1


def test_baseline_percentile_threshold_counts_exact_responders_and_finite_denominator(store):
    times = np.repeat(np.arange(8) + 0.5, 4)
    signal = np.tile([1, 2, 3, 4], 8).astype(float)
    signal[16:] += 10
    signal[20] = np.nan
    doc, _, request = acquisition(
        store,
        times,
        signal,
        time_min=0,
        time_max=8,
        statistic="percent_positive",
        threshold_mode="baseline_percentile",
        baseline=KineticsRange(name="Baseline", start=0, end=4),
    )
    result, arrays = calculate(store, doc, request)
    fit = result.fits[0]
    threshold = float(np.quantile(signal[:16], 0.95))
    assert fit.threshold == threshold and fit.baseline_count == 16
    assert [b.raw_value for b in fit.bins] == [0] * 4 + [100] * 4
    assert fit.bins[5].population_count == 4 and fit.bins[5].finite_count == 3
    response = kinetics.selection(arrays[fit.sample_id], fit, fit.ranges[0].id, True)
    np.testing.assert_array_equal(response, np.isfinite(signal) & (signal > threshold))


def test_baseline_can_precede_the_cropped_analysis_time_domain(store):
    times = np.arange(16) + 0.5
    signal = np.r_[np.arange(8), np.full(8, 100)]
    doc, _, request = acquisition(
        store,
        times,
        signal,
        time_min=8,
        time_max=16,
        threshold_mode="baseline_percentile",
        statistic="percent_positive",
        baseline=KineticsRange(name="Earlier baseline", start=0, end=8),
    )
    result, _ = calculate(store, doc, request)
    assert result.fits[0].baseline_count == 8
    assert result.fits[0].threshold == pytest.approx(np.percentile(np.arange(8), 95))
    assert [b.value for b in result.fits[0].bins] == [100] * 8


@pytest.mark.parametrize("smoothing", ["none", "moving_average", "gaussian"])
def test_missing_bins_stay_missing_and_integrals_never_cross_gaps(store, smoothing):
    times = [0.5, 1.5, 3.5, 4.5, 5.5, 7.5]
    doc, _, request = acquisition(
        store, times, [10] * 6, time_min=0, time_max=8, smoothing=smoothing
    )
    result, _ = calculate(store, doc, request)
    fit = result.fits[0]
    assert [b.value for b in fit.bins] == [10, 10, None, 10, 10, 10, None, 10]
    assert fit.ranges[0].auc == 30
    assert fit.ranges[0].covered_duration == 3


def test_gaussian_smoothing_uses_centered_bin_weights_and_cropped_edges(store):
    doc, _, request = acquisition(
        store,
        np.arange(8) + 0.5,
        np.arange(8) ** 2,
        time_min=0,
        time_max=8,
        smoothing="gaussian",
        smoothing_width=3,
        gaussian_sigma=1,
    )
    result, _ = calculate(store, doc, request)
    expected = []
    for i in range(8):
        neighbours = range(max(0, i - 1), min(8, i + 2))
        weights = [math.exp(-0.5 * (j - i) ** 2) for j in neighbours]
        expected.append(
            math.fsum(j * j * w for j, w in zip(neighbours, weights, strict=True))
            / math.fsum(weights)
        )
    np.testing.assert_allclose([b.value for b in result.fits[0].bins], expected)
    assert [b.raw_value for b in result.fits[0].bins] == list(np.arange(8) ** 2)


def test_responders_only_geometric_means_keep_zero_negative_and_missing_distinct(store):
    times = np.repeat(np.arange(8) + 0.5, 4)
    signal = np.tile([-1, 0, 1, 2], 8)
    doc, _, request = acquisition(
        store, times, signal, time_min=0, time_max=8, statistic="geometric_mean"
    )
    result, _ = calculate(store, doc, request)
    assert all(b.value is None for b in result.fits[0].bins)
    assert any("nonpositive" in w for w in result.fits[0].warnings)
    request.above_threshold_only = True
    result, _ = calculate(store, doc, request)
    np.testing.assert_allclose([b.value for b in result.fits[0].bins], [math.sqrt(2)] * 8)
    assert all(b.selected_count == 2 for b in result.fits[0].bins)


def test_clock_decreases_require_explicit_policy_and_unwrap_retains_original_event_order(store):
    doc, sample, request = acquisition(store, [0, 0.5, 1, 0, 0.5, 1], [1] * 6)
    with pytest.raises(ValueError, match="Time decreases"):
        calculate(store, doc, request)
    request.clock_policy = "unwrap"
    request.time_multiplier = 2
    request.time_offsets = {sample.id: -1}
    result, arrays = calculate(store, doc, request)
    np.testing.assert_array_equal(arrays[sample.id][:, 0], [-1, 0, 1, 2, 3, 4])
    assert result.fits[0].reset_count == 1
    request.clock_policy = "use_recorded"
    result, arrays = calculate(store, doc, request)
    np.testing.assert_array_equal(arrays[sample.id][:, 0], [-1, 0, 1, -1, 0, 1])
    assert result.fits[0].reset_count == 1


def test_estimated_time_uses_original_event_ids_without_compressing_the_selected_population(store):
    doc, sample, request = acquisition(store, np.arange(8), np.arange(8))
    gate = Gate(
        sample_id=sample.id,
        name="Selected",
        kind="range",
        x="Signal",
        x_transform=Transform(kind="linear"),
        bounds=[3, 6],
    )
    doc.gates.append(gate)
    request.inputs[0].gate_id = gate.id
    request.time_channel = None
    request.event_rate = 2
    result, arrays = calculate(store, doc, request)
    np.testing.assert_array_equal(arrays[sample.id][:, 0], np.arange(8) / 2)
    assert result.data[0].population_count == 3
    np.testing.assert_array_equal(arrays[sample.id][:, 2], (np.arange(8) >= 3) & (np.arange(8) < 6))
    assert any("estimated" in w for w in result.fits[0].warnings)


def test_empty_population_and_undefined_clock_have_explicit_missing_curves(store):
    doc, sample, request = acquisition(store, np.arange(8), np.arange(8))
    gate = Gate(
        sample_id=sample.id,
        name="Empty",
        kind="range",
        x="Signal",
        x_transform=Transform(kind="linear"),
        bounds=[-2, -1],
    )
    doc.gates.append(gate)
    request.inputs[0].gate_id = gate.id
    result, _ = calculate(store, doc, request)
    assert result.data[0].population_count == 0
    assert result.fits[0].time_domain == (0, 7)
    assert all(b.value is None for b in result.fits[0].bins)
    assert result.fits[0].ranges[0].auc is None
    doc, _, request = acquisition(store, [np.nan] * 8, [1] * 8)
    result, _ = calculate(store, doc, request)
    assert result.fits[0].time_domain is None and result.fits[0].bins == []
    assert result.data[0].time_count == 0


def test_empty_baseline_leaves_response_measurements_undefined(store):
    doc, _, request = acquisition(
        store,
        np.arange(8),
        np.arange(8),
        statistic="percent_positive",
        threshold_mode="baseline_percentile",
        baseline=KineticsRange(start=30, end=40),
    )
    result, _ = calculate(store, doc, request)
    assert result.fits[0].threshold is None
    assert all(b.value is None for b in result.fits[0].bins)
    assert any("baseline" in w for w in result.fits[0].warnings)


def test_sampled_curve_is_not_weighted_by_acquisition_rate(store):
    times = np.repeat(np.arange(8) + 0.5, np.arange(1, 9))
    signal = np.repeat(np.arange(8) + 1, np.arange(1, 9))
    doc, _, request = acquisition(store, times, signal, time_min=0, time_max=8)
    result, _ = calculate(store, doc, request)
    assert result.fits[0].ranges[0].mean == 4.5
    assert np.mean(signal) != 4.5


def test_slope_retains_small_variations_on_large_time_and_signal_offsets(store):
    elapsed = np.arange(8) + 0.5
    doc, _, request = acquisition(
        store, 1e12 + elapsed, 1e12 + 3 * elapsed, time_min=1e12, time_max=1e12 + 8
    )
    result, _ = calculate(store, doc, request)
    assert result.fits[0].ranges[0].slope == pytest.approx(3, rel=1e-14)


def test_extreme_finite_mean_median_and_smoothing_do_not_overflow(store):
    times = np.repeat(np.arange(8) + 0.5, 2)
    for metric in ["mean", "median"]:
        doc, _, request = acquisition(
            store,
            times,
            np.tile([-1e308, 1e308], 8),
            time_min=0,
            time_max=8,
            statistic=metric,
            smoothing="moving_average",
        )
        result, _ = calculate(store, doc, request)
        assert [b.value for b in result.fits[0].bins] == [0] * 8
        assert result.fits[0].ranges[0].slope == 0 and result.fits[0].ranges[0].auc == 0


def test_constant_recorded_time_supports_manual_range_counts_without_inventing_a_curve(store):
    doc, _, request = acquisition(
        store, [0] * 8, np.arange(8), ranges=[KineticsRange(name="Instant", start=0, end=1)]
    )
    result, _ = calculate(store, doc, request)
    assert result.fits[0].time_domain is None and not result.fits[0].bins
    assert result.fits[0].ranges[0].population_count == 8
    assert result.fits[0].ranges[0].auc is None


def test_estimated_clock_overflow_is_rejected_as_a_calibration_error(store):
    doc, _, request = acquisition(store, np.arange(8), [1] * 8)
    request.time_channel = None
    request.event_rate = 1e-323
    with pytest.raises(ValueError, match="nonfinite acquisition duration"):
        calculate(store, doc, request)


def test_native_file_hashes_and_event_membership_counts_are_checked(store):
    doc, _, request = acquisition(store, np.arange(8), np.arange(8))
    result, arrays = calculate(store, doc, request)
    data = result.data[0]
    path = store.analysis_path(doc.id, result.id, data.sample_id)
    data.sha256 = save_array(path, arrays[data.sample_id])
    np.testing.assert_array_equal(
        kinetics.load_data(store, doc.id, result, data), arrays[data.sample_id]
    )
    corrupt = arrays[data.sample_id].copy()
    corrupt[0, 2] = 2
    with pytest.raises(ValueError, match="membership"):
        kinetics.validate_data(result, data, corrupt)
    corrupt[0, 2] = 0
    with pytest.raises(ValueError, match="event counts"):
        kinetics.validate_data(result, data, corrupt)
    path.write_bytes(path.read_bytes()[:-8])
    with pytest.raises(ValueError, match="integrity"):
        kinetics.load_data(store, doc.id, result, data)


@pytest.mark.parametrize(
    "settings",
    [
        {"time_channel": None},
        {"event_rate": 100},
        {"time_min": 10, "time_max": 10},
        {"threshold_mode": "baseline_percentile"},
        {"smoothing_width": 4},
        {"statistic": "percent_positive", "above_threshold_only": True},
        {"time_offsets": {"f" * 32: 1}},
        {"bins": 10000},
        {"threshold": float("inf")},
    ],
)
def test_invalid_clock_curve_and_threshold_settings_fail_explicitly(settings):
    with pytest.raises(ValidationError):
        KineticsRequest(
            revision=0, inputs=[AnalysisInput(sample_id="a" * 32)], channel="Signal", **settings
        )
