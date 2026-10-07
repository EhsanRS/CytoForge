"""Complete empirical distributions and control-derived probability bins.

T(X) follows Roederer et al., Cytometry 45:37-55 (2001), with E=min(Nc,Nt).
ENS follows Bagwell's author publication, Journey Through Immunofluorescence
Analysis, equation ENS-1. No unpublished SED correction is inferred.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy import stats

from .comparison_storage import ColumnMatrix


def vector(values):
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 1 or not len(values) or not np.all(np.isfinite(values)):
        raise ValueError("Distribution comparisons require nonempty finite vectors")
    return values


def empirical(control, test, *, direction="higher", shared_events=0):
    """Exact ECDF effects; separate event-level inference from overlapping inputs."""
    control, test = vector(control), vector(test)
    if direction not in {"higher", "lower"}:
        raise ValueError("Positive direction must be higher or lower")
    if not 0 <= shared_events <= min(len(control), len(test)):
        raise ValueError("Shared event count is inconsistent with the populations")
    a, b = np.sort(control), np.sort(test)
    coordinates = np.union1d(a, b)
    c = np.searchsorted(a, coordinates, side="right") / len(a)
    t = np.searchsorted(b, coordinates, side="right") / len(b)
    difference = c - t
    ks_index = int(np.argmax(np.abs(difference)))
    distance = float(abs(difference[ks_index]))
    ks_at = float(coordinates[ks_index])
    ks_signed = float(difference[ks_index])
    if direction == "lower":
        c = (1 - np.searchsorted(a, coordinates, side="left") / len(a))[::-1]
        t = (1 - np.searchsorted(b, coordinates, side="left") / len(b))[::-1]
        coordinates = coordinates[::-1]
        difference = c - t
    positive_index = int(np.argmax(difference))
    positive = max(0.0, float(difference[positive_index]))
    ed = correction = 0.0
    secondary_index = None
    if positive:
        cd, td = float(c[positive_index]), float(t[positive_index])
        ed = positive / cd
        # ENS-1 adds a second, prefix-normalized Dmax to enhanced Dmax.
        # This form remains defined when Td=0, including fully separated samples.
        prefix = (c[: positive_index + 1] * td - cd * t[: positive_index + 1]) / cd**2
        secondary_index = int(np.argmax(prefix))
        correction = max(0.0, float(prefix[secondary_index]))
    probability = None
    p_method = "unavailable: overlapping event identities"
    if not shared_events:
        if max(len(a), len(b)) <= 10_000:
            probability = float(stats.ks_2samp(a, b, method="exact").pvalue)
            p_method = "two-sided automatic finite-sample continuous-null KS"
        else:
            effective = len(a) / (1 + len(a) / len(b))
            probability = float(stats.distributions.kstwo.sf(distance, max(1, round(effective))))
            p_method = "two-sided asymptotic continuous-null KS"
    tied_control = len(a) - len(np.unique(a))
    tied_test = len(b) - len(np.unique(b))
    return {
        "ks_distance": distance,
        "ks_at_coordinate": ks_at,
        "ks_signed_control_minus_test": ks_signed,
        "ks_p_value": probability,
        "ks_p_method": p_method,
        "continuous_null_has_ties": bool(tied_control or tied_test),
        "overton_cumulative_percent": 100 * positive,
        "enhanced_dmax_percent": 100 * min(1.0, ed),
        "ens_percent": 100 * min(1.0, ed + correction),
        "ens_prefix_correction": correction,
        "positive_at_coordinate": float(coordinates[positive_index]),
        "ens_secondary_at_coordinate": (
            float(coordinates[secondary_index]) if secondary_index is not None else None
        ),
        "control_count": len(a),
        "test_count": len(b),
        "shared_events": int(shared_events),
    }


def midpoint(left, right):
    """A finite cut between ordered values, retaining subnormal and extreme values."""
    if not np.isfinite(left) or not np.isfinite(right) or not left < right:
        raise ValueError("A probability-bin cut needs two distinct finite values")
    middle = left / 2 + right / 2
    # The right value itself is a valid half-open cut if rounding reaches an end.
    return float(middle if left < middle < right else right)


def histogram_edges(control, test, bins):
    control, test = vector(control), vector(test)
    if not isinstance(bins, int) or not 2 <= bins <= 4096:
        raise ValueError("Histogram resolution must be between 2 and 4096")
    lower = min(float(control.min()), float(test.min()))
    upper = max(float(control.max()), float(test.max()))
    if lower == upper:
        padding = max(abs(lower) * 1e-6, 1.0)
        with np.errstate(over="ignore"):
            lo, hi = lower - padding, upper + padding
        lower = lo if np.isfinite(lo) else float(np.nextafter(lower, -np.inf))
        upper = hi if np.isfinite(hi) else upper
    weights = np.linspace(0, 1, bins + 1)
    # Avoid high-low overflow and remove edges which collapse at machine precision.
    edges = np.unique(lower * (1 - weights) + upper * weights)
    edges[0], edges[-1] = lower, upper
    if len(edges) < 2:
        raise ValueError("The histogram domain cannot be represented")
    return edges


def bin_counts(values, edges):
    values = vector(values)
    edges = np.asarray(edges, dtype=np.float64)
    if len(edges) < 2 or not np.all(np.isfinite(edges)) or np.any(edges[1:] <= edges[:-1]):
        raise ValueError("Histogram edges must be finite and strictly increasing")
    if np.any(values < edges[0]) or np.any(values > edges[-1]):
        raise ValueError("Histogram coordinates fall outside the shared domain")
    indices = np.minimum(np.searchsorted(edges, values, side="right") - 1, len(edges) - 2)
    return np.bincount(indices, minlength=len(edges) - 1)


def histogram_comparison(control, test, bins=256):
    edges = histogram_edges(control, test, bins)
    a, b = bin_counts(control, edges), bin_counts(test, edges)
    scaled_control = a * (float(b.max()) / float(a.max()))
    excess = np.maximum(0, b - scaled_control)
    return {
        "edges": edges,
        "control": a,
        "test": b,
        "control_cdf": np.cumsum(a) / int(a.sum()),
        "test_cdf": np.cumsum(b) / int(b.sum()),
        "peak_normalized_excess_percent": float(100 * excess.sum() / b.sum()),
    }


def probability_edges(control, bins=64):
    control = np.sort(vector(control))
    if not isinstance(bins, int) or not 2 <= bins <= 4096:
        raise ValueError("Probability bins must be between 2 and 4096")
    positions = np.unique(np.arange(1, bins, dtype=np.int64) * len(control) // bins)
    positions = positions[(positions > 0) & (positions < len(control))]
    cuts = [midpoint(control[i - 1], control[i]) for i in positions if control[i - 1] < control[i]]
    return np.unique(np.asarray(cuts, dtype=np.float64))


def probability_score(control_counts, test_counts):
    a, b = np.asarray(control_counts), np.asarray(test_counts)
    if (
        a.ndim != 1
        or a.shape != b.shape
        or not len(a)
        or not np.all(np.isfinite(a))
        or not np.all(np.isfinite(b))
        or np.any(a < 0)
        or np.any(b < 0)
        or np.any(a != np.floor(a))
        or np.any(b != np.floor(b))
        or a.sum() <= 0
        or b.sum() <= 0
    ):
        raise ValueError("Probability bin counts must be aligned nonnegative populations")
    count = len(a)
    if count < 2:
        return {
            "status": "unavailable",
            "reason": "The control does not support distinct probability bins",
            "bin_count": count,
            "chi_squared": None,
            "tx": None,
            "maximum_tx": None,
            "control_counts": a.astype(np.int64).tolist(),
            "test_counts": b.astype(np.int64).tolist(),
        }
    ca, cb = a / a.sum(), b / b.sum()
    denominator = ca + cb
    usable = denominator > 0
    chi_squared = float(np.sum((ca[usable] - cb[usable]) ** 2 / denominator[usable]))
    events = min(float(a.sum()), float(b.sum()))
    baseline, sigma = count / events, math.sqrt(count) / events
    return {
        "status": "available",
        "bin_count": count,
        "chi_squared": chi_squared,
        "tx": max(0.0, (chi_squared - baseline) / sigma),
        "maximum_tx": max(0.0, (2 - baseline) / sigma),
        "baseline_chi_squared": baseline,
        "baseline_standard_deviation": sigma,
        "calibration_minimum_events": int(events),
        "minimum_control_bin_events": int(a.min()),
        "control_bins_have_equal_counts": bool(a.min() == a.max()),
        "control_counts": a.astype(np.int64).tolist(),
        "test_counts": b.astype(np.int64).tolist(),
    }


def univariate_probability(control, test, bins=64):
    control, test = vector(control), vector(test)
    cuts = probability_edges(control, bins)
    a = np.bincount(np.searchsorted(cuts, control, side="right"), minlength=len(cuts) + 1)
    b = np.bincount(np.searchsorted(cuts, test, side="right"), minlength=len(cuts) + 1)
    return {**probability_score(a, b), "cuts": cuts.tolist(), "requested_bins": bins}


def matrix(values):
    if isinstance(values, ColumnMatrix):
        if not len(values) or not 1 <= values.shape[1] <= 64:
            raise ValueError(
                "Joint comparisons require nonempty finite matrices with 1–64 dimensions"
            )
        return values
    values = np.asarray(values, dtype=np.float64)
    if (
        values.ndim != 2
        or not values.shape[0]
        or not 1 <= values.shape[1] <= 64
        or not np.all(np.isfinite(values))
    ):
        raise ValueError("Joint comparisons require nonempty finite matrices with 1–64 dimensions")
    return values


def matrix_column(values, axis, events):
    return values.column(axis, events) if isinstance(values, ColumnMatrix) else values[events, axis]


def log_variance(values):
    """Compare original variances without squaring huge or subnormal coordinates."""
    with np.errstate(over="ignore", invalid="ignore"):
        differences = values - values[0]
    if not np.all(np.isfinite(differences)):
        scale = float(np.max(np.abs(values)))
        differences = values / scale
    else:
        scale = float(np.max(np.abs(differences)))
        if not scale:
            return -math.inf
        differences = differences / scale
    variance = float(np.var(differences))
    return 2 * math.log(scale) + math.log(variance) if variance > 0 else -math.inf


@dataclass
class ProbabilityTree:
    dimensions: int
    nodes: list[dict]
    control_counts: np.ndarray
    requested_bins: int

    def assign(self, values, check: Callable[[], None] = lambda: None):
        values = matrix(values)
        if values.shape[1] != self.dimensions:
            raise ValueError("Joint comparison dimensions do not match the control bins")
        labels = np.empty(len(values), dtype=np.int32)
        pending = [(0, np.arange(len(values)))]
        while pending:
            check()
            index, events = pending.pop()
            node = self.nodes[index]
            if "leaf" in node:
                labels[events] = node["leaf"]
            else:
                left = matrix_column(values, node["axis"], events) < node["cut"]
                pending.extend([(node["left"], events[left]), (node["right"], events[~left])])
        return labels

    def compare(self, values, check: Callable[[], None] = lambda: None):
        labels = self.assign(values, check)
        counts = np.bincount(labels, minlength=len(self.control_counts))
        return {
            **probability_score(self.control_counts, counts),
            "requested_bins": self.requested_bins,
            "dimensions": self.dimensions,
        }

    def serialize(self):
        return {
            "dimensions": self.dimensions,
            "requested_bins": self.requested_bins,
            "nodes": self.nodes,
            "control_counts": self.control_counts.tolist(),
        }


def probability_tree(control, bins=64, minimum_events=1, check=lambda: None):
    """Median splits on each node's greatest-variance parameter; ties stay whole."""
    control = matrix(control)
    if not isinstance(bins, int) or not 2 <= bins <= 4096:
        raise ValueError("Probability bins must be between 2 and 4096")
    if not isinstance(minimum_events, int) or minimum_events < 1:
        raise ValueError("The minimum events per bin must be positive")
    # The published multivariate partition applies complete binary split levels.
    depth = int(math.floor(math.log2(bins)))
    nodes, counts = [], []

    def split(events, level):
        check()
        index = len(nodes)
        nodes.append({})
        if level < depth and len(events) >= 2 * minimum_events:
            ranking = sorted(
                range(control.shape[1]),
                key=lambda axis: (-log_variance(matrix_column(control, axis, events)), axis),
            )
            for axis in ranking:
                column = matrix_column(control, axis, events)
                ordered = np.sort(column)
                if ordered[0] == ordered[-1]:
                    continue
                middle = len(ordered) // 2
                if ordered[middle - 1] < ordered[middle]:
                    positions = [middle]
                else:
                    value = ordered[middle]
                    positions = sorted(
                        [
                            int(np.searchsorted(ordered, value, side="left")),
                            int(np.searchsorted(ordered, value, side="right")),
                        ],
                        key=lambda position: (abs(position - middle), position),
                    )
                positions = [
                    p for p in positions if minimum_events <= p <= len(ordered) - minimum_events
                ]
                if not positions:
                    continue
                position = positions[0]
                cut = midpoint(ordered[position - 1], ordered[position])
                left = column < cut
                first = split(events[left], level + 1)
                second = split(events[~left], level + 1)
                nodes[index] = {"axis": axis, "cut": cut, "left": first, "right": second}
                return index
        nodes[index] = {"leaf": len(counts)}
        counts.append(len(events))
        return index

    split(np.arange(len(control)), 0)
    return ProbabilityTree(control.shape[1], nodes, np.asarray(counts), bins)
