"""
Genomics analysis package — ported from kannapedia-scraper.

Provides data loading, distance matrix computation, terpene analysis,
and combined similarity scoring. All functions operate on the canonical
models rather than raw CSV files, making them source-agnostic.
"""

from src.genomics.data_loader import (
    load_strain_data_from_directory,
    load_strain_data_from_samples,
)
from src.genomics.distance_matrix import create_distance_matrix
from src.genomics.terpene_analysis import (
    calculate_terpene_relationships,
    PRIMARY_TERPENE_GROUPS,
)
from src.genomics.similarity import compute_combined_similarity

__all__ = [
    "load_strain_data_from_directory",
    "load_strain_data_from_samples",
    "create_distance_matrix",
    "calculate_terpene_relationships",
    "PRIMARY_TERPENE_GROUPS",
    "compute_combined_similarity",
]
