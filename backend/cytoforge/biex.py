"""FlowJo biexponential lookup-table implementation.

Adapted from FlowKit (Copyright 2018 Scott White), BSD-3-Clause.
The complete notice is distributed in licenses/FlowKit-BSD-3-Clause.txt.
Source: https://github.com/whitews/FlowKit/blob/master/src/flowkit/_models/transforms/_wsp_transforms.py
"""

from functools import lru_cache

import numpy as np
from scipy.interpolate import interp1d


def _log_root(b, w):
    x_lo = 0
    x_hi = b
    d = (x_lo + x_hi) / 2
    dx = abs(int(x_lo - x_hi))
    dx_last = dx
    fb = -2 * np.log(b) + w * b
    f = 2.0 * np.log(d) + w * b + fb
    df = 2 / d + w

    if w == 0:
        return b

    for _i in range(100):
        if (((d - x_hi) * df - f) - ((d - x_lo) * df - f)) > 0 or abs(2 * f) > abs(dx_last * df):
            dx = (x_hi - x_lo) / 2
            d = x_lo + dx
            if d == x_lo:
                return d
        else:
            dx = f / df
            t = d
            d -= dx
            if d == t:
                return d

        # if abs(int(dx)) < 1.0E-12:
        if abs(dx) < 1.0e-12:
            return d

        dx_last = dx
        f = 2 * np.log(d) + w * d + fb
        df = 2 / d + w
        if f < 0:
            x_lo = d
        else:
            x_hi = d

    return d


def generate_biex_lut(
    channel_range=4096, pos=4.418540, neg=0.0, width_basis=-10, max_value=262144.000029
):
    """
    Creates a FlowJo compatible biex lookup table.

    Implementation ported from the R library cytolib, which claims to be directly ported from the
    legacy Java code from TreeStar.

    :param channel_range: Maximum positive value of the output range
    :param pos: Number of decades
    :param neg: Number of extra negative decades
    :param width_basis: Controls the input range compressed in the zero / linear region.
        A higher
        width basis value will include more input values in the zero / linear region.
    :param max_value: maximum input value to scale
    :return: 2-column NumPy array of the LUT (column order: input, output)
    """
    ln10 = np.log(10.0)
    decades = pos
    low_scale = width_basis
    width = np.log10(-low_scale)

    decades = decades - (width / 2)

    extra = neg

    if extra < 0:
        extra = 0

    extra = extra + (width / 2)

    zero_point = int((extra * channel_range) / (extra + decades))
    zero_point = int(np.min([zero_point, channel_range / 2]))

    if zero_point > 0:
        decades = extra * channel_range / zero_point

    width = width / (2 * decades)

    maximum = max_value
    positive_range = ln10 * decades
    minimum = maximum / np.exp(positive_range)

    negative_range = _log_root(positive_range, width)

    max_channel_value = channel_range + 1
    n_points = max_channel_value

    step = (max_channel_value - 1) / (n_points - 1)

    values = np.arange(n_points)
    positive = np.exp(values / float(n_points) * positive_range)
    negative = np.exp(values / float(n_points) * -negative_range)

    # apply step to values
    values = values * step

    s = np.exp((positive_range + negative_range) * (width + extra / decades))

    negative *= s
    s = positive[zero_point] - negative[zero_point]

    positive[zero_point:n_points] = positive[zero_point:n_points] - negative[zero_point:n_points]
    positive[zero_point:n_points] = minimum * (positive[zero_point:n_points] - s)

    neg_range = np.arange(zero_point)
    m = 2 * zero_point - neg_range

    positive[neg_range] = -positive[m]

    return positive, values


@lru_cache(maxsize=64)
def biex_functions(negative, width, positive, top):
    x, y = generate_biex_lut(neg=negative, width_basis=width, pos=positive, max_value=top)
    if not np.all(np.isfinite(x)) or np.any(np.diff(x) <= 0):
        raise ValueError("Biex parameters do not produce a finite monotonic lookup table")
    return (
        interp1d(x, y, bounds_error=False, fill_value=(y.min(), y.max())),
        interp1d(y, x, bounds_error=False, fill_value=(x.min(), x.max())),
    )
