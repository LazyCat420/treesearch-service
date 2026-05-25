"""
Visualization server — FastAPI-based replacement for kannapedia-scraper's
SimpleHTTPRequestHandler.

Serves the strain network visualization, phylogenetic tree views,
and strain detail endpoints. Works against the unified DB models
instead of reading raw CSV files from disk.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from src.genomics.data_loader import (
    StrainDataDict,
    RelationshipSet,
    load_strain_data_from_samples,
)
from src.genomics.terpene_analysis import calculate_terpene_relationships

logger = logging.getLogger(__name__)

# Path to viz assets
VIZ_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(VIZ_DIR, "templates")
STATIC_DIR = os.path.join(VIZ_DIR, "static")


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
        nodes.append({
            "id": strain_name,
            "label": strain_name,
            "title": f"{strain_name}<br>RSP: {rsp}<br>{'Has full data' if is_complete else 'Incomplete'}",
            "color": {
                "background": "#2B7CE9" if is_complete else "#cccccc",
                "border": "#2B7CE9" if is_complete else "#666666",
            },
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


def render_visualization_html(
    network_data: dict[str, Any],
    template_name: str = "network_view.html",
) -> str:
    """Render the visualization HTML with embedded data.

    Reads the template and injects the network data as a JS object.

    Args:
        network_data: Output of build_network_data().
        template_name: Template filename.

    Returns:
        Complete HTML string ready to serve.
    """
    template_path = os.path.join(TEMPLATES_DIR, template_name)

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    data_script = f"""
        window.INITIAL_DATA = {{
            nodes: {json.dumps(network_data['nodes'])},
            relationships: {json.dumps(network_data['relationships'])},
            terpeneRelationships: {json.dumps(network_data['terpeneRelationships'])}
        }};
    """

    html_content = template.replace("{{DATA_INITIALIZATION}}", data_script)
    return html_content

