"""AutoSpill's flowWorkspace-style biexponential refinement coordinates.

Lookup mathematics follows FlowKit, Copyright 2018 Scott White, BSD-3-Clause (see
licenses/FlowKit-BSD-3-Clause.txt). The native reference uses single-precision
lookup positions, an integer stopping rule and a natural cubic spline with
linear extrapolation. These compatibility choices apply only to AutoSpill;
workspace display and interchange transforms retain their existing behavior.
"""

import math
from functools import lru_cache

import numpy as np
from scipy.interpolate import CubicSpline


def _negative_range(positive, width):
    """Safeguarded Newton solve with the legacy integer convergence criterion."""
    lower, upper = 0.0, positive
    root = positive / 2
    previous_step = abs(int(positive))
    constant = width * positive - 2 * math.log(positive)
    value = 2 * math.log(root) + width * positive + constant
    derivative = 2 / root + width
    if width == 0:
        return positive
    for _ in range(100):
        bracket_product = ((root - upper) * derivative - value) * (
            (root - lower) * derivative - value
        )
        use_bisection = bracket_product >= 0 or abs(int(2 * value)) > abs(
            int(previous_step * derivative)
        )
        step = (upper - lower) / 2 if use_bisection else value / derivative
        candidate = lower + step if use_bisection else root - step
        stalled = candidate == lower if use_bisection else candidate == root
        if stalled or abs(int(step)) == 0:
            return candidate
        root, previous_step = candidate, step
        value = 2 * math.log(root) + width * root + constant
        derivative = 2 / root + width
        if value < 0:
            lower = root
        else:
            upper = root
    return root


def lookup(negative, width_basis, positive, top, length):
    width_decades = math.log10(-width_basis)
    if not 0.5 <= width_decades <= 3:
        width_decades = 0.5
    extra = max(negative, 0) + width_decades / 2
    decades = positive - width_decades / 2
    zero = min(int(extra * length / (extra + decades)), length // 2)
    if zero:
        decades = extra * length / zero
    compressed_width = width_decades / (2 * decades)
    positive_range = math.log(10) * decades
    negative_range = _negative_range(positive_range, compressed_width)
    output = np.arange(length + 1, dtype=float)
    positions = (output.astype(np.float32) / np.float32(length + 1)).astype(float)
    ascending = np.exp(positions * positive_range)
    descending = np.exp(-positions * negative_range)
    descending *= math.exp((positive_range + negative_range) * (compressed_width + extra / decades))
    origin = ascending[zero] - descending[zero]
    ascending[zero:] = (top / math.exp(positive_range)) * (
        ascending[zero:] - descending[zero:] - origin
    )
    ascending[:zero] = -ascending[2 * zero - np.arange(zero)]
    if not np.isfinite(ascending).all() or np.any(np.diff(ascending) <= 0):
        raise ValueError("AutoSpill biex parameters do not produce a finite monotonic lookup")
    return ascending, output


class NaturalLinearSpline:
    """Natural cubic interpolation within the table, tangent lines outside it."""

    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.spline = CubicSpline(x, y, bc_type="natural", extrapolate=False)
        self.left_slope = float(self.spline(x[0], 1))
        self.right_slope = float(self.spline(x[-1], 1))

    def __call__(self, values):
        values = np.asarray(values, dtype=float)
        inside = self.spline(np.clip(values, self.x[0], self.x[-1]))
        return np.where(
            values < self.x[0],
            self.y[0] + (values - self.x[0]) * self.left_slope,
            np.where(
                values > self.x[-1],
                self.y[-1] + (values - self.x[-1]) * self.right_slope,
                inside,
            ),
        )


@lru_cache(maxsize=64)
def biex_functions(negative, width, positive, top, channel_range=256):
    x, y = lookup(negative, width, positive, top, channel_range)
    return NaturalLinearSpline(x, y), NaturalLinearSpline(y, x)
