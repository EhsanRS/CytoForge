"""PhenoGraph nearest-neighbour/Jaccard graph with seeded Louvain communities.

The graph follows Levine et al. and the maintained PhenoGraph kernel. Community
optimization uses igraph's Louvain implementation; it does not claim identical
partitions to another optimizer or biological validation of a community label.
"""

from __future__ import annotations

import random
import sys
import threading

import numpy as np
from numba import njit
from scipy import sparse
from sklearn.neighbors import NearestNeighbors

_rng_lock = threading.Lock()


@njit(cache=not getattr(sys, "frozen", False))
def _jaccard_weights(neighbors):
    count, width = neighbors.shape
    weights = np.zeros((count, width), dtype=np.float64)
    for row in range(count):
        for column in range(width):
            other = neighbors[row, column]
            left, right, intersection = 0, 0, 0
            while left < width and right < width:
                a, b = neighbors[row, left], neighbors[other, right]
                if a == b:
                    intersection += 1
                    left += 1
                    right += 1
                elif a < b:
                    left += 1
                else:
                    right += 1
            weights[row, column] = intersection / (2 * width - intersection)
    return weights


def neighbor_graph(neighbors):
    """Average the directed Jaccard graph with its transpose, keeping zero diagonals."""
    neighbors = np.asarray(neighbors)
    if (
        neighbors.ndim != 2
        or not np.issubdtype(neighbors.dtype, np.integer)
        or not 1 <= neighbors.shape[1] < neighbors.shape[0]
        or np.any(neighbors < 0)
        or np.any(neighbors >= neighbors.shape[0])
    ):
        raise ValueError("Each cell needs a valid, uniformly sized nearest-neighbour set")
    ordered = np.sort(neighbors.astype(np.int32, copy=False), axis=1)
    if np.any(ordered[:, 1:] == ordered[:, :-1]) or np.any(
        ordered == np.arange(len(ordered))[:, None]
    ):
        raise ValueError("Neighbour sets must be unique and exclude the cell itself")
    weights = _jaccard_weights(np.ascontiguousarray(ordered))
    rows = np.repeat(np.arange(len(ordered), dtype=np.int32), ordered.shape[1])
    directed = sparse.csr_matrix(
        (weights.ravel(), (rows, ordered.ravel())), shape=(len(ordered), len(ordered))
    )
    graph = (directed + directed.T) * 0.5
    graph.eliminate_zeros()
    graph.sort_indices()
    return graph


def fit(values, *, neighbors=30, min_cluster_size=10, resolution=1.0, seed=42, restarts=5):
    """Fit the graph's cells only; no out-of-sample class prediction is performed."""
    import igraph

    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 2 or len(values) < 3 or not values.shape[1] or not np.isfinite(values).all():
        raise ValueError("PhenoGraph requires a finite two-dimensional cell matrix")
    if not 2 <= neighbors < len(values):
        raise ValueError("PhenoGraph neighbours must be less than the fitted event count")
    if not 2 <= min_cluster_size <= 100000 or not 1 <= restarts <= 20:
        raise ValueError("Choose valid minimum community size and Louvain restart count")
    if not np.isfinite(resolution) or not 0 < resolution <= 100:
        raise ValueError("Louvain resolution must be positive and at most 100")
    model = NearestNeighbors(n_neighbors=neighbors + 1, metric="euclidean", n_jobs=1).fit(values)
    candidates = model.kneighbors(values, return_distance=False)
    # Identical events can place self anywhere in the returned list, or omit it.
    identifiers = np.empty((len(values), neighbors), dtype=np.int32)
    for row in range(len(values)):
        identifiers[row] = candidates[row][candidates[row] != row][:neighbors]
    graph = neighbor_graph(identifiers)
    upper = sparse.triu(graph, k=1, format="coo")
    native = igraph.Graph(
        n=len(values), edges=np.column_stack([upper.row, upper.col]).tolist(), directed=False
    )
    native.es["weight"] = upper.data.tolist()
    best, modularities = None, []
    if native.ecount():
        # Analysis jobs are isolated processes. Serialize this process's RNG
        # assignment as well so direct numerical callers cannot race one another.
        with _rng_lock:
            try:
                for attempt in range(restarts):
                    igraph.set_random_number_generator(random.Random(seed + attempt))
                    partition = native.community_multilevel(weights="weight", resolution=resolution)
                    score = float(partition.modularity)
                    modularities.append(score)
                    if best is None or score > best[0]:
                        best = score, [list(group) for group in partition]
            finally:
                igraph.set_random_number_generator(None)
        groups = best[1]
        modularity = best[0]
    else:
        groups = [[i] for i in range(len(values))]
        modularity = None
    accepted = sorted(
        (group for group in groups if len(group) >= min_cluster_size),
        key=lambda group: (-len(group), min(group)),
    )
    labels = np.zeros(len(values), dtype=np.int32)
    for identifier, group in enumerate(accepted, start=1):
        labels[group] = identifier
    diagnostics = dict(
        graph_kernel="Jaccard nearest-neighbour sets; average with transpose",
        distance="euclidean",
        community_optimizer="igraph Louvain",
        neighbors=neighbors,
        graph_nodes=len(values),
        graph_edges=native.ecount(),
        graph_isolated_cells=int(np.count_nonzero(np.diff(graph.indptr) == 0)),
        resolution=resolution,
        modularity=modularity,
        restart_modularities=modularities,
        min_cluster_size=min_cluster_size,
        community_sizes={str(i): len(group) for i, group in enumerate(accepted, start=1)},
        discarded_community_sizes=sorted(
            [len(group) for group in groups if len(group) < min_cluster_size], reverse=True
        ),
        unassigned_fitted_count=int(np.count_nonzero(labels == 0)),
        unassigned_label=0,
        mapping="Fitted events only; remaining events are undefined (NaN)",
        biological_classes_verified=False,
    )
    return labels, diagnostics
