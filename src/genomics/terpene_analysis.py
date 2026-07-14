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

        # Skip profiles that were copied from a relative rather than measured.
        #
        # main.py propagates a neighbour's terpene profile onto strains that have none, so
        # the graph can still show them. That is fine for display, but it is circular as a
        # correlation input: two strains that inherit from the same relative end up with
        # byte-identical profiles, so they score distance 0 — "perfectly correlated" — on
        # the strength of data neither of them has. Correlate only measured profiles.
        if data.get("terpenes_inherited_from"):
            continue

        # NOTE: this deliberately does NOT gate on `complete`. A strain qualifies by having
        # a measured terpene profile, full stop. Gating on is_complete tied the terpene
        # graph to whether a strain had a *genomic* lab assay, which excluded Leafly — the
        # source of almost every terpene profile we hold — and left the graph nearly empty.
        raw = normalize_terpene_profile(data["terpenes"])
        total = sum(raw.values())
        if total < min_total_terpenes:
            continue

        # Compare COMPOSITION, not magnitude. Kannapedia reports terpenes as mass percent
        # of the flower; Leafly reports a relative prominence score. Those are different
        # units, and _terpene_distance() weights by absolute concentration — so comparing
        # them raw makes the distance depend on which source a strain came from. Scaling
        # each profile to fractions of its own total makes the two directly comparable:
        # "60% myrcene, 30% limonene" means the same thing whatever the source.
        profiles[name] = {k: v / total for k, v in raw.items()}

    logger.info("Computing terpene relationships for %d strains with profiles", len(profiles))

    # Pairwise similarity
    relationships: list[dict[str, Any]] = []
    strain_names = sorted(profiles.keys())
    
    # Store all distances for each strain to find top 5 closest
    all_distances: dict[str, list[tuple[str, float]]] = {s: [] for s in strain_names}

    for i, s1 in enumerate(strain_names):
        t1 = profiles[s1]
        for s2 in strain_names[i + 1:]:
            t2 = profiles[s2]

            distance = _terpene_distance(t1, t2)
            
            all_distances[s1].append((s2, distance))
            all_distances[s2].append((s1, distance))

            if distance < max_distance:
                relationships.append({
                    "from": s1,
                    "to": s2,
                    "distance": distance,
                })

    # Ensure every strain has its top 5 closest terpene neighbors connected
    rel_map = {}
    for rel in relationships:
        key = tuple(sorted([rel["from"], rel["to"]]))
        rel_map[key] = rel

    for s1 in strain_names:
        # Sort neighbors by distance
        neighbors = sorted(all_distances[s1], key=lambda x: x[1])
        top_5 = neighbors[:5]
        for s2, distance in top_5:
            key = tuple(sorted([s1, s2]))
            if key in rel_map:
                rel_map[key]["is_top_5"] = True
            else:
                new_rel = {
                    "from": s1,
                    "to": s2,
                    "distance": distance,
                    "is_top_5": True,
                }
                relationships.append(new_rel)
                rel_map[key] = new_rel

    logger.info("Found %d terpene relationships (including guaranteed top 5 neighbors)", len(relationships))
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
