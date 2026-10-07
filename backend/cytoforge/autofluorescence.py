"""Review distinct AF reference directions in weighted spectral detector space."""

import math

import numpy as np


def reference_review(matrix, names):
    """Measure each AF direction's separation from the span of all other sources.

    This is a geometric identifiability diagnostic, not biological classification
    or an estimate of the proportion of AF in an experimental cell.
    """
    if not names:
        return [], []
    if matrix.kind != "spectral" or not set(names) <= set(matrix.outputs):
        raise ValueError("AF references must be named spectral outputs")
    values = np.asarray(matrix.matrix, dtype=float)
    weights = np.asarray(matrix.weights or [1.0] * values.shape[1], dtype=float)
    roots = np.sqrt(weights)
    roots /= roots.max()
    # Scaling each row preserves its direction while avoiding squared overflow.
    scaled = values / np.max(np.abs(values), axis=1)[:, None]
    weighted = scaled * roots
    weighted /= np.max(np.abs(weighted), axis=1)[:, None]
    directions = weighted / np.linalg.norm(weighted, axis=1)[:, None]
    rows, warnings = [], []
    for name in names:
        index = matrix.outputs.index(name)
        other_indices = [i for i in range(len(matrix.outputs)) if i != index]
        vector = directions[index]
        if other_indices:
            others = directions[other_indices]
            _, singular, basis = np.linalg.svd(others, full_matrices=False)
            rank = int(
                np.count_nonzero(singular > singular[0] * max(others.shape) * np.finfo(float).eps)
            )
            residual = vector - (vector @ basis[:rank].T) @ basis[:rank]
            fraction = float(np.clip(np.linalg.norm(residual), 0, 1))
            cosines = np.clip(others @ vector, -1, 1)
            closest = int(np.argmax(np.abs(cosines)))
            closest_name = matrix.outputs[other_indices[closest]]
            similarity = float(cosines[closest])
        else:
            fraction, closest_name, similarity = 1.0, None, None
        angle = math.degrees(math.asin(fraction))
        review = dict(
            output=name,
            closest_output=closest_name,
            weighted_cosine=similarity,
            orthogonal_fraction=fraction,
            separation_angle_degrees=angle,
            weakly_separated=fraction < 0.1,
        )
        rows.append(review)
        if review["weakly_separated"]:
            warnings.append(
                f"{name}: AF reference is only {angle:.3g}° from the span of all other sources; "
                "small spectral differences or detector noise may destabilize extraction"
            )
    return rows, warnings
