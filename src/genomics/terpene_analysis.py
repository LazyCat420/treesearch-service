"""
Terpene analysis — ported from kannapedia-scraper's calculate_terpene_relationships().

Computes similarity relationships between strains based on their
terpene profiles, normalizing variant names into primary groups.
"""

from __future__ import annotations

import logging
from typing import Any

from src.genomics.data_loader import StrainDataDict

logger = logging.getLogger(__name__)

# Primary terpene groups — maps variant names to canonical groups
PRIMARY_TERPENE_GROUPS: dict[str, list[str]] = {
    "myrcene": ["myrcene"],
    "limonene": ["limonene", "d-limonene"],
    "caryophyllene": ["caryophyllene", "β-caryophyllene", "beta-caryophyllene"],
    "pinene": ["α-pinene", "beta-pinene", "α-pinene", "alpha-pinene"],
    "terpinolene": ["terpinolene"],
    "linalool": ["linalool"],
    "humulene": ["humulene", "α-humulene", "alpha-humulene"],
    "ocimene": ["ocimene"],
    "nerolidol": ["nerolidol"],
    "bisabolol": ["bisabolol", "α-bisabolol", "alpha-bisabolol"],
}


def normalize_terpene_profile(
    raw_terpenes: dict[str, float],
    groups: dict[str, list[str]] | None = None,
) -> dict[str, float]:
    """Normalize raw terpene names into primary groups, summing variants.

    Args:
        raw_terpenes: Dict of {raw_name: value} (values in %).
        groups: Terpene group mapping (defaults to PRIMARY_TERPENE_GROUPS).

    Returns:
        Dict of {group_name: summed_value}.
    """
    if groups is None:
        groups = PRIMARY_TERPENE_GROUPS

    normalized: dict[str, float] = {}
    for raw_name, value in raw_terpenes.items():
        name_lower = raw_name.lower()
        if isinstance(value, str):
            value = float(value.strip("%"))

        matched = False
        for primary, variants in groups.items():
            if any(variant in name_lower for variant in variants):
                normalized[primary] = normalized.get(primary, 0.0) + value
                matched = True
                break

        if not matched:
            normalized[raw_name.lower()] = normalized.get(raw_name.lower(), 0.0) + value

    return normalized


def calculate_terpene_relationships(
    strains_data: StrainDataDict,
    min_total_terpenes: float = 0.1,
    max_distance: float = 0.5,
) -> list[dict[str, Any]]:
    """Calculate pairwise similarity between strains based on terpene profiles.

    Ported from kannapedia-scraper. Uses weighted cosine-like similarity
    across primary terpene groups.

    Args:
        strains_data: Dict of strain info (must include 'terpenes' key for relevant strains).
        min_total_terpenes: Minimum total terpene % to include a strain.
        max_distance: Maximum distance threshold to emit a relationship.

    Returns:
        List of relationship dicts with 'from', 'to', 'distance'.
    """
    # Build normalized profiles for strains with data
    profiles: dict[str, dict[str, float]] = {}
    for name, data in strains_data.items():
        if not data.get("terpenes"):
            continue

        normalized = normalize_terpene_profile(data["terpenes"])
        total = sum(normalized.values())

        if total >= min_total_terpenes:
            profiles[name] = normalized

    logger.info("Computing terpene relationships for %d strains with profiles", len(profiles))

    # Pairwise similarity
    relationships: list[dict[str, Any]] = []
    strain_names = sorted(profiles.keys())
    
    # Track the single closest neighbor for each strain to guarantee at least one connection
    closest_neighbors: dict[str, tuple[str, float]] = {}

    for i, s1 in enumerate(strain_names):
        t1 = profiles[s1]
        for s2 in strain_names[i + 1:]:
            t2 = profiles[s2]

            distance = _terpene_distance(t1, t2)
            
            # Update closest neighbor for s1
            if s1 not in closest_neighbors or distance < closest_neighbors[s1][1]:
                closest_neighbors[s1] = (s2, distance)
            # Update closest neighbor for s2
            if s2 not in closest_neighbors or distance < closest_neighbors[s2][1]:
                closest_neighbors[s2] = (s1, distance)

            if distance < max_distance:
                relationships.append({
                    "from": s1,
                    "to": s2,
                    "distance": distance,
                })

    # Ensure every strain has its closest terpene neighbor connected
    added_pairs = {tuple(sorted([r["from"], r["to"]])) for r in relationships}
    for s1, (s2, distance) in closest_neighbors.items():
        pair = tuple(sorted([s1, s2]))
        if pair not in added_pairs:
            relationships.append({
                "from": s1,
                "to": s2,
                "distance": distance,
            })
            added_pairs.add(pair)

    logger.info("Found %d terpene relationships (including guaranteed closest neighbors)", len(relationships))
    return relationships


def _terpene_distance(
    t1: dict[str, float],
    t2: dict[str, float],
    trace_threshold: float = 0.1,
) -> float:
    """Compute weighted distance between two terpene profiles.

    Uses the same algorithm as kannapedia-scraper: for each shared terpene,
    compute 1 - (diff / max_val), weighted by the maximum concentration.
    """
    all_terpenes = set(t1.keys()) | set(t2.keys())

    similarity_score = 0.0
    total_weight = 0.0

    for terpene in all_terpenes:
        val1 = t1.get(terpene, 0.0)
        val2 = t2.get(terpene, 0.0)

        if max(val1, val2) < trace_threshold:
            continue

        diff = abs(val1 - val2)
        max_val = max(val1, val2, 0.1)
        terpene_similarity = 1.0 - (diff / max_val)

        weight = max(val1, val2)
        similarity_score += terpene_similarity * weight
        total_weight += weight

    if total_weight <= 0:
        return 1.0

    return 1.0 - (similarity_score / total_weight)
