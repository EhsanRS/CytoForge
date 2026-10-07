"""Unbounded linked angular partitions and exact viewport clipping of their arms."""

from __future__ import annotations

import math
from fractions import Fraction

import numpy as np

TAU = 2 * math.pi
SECTORS = (2, 1, 4, 3)  # Counterclockwise from the first arm.
BOUNDARY_OWNERS = (2, 2, 1, 3)  # Match ordinary quadrants at cardinal angles.
MEMBER_ARMS = {1: (1, 2), 2: (0, 1), 3: (3, 0), 4: (2, 3)}


def direction(angle):
    cardinal = {
        0.0: (1.0, 0.0),
        math.pi / 2: (0.0, 1.0),
        math.pi: (-1.0, 0.0),
        3 * math.pi / 2: (0.0, -1.0),
    }
    return cardinal.get(angle, (math.cos(angle), math.sin(angle)))


def delta_parts(values, center):
    """Represent finite coordinate differences without overflowing their exponent."""
    with np.errstate(over="ignore", invalid="ignore"):
        delta = values - center
    overflow = np.isinf(delta) & np.isfinite(values)
    if overflow.any():
        delta = delta.copy()
        delta[overflow] = values[overflow] / 2 - center / 2
    mantissa, exponent = np.frexp(delta)
    exponent = exponent.astype(np.int32) + overflow
    return mantissa, exponent


def product_parts(parts, coefficient, scale):
    mantissa, exponent = parts
    cm, ce = math.frexp(coefficient)
    sm, se = math.frexp(scale)
    return mantissa * cm / sm, exponent + ce - se


def difference_sign(left, right):
    """Align exponents before subtraction, including single subnormal terms."""
    lm, le = left
    rm, re = right
    exponent = np.maximum(np.where(lm != 0, le, -10000), np.where(rm != 0, re, -10000))
    with np.errstate(under="ignore", invalid="ignore"):
        aligned_left = np.ldexp(lm, le - exponent)
        aligned_right = np.ldexp(rm, re - exponent)
        difference = aligned_left - aligned_right
    uncertain = (
        (lm != 0)
        & (rm != 0)
        & (
            np.abs(difference)
            <= 64 * np.finfo(float).eps * (np.abs(aligned_left) + np.abs(aligned_right))
        )
    )
    return np.sign(difference).astype(np.int8), uncertain


def labels(x, y, geometry):
    """One label per finite point; shared ray ownership is independent of gate order."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    finite = np.isfinite(x) & np.isfinite(y)
    result = np.zeros(len(x), dtype=np.uint8)
    if not finite.any():
        return result
    x, y = x[finite], y[finite]
    cx, cy = geometry.center
    sx, sy = geometry.scale
    dx, dy = delta_parts(x, cx), delta_parts(y, cy)
    crosses = []
    for angle in geometry.angles:
        cosine, sine = direction(angle)
        signs, uncertain = difference_sign(
            product_parts(dy, cosine, sy), product_parts(dx, sine, sx)
        )
        if uncertain.any():
            # Resolve cancellation using the exact rational values of the saved
            # float coefficients and original coordinates. No angular epsilon
            # converts nearby events into boundary events.
            a, b = Fraction(cosine) * Fraction(sx), Fraction(sine) * Fraction(sy)
            ox, oy = Fraction(cx), Fraction(cy)
            for index in np.flatnonzero(uncertain):
                cross = (Fraction(float(y[index])) - oy) * a - (Fraction(float(x[index])) - ox) * b
                signs[index] = (cross > 0) - (cross < 0)
        crosses.append(signs)
    selected = np.zeros(len(x), dtype=np.uint8)
    for index, member in enumerate(SECTORS):
        following = (index + 1) % 4
        width = (geometry.angles[following] - geometry.angles[index]) % TAU
        included = (
            ((crosses[index] > 0) & (crosses[following] < 0))
            if width < math.pi
            else ((crosses[index] > 0) | (crosses[following] < 0))
        )
        selected[included] = member
    for index, owner in enumerate(BOUNDARY_OWNERS):
        cosine, sine = direction(geometry.angles[index])
        forward = ((x > cx) if cosine > 0 else (x < cx) if cosine < 0 else False) | (
            (y > cy) if sine > 0 else (y < cy) if sine < 0 else False
        )
        selected[(crosses[index] == 0) & forward] = owner
    selected[(x == cx) & (y == cy)] = 2
    if np.any(selected == 0):
        raise ValueError("Spider directions cannot resolve this coordinate range")
    result[finite] = selected
    return result


def clip_arm(geometry, bounds, index):
    """Clip a ray with rational float coefficients, even at opposite float extremes."""
    origin = [Fraction(v) for v in geometry.center]
    vector = [
        Fraction(s) * Fraction(d)
        for s, d in zip(geometry.scale, direction(geometry.angles[index]), strict=True)
    ]
    enter, leave = Fraction(0), None
    for axis in range(2):
        low, high = (Fraction(v) for v in bounds[axis * 2 : axis * 2 + 2])
        if not vector[axis]:
            if not low <= origin[axis] <= high:
                return None
            continue
        first, last = sorted(
            ((low - origin[axis]) / vector[axis], (high - origin[axis]) / vector[axis])
        )
        enter = max(enter, first)
        leave = min(leave, last) if leave is not None else last
    if leave is None or leave < enter:
        return None
    return [[float(o + t * v) for o, v in zip(origin, vector, strict=True)] for t in (enter, leave)]
