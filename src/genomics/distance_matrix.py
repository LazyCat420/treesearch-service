"""
Distance matrix computation — ported from kannapedia-scraper.

Creates NxN genetic distance matrices from relationship data,
suitable for MDS, PCA, heatmaps, and phylogenetic tree layouts.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from src.genomics.data_loader import StrainDataDict, RelationshipSet

logger = logging.getLogger(__name__)


def create_distance_matrix(
    strains_data: StrainDataDict,
    all_relationships: RelationshipSet,
    default_distance: float = 1.0,
) -> tuple[np.ndarray, list[str]]:
    """Create a symmetric distance matrix from known pairwise relationships.

    Args:
        strains_data: Dict of strain info (from data_loader).
        all_relationships: Set of (strain_a, strain_b, distance) tuples.
        default_distance: Value for unknown pairs.

    Returns:
        Tuple of (NxN numpy array, list of strain names in row/col order).
    """
    all_names: set[str] = set(strains_data.keys())
    for s1, s2, _ in all_relationships:
        all_names.add(s1)
        all_names.add(s2)

    strain_names = sorted(all_names)
    n = len(strain_names)

    distances = np.full((n, n), default_distance, dtype=np.float64)
    np.fill_diagonal(distances, 0.0)

    name_to_idx = {name: i for i, name in enumerate(strain_names)}

    filled = 0
    for s1, s2, dist in all_relationships:
        i = name_to_idx[s1]
        j = name_to_idx[s2]
        distances[i, j] = dist
        distances[j, i] = dist
        filled += 1

    logger.info("Created %dx%d distance matrix (%d known pairs)", n, n, filled)
    return distances, strain_names


def get_nearest_neighbors(
    distances: np.ndarray,
    strain_names: list[str],
    target_strain: str,
    k: int = 10,
    max_distance: float = 1.0,
) -> list[dict[str, Any]]:
    """Find the k nearest neighbors for a given strain."""
    if target_strain not in strain_names:
        return []

    idx = strain_names.index(target_strain)
    row = distances[idx]
    sorted_indices = np.argsort(row)
    neighbors = []

    for rank, j in enumerate(sorted_indices):
        if j == idx:
            continue
        if row[j] >= max_distance:
            break
        if len(neighbors) >= k:
            break
        neighbors.append({
            "strain": strain_names[j],
            "distance": float(row[j]),
            "rank": rank,
        })

    return neighbors


def compute_mds_coordinates(
    distances: np.ndarray,
    strain_names: list[str],
    n_components: int = 2,
    random_state: int = 42,
) -> list[dict[str, Any]]:
    """Compute MDS coordinates from a distance matrix for scatter plots."""
    from sklearn.manifold import MDS

    mds = MDS(
        n_components=n_components,
        dissimilarity="precomputed",
        random_state=random_state,
        normalized_stress="auto",
    )
    coords = mds.fit_transform(distances)

    results = []
    for i, name in enumerate(strain_names):
        point: dict[str, Any] = {"strain": name, "x": float(coords[i, 0]), "y": float(coords[i, 1])}
        if n_components >= 3:
            point["z"] = float(coords[i, 2])
        results.append(point)

    return results
