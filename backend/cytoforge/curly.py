"""Unbounded square-root detection limits, shared by four curved populations."""

from fractions import Fraction

import numpy as np

from .spider import delta_parts, difference_sign, product_parts


def raw_center(gate):
    from .science import transform

    values = tuple(
        float(transform(np.asarray([value]), dimension.transform, inverse=True)[0])
        for value, dimension in zip(gate.curly.center, gate.dimensions, strict=True)
    )
    if not all(np.isfinite(values)):
        raise ValueError(
            "The curly center cannot be represented as finite fluorescence intensities"
        )
    return values


def root_difference(signal, center):
    """Stable sqrt(max(signal, 0)) - sqrt(max(center, 0)), on the positive arm."""
    positive = np.maximum(np.asarray(signal), max(center, 0))
    baseline = max(center, 0)
    roots = np.sqrt(positive) + np.sqrt(baseline)
    return np.divide(positive - baseline, roots, out=np.zeros_like(positive), where=roots > 0)


def exact_positive(signal, ordinate, center_signal, center_ordinate, coefficient):
    """An exact rational comparison with the real square root, without taking it."""
    displacement = Fraction(ordinate) - Fraction(center_ordinate)
    if displacement < 0:
        return False
    signal = max(Fraction(signal), Fraction(center_signal), Fraction(0))
    baseline = max(Fraction(center_signal), Fraction(0))
    if not coefficient or signal == baseline:
        return True
    quotient = displacement / Fraction(coefficient)
    remainder = signal - baseline - quotient * quotient
    return remainder <= 0 or 4 * quotient * quotient * baseline >= remainder * remainder


def positive(signal, ordinate, center_signal, center_ordinate, coefficient):
    root = root_difference(signal, center_signal)
    signs, uncertain = difference_sign(
        delta_parts(ordinate, center_ordinate),
        product_parts(np.frexp(root), coefficient, 1),
    )
    for index in np.flatnonzero(uncertain):
        signs[index] = (
            1
            if exact_positive(
                float(signal[index]),
                float(ordinate[index]),
                center_signal,
                center_ordinate,
                coefficient,
            )
            else -1
        )
    return signs >= 0


def labels(x, y, center, coefficients):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    finite = np.isfinite(x) & np.isfinite(y)
    result = np.zeros(len(x), dtype=np.uint8)
    if finite.any():
        x, y = x[finite], y[finite]
        cx, cy = center
        ax, ay = coefficients
        x_positive = positive(y, x, cy, cx, ax)
        y_positive = positive(x, y, cx, cy, ay)
        result[finite] = np.where(
            y_positive, np.where(x_positive, 2, 1), np.where(x_positive, 3, 4)
        )
    return result


def classify_gate(gate, raw_columns):
    from .science import transform

    classification = labels(*raw_columns, raw_center(gate), gate.curly.coefficients)
    for values, dimension in zip(raw_columns, gate.dimensions, strict=True):
        classification[~np.isfinite(transform(values, dimension.transform))] = 0
    return classification


def boundaries(gate, native_limits, positions=129):
    """Sample all four boundaries in saved plot coordinates; counts never use these paths."""
    from .science import transform

    center = raw_center(gate)
    cx, cy = center
    ax, ay = gate.curly.coefficients
    result = []
    # Right/up curves and left/down straight arms, counterclockwise.
    for arm, axis in enumerate([0, 1, 0, 1]):
        low, high = native_limits[2 * axis : 2 * axis + 2]
        start = max(low, gate.curly.center[axis]) if arm < 2 else low
        end = high if arm < 2 else min(high, gate.curly.center[axis])
        if start > end:
            result.append([])
            continue
        weight = np.linspace(0, 1, positions)
        native = (1 - weight) * start + weight * end
        raw = transform(native, gate.dimensions[axis].transform, inverse=True)
        with np.errstate(over="ignore", invalid="ignore"):
            other = (
                (cy + ay * root_difference(raw, cx))
                if axis == 0
                else (cx + ax * root_difference(raw, cy))
            )
        if arm >= 2:
            other = np.full_like(raw, cy if axis == 0 else cx)
        projected = transform(other, gate.dimensions[1 - axis].transform)
        points = (
            np.column_stack((native, projected))
            if axis == 0
            else np.column_stack((projected, native))
        )
        if not np.isfinite(points).all():
            raise ValueError("The curly boundary exceeds this plot's finite display range")
        result.append(points.tolist())
    return result


def member_boundaries(gate, curves):
    """Assign sampled divider pieces to their adjacent populations, including crossings."""
    from .science import transform

    cx, cy = raw_center(gate)
    ax, ay = gate.curly.coefficients
    result = {member: [] for member in range(1, 5)}
    for arm, curve in enumerate(curves):
        if len(curve) < 2:
            continue
        axis = arm % 2
        values = np.asarray(curve)
        midpoints = values[:-1, axis] / 2 + values[1:, axis] / 2
        signal = transform(midpoints, gate.dimensions[axis].transform, inverse=True)
        other = (
            cy + ay * root_difference(signal, cx)
            if axis == 0
            else cx + ax * root_difference(signal, cy)
        )
        if arm >= 2:
            other = np.full_like(signal, cy if axis == 0 else cx)
        side = (
            positive(other, signal, cy, cx, ax)
            if axis == 0
            else positive(other, signal, cx, cy, ay)
        )
        changes = [0, *(np.flatnonzero(side[1:] != side[:-1]) + 1), len(side)]
        for start, end in zip(changes[:-1], changes[1:], strict=True):
            adjacent = (
                ((2, 3) if side[start] else (1, 4))
                if axis == 0
                else ((2, 1) if side[start] else (3, 4))
            )
            for member in adjacent:
                result[member].append(curve[start : end + 1])
    return result
