"""Independent discrete distributions, published equations and joint-only changes."""

import math

import numpy as np
import pytest
from cytoforge.population_statistics import (
    bin_counts,
    empirical,
    histogram_comparison,
    histogram_edges,
    log_variance,
    midpoint,
    probability_edges,
    probability_score,
    probability_tree,
    univariate_probability,
)
from scipy import stats


def test_literal_cumulative_and_bagwell_ens_equation():
    # At 2: C=1, T=.6, D=.4. At the prefix maximum 0: C=.4, T=.2.
    # Bagwell ENS-1 = .4/1 + (.4*.6 - 1*.2)/1**2 = .44.
    c = np.repeat([0, 1, 2], [4, 4, 2])
    t = np.repeat([0, 1, 2, 3], [2, 3, 1, 4])
    result = empirical(c, t)
    assert result["ks_distance"] == pytest.approx(0.4)
    assert result["ks_at_coordinate"] == 2
    assert result["ks_signed_control_minus_test"] == pytest.approx(0.4)
    assert result["overton_cumulative_percent"] == pytest.approx(40)
    assert result["enhanced_dmax_percent"] == pytest.approx(40)
    assert result["ens_percent"] == pytest.approx(44)
    assert result["ens_secondary_at_coordinate"] == 0
    assert result["ks_p_value"] == pytest.approx(stats.ks_2samp(c, t).pvalue)


@pytest.mark.parametrize("direction", ["higher", "lower"])
def test_fully_separated_distributions(direction):
    c, t = np.arange(4), np.arange(5, 9)
    if direction == "lower":
        c, t = t, c
    result = empirical(c, t, direction=direction)
    assert result["ks_distance"] == 1
    assert result["ks_p_value"] == pytest.approx(2 / math.comb(8, 4))
    assert result["overton_cumulative_percent"] == 100
    assert result["enhanced_dmax_percent"] == 100
    assert result["ens_percent"] == 100


def test_direction_changes_positive_estimates_and_keeps_ks_location():
    c, t = np.arange(5, 9), np.arange(4)
    higher, lower = empirical(c, t), empirical(c, t, direction="lower")
    assert higher["ens_percent"] == higher["overton_cumulative_percent"] == 0
    assert lower["ens_percent"] == lower["overton_cumulative_percent"] == 100
    for key in ["ks_distance", "ks_at_coordinate", "ks_p_value", "ks_signed_control_minus_test"]:
        assert higher[key] == lower[key]
    assert lower["ks_at_coordinate"] == 3


def test_different_event_counts_same_distribution():
    c, t = np.arange(20), np.repeat(np.arange(20), 3)
    result = empirical(c, t)
    assert result["ks_distance"] == result["overton_cumulative_percent"] == 0
    assert result["ens_percent"] == 0
    assert result["ks_p_value"] == 1
    pb = univariate_probability(c, t, bins=4)
    assert pb["chi_squared"] == pb["tx"] == 0
    assert pb["control_counts"] == [5, 5, 5, 5]
    assert pb["test_counts"] == [15, 15, 15, 15]


def test_shared_event_identities_have_no_independent_ks_probability():
    result = empirical(np.arange(8), np.arange(8), shared_events=8)
    assert result["ks_distance"] == 0
    assert result["ks_p_value"] is None
    assert "overlapping" in result["ks_p_method"]


def test_exact_ecdf_agrees_with_brute_counts_at_every_observed_value():
    rng = np.random.default_rng(410)
    c, t = rng.integers(-8, 13, 75), rng.integers(-10, 17, 123)
    points = sorted(set(c) | set(t))
    difference = [sum(c <= x) / len(c) - sum(t <= x) / len(t) for x in points]
    index = np.argmax(np.abs(difference))
    result = empirical(c, t)
    assert result["ks_distance"] == pytest.approx(abs(difference[index]))
    assert result["ks_at_coordinate"] == points[index]
    assert result["overton_cumulative_percent"] == pytest.approx(100 * max(difference))
    assert result["continuous_null_has_ties"]


def test_large_population_asymptotic_probability_matches_scipy():
    rng = np.random.default_rng(491)
    c = rng.normal(size=10_101)
    t = rng.normal(0.05, 1.1, size=11_107)
    result = empirical(c, t)
    standard = stats.ks_2samp(c, t, method="asymp")
    assert result["ks_distance"] == pytest.approx(standard.statistic)
    assert result["ks_p_value"] == pytest.approx(standard.pvalue)


@pytest.mark.parametrize("seed", range(6))
def test_positive_estimators_are_bounded_and_nested(seed):
    rng = np.random.default_rng(seed)
    c = rng.normal(size=400)
    t = np.r_[rng.normal(size=220), rng.normal(3, 0.9, size=180)]
    r = empirical(c, t)
    assert 0 <= r["overton_cumulative_percent"] <= r["enhanced_dmax_percent"]
    assert r["enhanced_dmax_percent"] <= r["ens_percent"] <= 100


def test_literal_probability_bins_and_published_tx_calibration():
    r = univariate_probability(np.arange(1, 9), np.full(4, 1), bins=4)
    # [2,2,2,2]/8 versus [4,0,0,0]/4 gives chi'=9/20+3/4=6/5.
    assert r["control_counts"] == [2, 2, 2, 2]
    assert r["test_counts"] == [4, 0, 0, 0]
    assert r["chi_squared"] == pytest.approx(6 / 5)
    assert r["baseline_chi_squared"] == 1
    assert r["baseline_standard_deviation"] == 0.5
    assert r["tx"] == pytest.approx(0.4)
    assert r["maximum_tx"] == 2


def test_univariate_ties_are_not_jittered_or_split():
    c = np.repeat([0, 1], [7, 3])
    r = univariate_probability(c, [0, 0, 1, 1], bins=8)
    assert r["bin_count"] == 2
    assert r["control_counts"] == [7, 3]
    assert r["test_counts"] == [2, 2]
    assert not r["control_bins_have_equal_counts"]
    assert probability_edges(c, 8).tolist() == [0.5]


def test_no_distinct_control_bins_are_unavailable_even_if_test_is_shifted():
    r = univariate_probability(np.zeros(100), np.ones(100), 16)
    assert r["status"] == "unavailable"
    assert r["chi_squared"] is None and r["tx"] is None
    assert empirical(np.zeros(100), np.ones(100))["ks_distance"] == 1


def test_joint_only_change_with_identical_univariate_distributions():
    c = np.repeat([[-1, -1], [1, 1]], 64, axis=0)
    t = np.repeat([[-1, 1], [1, -1]], 64, axis=0)
    assert all(empirical(c[:, i], t[:, i])["ks_distance"] == 0 for i in [0, 1])
    tree = probability_tree(c, bins=4)
    # No jitter creates imaginary control clusters: two degenerate clusters are two leaves.
    assert tree.control_counts.tolist() == [64, 64]
    # Control-only bins cannot distinguish these perfectly degenerate clusters.
    # Add real within-cluster spread to expose a joint correlation change.
    spread = np.array([[-0.05, -0.2], [-0.05, 0.2], [0.05, -0.2], [0.05, 0.2]])
    c = np.repeat(np.concatenate([spread - 2, spread + 2]), 16, axis=0)
    t = c.copy()
    t[:, 1] = c[::-1, 1]
    assert all(empirical(c[:, i], t[:, i])["ks_distance"] == 0 for i in [0, 1])
    tree = probability_tree(c, bins=8)
    actual = tree.compare(t)
    assert actual["chi_squared"] > 0.5
    assert actual["tx"] > 2
    assert tree.compare(c)["chi_squared"] == 0
    assert np.bincount(tree.assign(c)).tolist() == tree.control_counts.tolist()


def test_multivariate_greatest_variance_and_literal_counts():
    c = np.array([[0, 0], [1, 10], [2, 20], [3, 30]], dtype=float)
    tree = probability_tree(c, bins=2)
    assert tree.nodes[0]["axis"] == 1
    assert tree.nodes[0]["cut"] == 15
    t = np.array([[0, -10], [0, 100], [0, 100], [0, 100]])
    assert tree.assign(t).tolist() == [0, 1, 1, 1]
    r = tree.compare(t)
    assert r["control_counts"] == [2, 2]
    assert r["test_counts"] == [1, 3]
    assert r["chi_squared"] == pytest.approx(2 / 15)


@pytest.mark.parametrize("scale", [1e-310, 1.0, 1e300])
def test_variance_ranking_survives_extreme_scales(scale):
    c = np.array([[0, 0], [1, 10], [2, 20], [3, 30]], dtype=float) * scale
    tree = probability_tree(c, bins=2)
    assert tree.nodes[0]["axis"] == 1
    assert tree.control_counts.tolist() == [2, 2]
    assert np.bincount(tree.assign(c)).tolist() == [2, 2]


@pytest.mark.parametrize("bins", [2, 3, 4, 7, 16, 64])
def test_tree_deterministic_complete_partition_and_input_unchanged(bins):
    rng = np.random.default_rng(177)
    c = rng.normal(size=(256, 3))
    saved = c.copy()
    a, b = probability_tree(c, bins), probability_tree(c, bins)
    assert a.serialize() == b.serialize()
    assert len(a.control_counts) == 2 ** int(np.floor(np.log2(bins)))
    assert sum(a.control_counts) == len(c)
    assert np.array_equal(c, saved)
    r = a.compare(c)
    assert r["chi_squared"] == r["tx"] == 0


def test_whole_tied_groups_and_minimum_leaf_population():
    c = np.repeat([[0, 0], [1, 1], [2, 2]], [9, 11, 20], axis=0)
    tree = probability_tree(c, 32, minimum_events=10)
    labels = tree.assign(c)
    for value in [0, 1, 2]:
        assert len(set(labels[c[:, 0] == value])) == 1
    assert tree.control_counts.min() >= 10
    assert tree.compare(c)["chi_squared"] == 0


def test_tree_checks_cancellation_and_dimension_ownership():
    def canceled():
        raise InterruptedError("canceled")

    with pytest.raises(InterruptedError):
        probability_tree(np.ones((10, 2)), check=canceled)
    tree = probability_tree(np.c_[np.arange(10), np.arange(10)], 4)
    with pytest.raises(InterruptedError):
        tree.assign(np.ones((10, 2)), check=canceled)
    with pytest.raises(ValueError, match="dimensions"):
        tree.assign(np.ones((10, 3)))


@pytest.mark.parametrize("values", [[], [np.nan], [np.inf], [[1, 2]]])
def test_nonfinite_or_nonvector_populations_require_explicit_eligibility(values):
    with pytest.raises(ValueError):
        empirical(values, [0, 1])


@pytest.mark.parametrize("bins", [0, 1, 4097, 1.5])
def test_invalid_bin_settings_rejected(bins):
    with pytest.raises(ValueError):
        probability_edges(np.arange(10), bins)
    with pytest.raises(ValueError):
        probability_tree(np.ones((10, 2)), bins)


def test_histogram_normalization_finite_cdf_and_peak_excess():
    c, t = np.repeat([0, 1], [4, 4]), np.repeat([0, 2], [2, 6])
    result = histogram_comparison(c, t, 2)
    assert result["control"].tolist() == [4, 4]
    assert result["test"].tolist() == [2, 6]
    # Peak normalized control is [6,6], giving zero excess, unlike area subtraction.
    assert result["peak_normalized_excess_percent"] == 0
    assert result["control_cdf"][-1] == result["test_cdf"][-1] == 1


@pytest.mark.parametrize("value", [0.0, -1e308, 1e308, np.finfo(float).max])
def test_constant_histogram_coordinates_count_every_event(value):
    c, t = np.full(10, value), np.full(3, value)
    result = histogram_comparison(c, t, 128)
    assert result["control"].sum() == 10 and result["test"].sum() == 3
    assert result["peak_normalized_excess_percent"] == 0
    assert np.isfinite(result["edges"]).all()


def test_histogram_and_probability_cuts_include_both_extreme_tails():
    c = np.array([-1e308, -1e300, 1e300, 1e308])
    t = np.array([-1.1e308, 0, 1.1e308])
    r = histogram_comparison(c, t, 32)
    assert r["control"].sum() == 4 and r["test"].sum() == 3
    p = univariate_probability(c, t, 4)
    assert sum(p["control_counts"]) == 4 and sum(p["test_counts"]) == 3


def test_subnormal_midpoint_retains_a_half_open_partition():
    tiny = np.nextafter(0.0, 1.0)
    cut = midpoint(0, tiny)
    assert cut == tiny
    result = univariate_probability([0, tiny], [0, tiny], 2)
    assert result["control_counts"] == [1, 1]
    assert result["test_counts"] == [1, 1]


def test_invalid_domains_counts_and_positive_directions():
    with pytest.raises(ValueError):
        bin_counts([-1], histogram_edges([0, 1], [0, 1], 8))
    with pytest.raises(ValueError):
        bin_counts([0], [0, 0])
    with pytest.raises(ValueError):
        empirical([0], [0], direction="both")
    with pytest.raises(ValueError):
        empirical([0], [0], shared_events=2)
    with pytest.raises(ValueError):
        probability_score([0, 0], [1, 1])
    with pytest.raises(ValueError):
        probability_score([1, -1], [1, 1])
    assert log_variance(np.zeros(5)) == -math.inf
