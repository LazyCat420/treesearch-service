"""
Data loader — reads strain/genomic data from CSV directories or from
GenomicSample model objects.

Ported from kannapedia-scraper's `load_strain_data()` in visualize_genetics.py.
Two modes:
  1. load_strain_data_from_directory() — reads the CSV file tree (legacy compat)
  2. load_strain_data_from_samples() — builds from GenomicSample model objects (DB-backed)
"""

from __future__ import annotations

import csv
import os
import re
import logging
from typing import Any

from src.models.genomic_sample import (
    GenomicSample,
    ChemicalProfile,
    GeneticRelationship,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Types used across genomics modules
# --------------------------------------------------------------------------- #

StrainDataDict = dict[str, dict[str, Any]]
"""{ strain_name: { 'complete': bool, 'rsp': str, 'dir_name': str, 'terpenes': {...} } }"""

RelationshipSet = set[tuple[str, str, float]]
"""Set of (strain_a, strain_b, distance) tuples"""


# --------------------------------------------------------------------------- #
# 1. Load from CSV directory tree (legacy / migration path)
# --------------------------------------------------------------------------- #

def load_strain_data_from_directory(folder_path: str) -> tuple[StrainDataDict, RelationshipSet]:
    """Load genetic relationship data from all strain folders.

    Walks the directory tree looking for metadata/chemicals/variants CSVs
    in the format produced by kaana_scraper.py.

    Args:
        folder_path: Root directory containing strain subdirectories.

    Returns:
        Tuple of (strains_data dict, all_relationships set).
    """
    strains_data: StrainDataDict = {}
    all_relationships: RelationshipSet = set()

    for root, dirs, files in os.walk(folder_path):
        for dir_name in dirs:
            if dir_name.startswith("."):
                continue

            # Clean strain name by removing extra spaces
            strain_name = " ".join(dir_name.split("-")[0].strip().split())

            # Construct expected file paths
            safe_name = strain_name.replace(" ", "_")
            metadata_file = os.path.join(root, dir_name, f"{safe_name}.metadata.csv")
            chemicals_file = os.path.join(root, dir_name, f"{safe_name}.chemicals.csv")
            variants_file = os.path.join(root, dir_name, f"{safe_name}.variants.csv")

            # Extract RSP number from directory name
            rsp_match = re.search(r"-rsp(\d+)", dir_name.lower())
            rsp = f"RSP{rsp_match.group(1)}" if rsp_match else ""

            # Check completeness
            is_complete = all(
                os.path.exists(f) and os.path.getsize(f) > 0
                for f in [metadata_file, chemicals_file, variants_file]
            )

            strains_data[strain_name] = {
                "complete": is_complete,
                "rsp": rsp,
                "dir_name": dir_name,
                "source": "kannapedia",
            }

            # Parse variant relationships
            if os.path.exists(variants_file):
                _parse_variants_csv(variants_file, strain_name, strains_data, all_relationships)

            # Parse terpene data
            if os.path.exists(chemicals_file):
                terpenes = _parse_chemicals_csv(chemicals_file)
                if terpenes:
                    strains_data[strain_name]["terpenes"] = terpenes

    logger.info(
        "Loaded %d strains with %d relationships from %s",
        len(strains_data), len(all_relationships), folder_path,
    )
    return strains_data, all_relationships


def _parse_variants_csv(
    filepath: str,
    strain_name: str,
    strains_data: StrainDataDict,
    all_relationships: RelationshipSet,
) -> None:
    """Parse a variants CSV and populate relationships + discovered strains."""
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            distance_str = row.get("Distance", "")
            rel_strain_raw = row.get("Strain", "")
            if not distance_str or not rel_strain_raw:
                continue

            rel_strain = " ".join(rel_strain_raw.strip().split())
            try:
                distance = float(distance_str)
            except ValueError:
                continue

            all_relationships.add((strain_name, rel_strain, distance))

            # Register discovered strains
            if rel_strain not in strains_data:
                strains_data[rel_strain] = {
                    "complete": False,
                    "rsp": row.get("RSP", ""),
                    "dir_name": "",
                    "source": "kannapedia",
                }


def _parse_chemicals_csv(filepath: str) -> dict[str, float]:
    """Parse a chemicals CSV and return terpene values."""
    terpenes: dict[str, float] = {}
    terpene_keywords = [
        "myrcene", "limonene", "pinene", "caryophyllene",
        "terpinolene", "linalool", "humulene", "ocimene",
        "nerolidol", "bisabolol", "borneol", "camphene",
        "carene", "fenchol", "geraniol", "phellandrene",
        "terpineol", "terpinene", "terpene",
    ]

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Name", "").lower()
            if not any(kw in name for kw in terpene_keywords):
                continue

            value_str = row.get("Value", "0").strip().rstrip("%")
            try:
                terpenes[row.get("Name", name)] = float(value_str)
            except ValueError:
                logger.warning("Could not parse terpene value '%s' for %s", value_str, name)
                terpenes[row.get("Name", name)] = 0.0

    return terpenes


# --------------------------------------------------------------------------- #
# 2. Load from GenomicSample model objects (DB-backed)
# --------------------------------------------------------------------------- #

def load_strain_data_from_samples(
    samples: list[GenomicSample],
) -> tuple[StrainDataDict, RelationshipSet]:
    """Build strains_data and relationships from GenomicSample objects.

    This is the preferred path when loading from the unified DB instead
    of raw CSV files.

    Args:
        samples: List of GenomicSample objects (with relationships + chemicals loaded).

    Returns:
        Tuple of (strains_data dict, all_relationships set).
    """
    strains_data: StrainDataDict = {}
    all_relationships: RelationshipSet = set()

    for sample in samples:
        name = sample.strain_name or sample.rsp_number
        src = sample.source or "kannapedia"
        
        existing = strains_data.get(name)
        should_update = True
        if existing:
            # If the existing one is complete, don't overwrite it with an incomplete one
            if existing.get("complete", False) and not sample.is_complete:
                should_update = False
            # If the existing one is incomplete, and new one is complete, definitely update
            elif not existing.get("complete", False) and sample.is_complete:
                should_update = True
            # If both have same completeness, prefer sources in order: manual > kannapedia > seedfinder > forum
            else:
                pref = {"manual": 4, "kannapedia": 3, "seedfinder": 2, "forum": 1}
                existing_pref = pref.get(existing.get("source"), 0)
                new_pref = pref.get(src, 0)
                if existing_pref >= new_pref:
                    should_update = False

        if should_update:
            strains_data[name] = {
                "complete": sample.is_complete,
                "rsp": sample.rsp_number,
                "dir_name": "",
                "source": src,
            }
            # Build terpene dict from chemical profile
            if sample.chemical_profile:
                terpenes = sample.chemical_profile.terpene_dict
                if terpenes:
                    strains_data[name]["terpenes"] = terpenes

        # Build relationships
        for rel in sample.genetic_relationships:
            other_name = rel.strain_name_b if rel.strain_name_a == name else rel.strain_name_a
            all_relationships.add((name, other_name, rel.distance))

            if other_name not in strains_data:
                other_rsp = rel.rsp_b if rel.strain_name_a == name else rel.rsp_a
                strains_data[other_name] = {
                    "complete": False,
                    "rsp": other_rsp,
                    "dir_name": "",
                    "source": "kannapedia",
                }

    logger.info(
        "Loaded %d strains with %d relationships from %d samples",
        len(strains_data), len(all_relationships), len(samples),
    )
    return strains_data, all_relationships


# --------------------------------------------------------------------------- #
# CSV → GenomicSample conversion (ETL helper)
# --------------------------------------------------------------------------- #

def csv_directory_to_samples(folder_path: str) -> list[GenomicSample]:
    """Convert a kannapedia-scraper CSV directory tree into GenomicSample objects.

    Useful for one-time migration of existing scraped data into the
    unified model layer.

    Args:
        folder_path: Root directory containing strain subdirectories.

    Returns:
        List of GenomicSample objects ready for DB insertion.
    """
    samples: list[GenomicSample] = []

    for root, dirs, _files in os.walk(folder_path):
        # Filter out hidden dirs to prevent os.walk from descending into .git etc.
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for dir_name in dirs:
            # Only process directories matching the strain-RSP naming pattern
            if not re.search(r"-rsp\d+", dir_name.lower()):
                continue

            strain_name = " ".join(dir_name.split("-")[0].strip().split())
            safe_name = strain_name.replace(" ", "_")
            base_path = os.path.join(root, dir_name)

            rsp_match = re.search(r"-rsp(\d+)", dir_name.lower())
            rsp = f"RSP{rsp_match.group(1)}" if rsp_match else ""

            metadata_file = os.path.join(base_path, f"{safe_name}.metadata.csv")
            chemicals_file = os.path.join(base_path, f"{safe_name}.chemicals.csv")
            variants_file = os.path.join(base_path, f"{safe_name}.variants.csv")

            sample = GenomicSample(
                rsp_number=rsp,
                strain_name=strain_name,
                source_url=f"https://www.kannapedia.net/strains/{rsp.lower()}" if rsp else None,
            )

            # Parse metadata
            if os.path.exists(metadata_file):
                _populate_sample_from_metadata(sample, metadata_file)

            # Parse chemicals
            if os.path.exists(chemicals_file):
                sample.chemical_profile = _parse_chemicals_to_profile(chemicals_file, sample.id)

            # Parse variants
            if os.path.exists(variants_file):
                sample.genetic_relationships = _parse_variants_to_relationships(
                    variants_file, strain_name, rsp, sample.id,
                )

            sample.is_complete = all(
                os.path.exists(f) and os.path.getsize(f) > 0
                for f in [metadata_file, chemicals_file, variants_file]
            )

            samples.append(sample)

    logger.info("Converted %d CSV directories to GenomicSample objects", len(samples))
    return samples


def _populate_sample_from_metadata(sample: GenomicSample, filepath: str) -> None:
    """Read a metadata CSV and populate GenomicSample fields."""
    field_map = {
        "Grower": "grower",
        "Accession Date": "accession_date",
        "Reported Sex": "reported_sex",
        "Report Type": "report_type",
        "Rarity": "rarity",
        "Plant Type": "plant_type",
        "Sample Name": "sample_name",
    }

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = row.get("Field", "")
            value = row.get("Value", "")
            if key in field_map and value:
                setattr(sample, field_map[key], value)
            elif key == "Reported Heterozygosity" and value:
                try:
                    sample.heterozygosity = float(value.rstrip("%"))
                except ValueError:
                    pass


def _parse_chemicals_to_profile(filepath: str, sample_id: str) -> ChemicalProfile:
    """Parse a chemicals CSV into a ChemicalProfile object."""
    profile = ChemicalProfile(sample_id=sample_id)

    # Map CSV names to ChemicalProfile field names
    chem_map = {
        "THC + THCA": ("thc", "thca"),
        "CBD + CBDA": ("cbd", "cbda"),
        "THCV + THCVA": ("thcv",),
        "CBC + CBCA": ("cbc",),
        "CBG + CBGA": ("cbg",),
        "CBN + CBNA": ("cbn",),
        "MYRCENE": ("myrcene",),
        "LIMONENE": ("limonene",),
        "BETA-CARYOPHYLLENE": ("caryophyllene",),
        "ALPHA-PINENE": ("pinene_alpha",),
        "BETA-PINENE": ("pinene_beta",),
        "LINALOOL": ("linalool",),
        "ALPHA-HUMULENE": ("humulene",),
        "TERPINOLENE": ("terpinolene",),
        "TOTAL OCIMENE": ("ocimene",),
        "TOTAL NEROLIDOL": ("nerolidol",),
        "ALPHA-BISABOLOL": ("bisabolol",),
        "BORNEOL": ("borneol",),
        "CAMPHENE": ("camphene",),
        "CARENE": ("carene",),
        "CARYOPHYLLENE OXIDE": ("caryophyllene_oxide",),
        "FENCHOL": ("fenchol",),
        "GERANIOL": ("geraniol",),
        "ALPHA-PHELLANDRENE": ("phellandrene",),
        "ALPHA-TERPINEOL": ("terpineol",),
        "ALPHA-TERPINENE": ("terpinene_alpha",),
        "GAMMA-TERPINENE": ("terpinene_gamma",),
    }

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            chem_type = row.get("Type", "")
            name = row.get("Name", "")
            value_str = row.get("Value", "").strip()

            if value_str in ("n/a", "") or "no information" in value_str.lower():
                continue

            value_str = value_str.rstrip("%")
            try:
                value = float(value_str)
            except ValueError:
                continue

            # Try direct mapping
            mapped_fields = chem_map.get(name.upper())
            if mapped_fields:
                setattr(profile, mapped_fields[0], value)
            else:
                profile.raw_data[name] = value

    return profile


def _parse_variants_to_relationships(
    filepath: str,
    strain_name: str,
    rsp: str,
    sample_id: str,
) -> list[GeneticRelationship]:
    """Parse a variants CSV into GeneticRelationship objects."""
    relationships: list[GeneticRelationship] = []

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            distance_str = row.get("Distance", "")
            rel_strain = row.get("Strain", "").strip()
            rel_rsp = row.get("RSP", "").strip()
            rel_type = row.get("Type", "all_samples").strip()

            if not distance_str or not rel_strain:
                continue

            try:
                distance = float(distance_str)
            except ValueError:
                continue

            relationships.append(GeneticRelationship(
                sample_id_a=sample_id,
                strain_name_a=strain_name,
                strain_name_b=" ".join(rel_strain.split()),
                rsp_a=rsp,
                rsp_b=rel_rsp,
                distance=distance,
                relationship_type=rel_type,
            ))

    return relationships
