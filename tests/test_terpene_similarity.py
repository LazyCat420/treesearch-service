import pytest
from src.genomics.terpene_analysis import calculate_terpene_relationships

def test_calculate_terpene_relationships_top_5():
    # Setup 7 mock strains with varying terpene profiles
    # Total terpene percentage needs to be >= 0.1
    strains_data = {
        "Strain_1": {"terpenes": {"myrcene": 0.5, "limonene": 0.5}},  # close to Strain_2, Strain_3
        "Strain_2": {"terpenes": {"myrcene": 0.45, "limonene": 0.45}},
        "Strain_3": {"terpenes": {"myrcene": 0.4, "limonene": 0.4}},
        "Strain_4": {"terpenes": {"myrcene": 0.1, "limonene": 0.9}},
        "Strain_5": {"terpenes": {"myrcene": 0.05, "limonene": 0.95}},
        "Strain_6": {"terpenes": {"caryophyllene": 1.0}},
        "Strain_7": {"terpenes": {"caryophyllene": 0.9, "myrcene": 0.1}},
    }

    # Run calculate_terpene_relationships with max_distance=0.1
    # This distance is low enough that only very close strains would match by default,
    # but the top 5 closest neighbors should be guaranteed to be connected.
    relationships = calculate_terpene_relationships(strains_data, min_total_terpenes=0.1, max_distance=0.1)

    # Let's count relationships for each strain
    connections = {name: [] for name in strains_data}
    for rel in relationships:
        connections[rel["from"]].append(rel)
        connections[rel["to"]].append(rel)

    # Verify that each strain has exactly 5 connections (since there are 6 other strains)
    # and that the connections are flagged with "is_top_5"
    for name, rels in connections.items():
        assert len(rels) >= 5, f"Strain {name} has only {len(rels)} connections, expected at least 5"
        top_5_rels = [r for r in rels if r.get("is_top_5") is True]
        assert len(top_5_rels) >= 5, f"Strain {name} is missing is_top_5 flag on some relationships"
        
        # Verify that the distance matches what's stored in relationships
        for r in rels:
            assert "distance" in r
            assert isinstance(r["distance"], float)
            assert 0.0 <= r["distance"] <= 1.0
