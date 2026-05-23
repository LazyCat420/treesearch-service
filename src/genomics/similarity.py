"""
Combined similarity scoring — correlates genetics, terpenes, and phenotype.

This is the "brains" layer that the plan describes: for each canonical strain,
combine evidence from multiple data domains into a unified feature stack
and compute cross-domain similarity.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from src.genomics.data_loader import StrainDataDict, RelationshipSet
from src.genomics.distance_matrix import create_distance_matrix
from src.genomics.terpene_analysis import calculate_terpene_relationships

logger = logging.getLogger(__name__)


def compute_combined_similarity(
    strains_data: StrainDataDict,
    genetic_relationships: RelationshipSet,
    weights: dict[str, float] | None = None,
    max_results: int = 20,
) -> dict[str, list[dict[str, Any]]]:
    """Compute combined similarity scores across multiple data domains.

    For each strain, produces a ranked list of most-similar strains
    using a weighted combination of:
      - Genetic distance (from Kannapedia/WGS data)
      - Terpene profile similarity
      - (Future: phenotype text similarity, image similarity)

    Args:
        strains_data: Dict of strain info with terpene data.
        genetic_relationships: Set of (strain_a, strain_b, distance).
        weights: Domain weights, defaults to {'genetic': 0.6, 'terpene': 0.4}.
        max_results: Max neighbors per strain.

    Returns:
        Dict of {strain_name: [{'strain': ..., 'combined_distance': ..., 'genetic_distance': ..., 'terpene_distance': ...}]}
    """
    if weights is None:
        weights = {"genetic": 0.6, "terpene": 0.4}

    # Build genetic distance matrix
    gen_matrix, gen_names = create_distance_matrix(strains_data, genetic_relationships)
    gen_idx = {name: i for i, name in enumerate(gen_names)}

    # Build terpene relationship lookup
    terpene_rels = calculate_terpene_relationships(strains_data)
    terp_lookup: dict[tuple[str, str], float] = {}
    for rel in terpene_rels:
        key = tuple(sorted([rel["from"], rel["to"]]))
        terp_lookup[key] = rel["distance"]

    # Compute combined scores
    results: dict[str, list[dict[str, Any]]] = {}

    for strain in gen_names:
        if strain not in strains_data:
            continue
        if not strains_data[strain].get("complete", False):
            continue

        neighbors: list[dict[str, Any]] = []
        strain_idx = gen_idx[strain]

        for other in gen_names:
            if other == strain:
                continue

            other_idx = gen_idx[other]
            gen_dist = float(gen_matrix[strain_idx, other_idx])

            # Look up terpene distance
            terp_key = tuple(sorted([strain, other]))
            terp_dist = terp_lookup.get(terp_key, 1.0)

            # Weighted combination
            combined = (
                weights.get("genetic", 0.6) * gen_dist
                + weights.get("terpene", 0.4) * terp_dist
            )

            neighbors.append({
                "strain": other,
                "combined_distance": round(combined, 4),
                "genetic_distance": round(gen_dist, 4),
                "terpene_distance": round(terp_dist, 4),
            })

        # Sort by combined distance and take top N
        neighbors.sort(key=lambda x: x["combined_distance"])
        results[strain] = neighbors[:max_results]

    logger.info("Computed combined similarity for %d strains", len(results))
    return results
