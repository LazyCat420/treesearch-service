"""Builds the strain-network payload for GET /api/network-data.

This is a pure data shaper: it deduplicates strains by RSP number and turns
relationships into edges. It emits DOMAIN data only — no colours, no HTML, no vis.js
styling. The client owns presentation.

(This module used to also carry a render_visualization_html() that injected the payload
into an HTML template from a src/viz/templates/ directory. That directory does not
exist and nothing called the function, so it was removed.)
"""

from __future__ import annotations

import logging
from typing import Any

from src.genomics.data_loader import (
    StrainDataDict,
    RelationshipSet,
)
from src.genomics.terpene_analysis import calculate_terpene_relationships

logger = logging.getLogger(__name__)


def build_network_data(
    strains_data: StrainDataDict,
    all_relationships: RelationshipSet,
    terpene_relationships: list[dict[str, Any]] | None = None,
    lineage_relationships: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the JSON payload for the network visualization.

    Deduplicates nodes by RSP number, creates edges from relationships,
    and packages everything for the Vis.js frontend.

    Returns:
        Dict with 'nodes', 'relationships', 'terpeneRelationships',
        'lineageRelationships' keys.
    """
    if terpene_relationships is None:
        terpene_relationships = calculate_terpene_relationships(strains_data)
    if lineage_relationships is None:
        lineage_relationships = []

    nodes = []
    relationships_list = []

    # Deduplicate by RSP
    seen_rsp: dict[str, dict[str, Any]] = {}
    for strain_name, data in strains_data.items():
        rsp = data.get("rsp", "").upper()
        if rsp:
            if rsp not in seen_rsp:
                seen_rsp[rsp] = {"name": strain_name, "complete": data.get("complete", False)}
            elif data.get("complete", False) and not seen_rsp[rsp]["complete"]:
                seen_rsp[rsp] = {"name": strain_name, "complete": True}

    # Build nodes
    for strain_name, data in strains_data.items():
        rsp = data.get("rsp", "").upper()
        if rsp and seen_rsp.get(rsp, {}).get("name") != strain_name:
            continue

        is_complete = data.get("complete", False)
        # Domain fields only. This deliberately does NOT emit vis.js `color` or an HTML
        # `title` tooltip: presentation belongs to the client, which already computes its
        # own palette and overwrote these anyway. Worse, phylogenetic_tree.js used to
        # *filter* on the hex values emitted here, so the colours were load-bearing
        # business logic living in the wrong tier.
        nodes.append({
            "id": strain_name,
            "label": strain_name,
            "rsp": rsp,
            "complete": is_complete,
            "source": data.get("source", "kannapedia"),
        })

    # Build edges
    seen_edges = set()
    for s1, s2, distance in all_relationships:
        rsp1 = strains_data.get(s1, {}).get("rsp", "").upper()
        rsp2 = strains_data.get(s2, {}).get("rsp", "").upper()

        from_name = seen_rsp.get(rsp1, {}).get("name", s1) if rsp1 else s1
        to_name = seen_rsp.get(rsp2, {}).get("name", s2) if rsp2 else s2

        if from_name == to_name:
            continue

        edge_key = tuple(sorted([from_name, to_name]))
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)

        relationships_list.append({
            "from": from_name,
            "to": to_name,
            "distance": distance,
        })

    return {
        "nodes": nodes,
        "relationships": relationships_list,
        "terpeneRelationships": terpene_relationships,
        "lineageRelationships": lineage_relationships,
    }


