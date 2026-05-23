"""
Kannapedia ETL — transforms raw Kannapedia scraper output into canonical models.

Accepts the JSON payload that scraper-service's KannapediaCollector produces
and resolves it into GenomicSample + ChemicalProfile + GeneticRelationship
+ SourceGenomicsRecord objects. Also attempts strain entity resolution
against the canonical strains table.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.models.genomic_sample import (
    GenomicSample,
    ChemicalProfile,
    GeneticRelationship,
)
from src.models.source_record import SourceGenomicsRecord
from src.models.strain import CanonicalStrain, StrainAlias

logger = logging.getLogger(__name__)


def ingest_kannapedia_record(
    payload: dict[str, Any],
    existing_strains: dict[str, CanonicalStrain] | None = None,
) -> dict[str, Any]:
    """Transform a raw Kannapedia scraper payload into canonical models.

    Args:
        payload: JSON dict from KannapediaCollector with keys:
            name, general_info, chemical_content, genetic_relationships, blockchain
        existing_strains: Optional lookup of {name: CanonicalStrain} for entity resolution.

    Returns:
        Dict with:
            'sample': GenomicSample
            'source_record': SourceGenomicsRecord
            'strain': CanonicalStrain (resolved or newly created)
            'alias': StrainAlias
    """
    if existing_strains is None:
        existing_strains = {}

    strain_name = payload.get("name", "Unknown")
    general_info = payload.get("general_info", {})
    chemical_content = payload.get("chemical_content", {})
    genetic_rels = payload.get("genetic_relationships", {})
    blockchain = payload.get("blockchain", {})

    # Extract RSP number
    rsp_number = payload.get("rsp_number") or _extract_rsp(general_info)
    if not rsp_number and payload.get("source_url"):
        match = re.search(r"rsp\d+", payload["source_url"], re.IGNORECASE)
        if match:
            rsp_number = match.group(0).upper()

    # Build source record (raw preservation)
    source_record = SourceGenomicsRecord(
        source_id=rsp_number,
        source_url=f"https://www.kannapedia.net/strains/{rsp_number.lower()}" if rsp_number else None,
        metadata_fields=general_info,
        chemical_fields=chemical_content,
        variant_fields=_flatten_genetic_relationships(genetic_rels),
        payload=payload,
    )

    # Build chemical profile
    chem_profile = _build_chemical_profile(chemical_content)

    # Build genetic relationships
    relationships = _build_genetic_relationships(
        genetic_rels, strain_name, rsp_number,
    )

    # Build genomic sample
    sample = GenomicSample(
        rsp_number=rsp_number,
        strain_name=strain_name,
        sample_name=general_info.get("Sample Name", ""),
        grower=general_info.get("Grower"),
        accession_date=general_info.get("Accession Date"),
        reported_sex=general_info.get("Reported Sex"),
        report_type=general_info.get("Report Type"),
        rarity=general_info.get("Rarity"),
        plant_type=general_info.get("Plant Type"),
        heterozygosity=_parse_heterozygosity(general_info.get("Reported Heterozygosity")),
        y_ratio=general_info.get("Y Ratio Distribution"),
        transaction_id=blockchain.get("txid"),
        shasum_hash=blockchain.get("shasum"),
        chemical_profile=chem_profile,
        genetic_relationships=relationships,
        source_url=source_record.source_url,
        is_complete=True,
        raw_payload=payload,
    )

    # Resolve or create canonical strain
    strain = _resolve_strain(strain_name, existing_strains)

    # Link sample to strain
    sample.canonical_strain_id = strain.id

    # Create alias
    alias = StrainAlias(
        canonical_strain_id=strain.id,
        name=strain_name,
        source_name="kannapedia",
        source_id=rsp_number,
    )

    # Link source record
    source_record.genomic_sample_id = sample.id

    logger.info(
        "Ingested Kannapedia record: %s (%s) → strain %s",
        strain_name, rsp_number, strain.id,
    )

    return {
        "sample": sample,
        "source_record": source_record,
        "strain": strain,
        "alias": alias,
    }


def _extract_rsp(general_info: dict[str, Any]) -> str:
    """Extract RSP number from general info or ref number field."""
    for key in ["REF NUMBER", "Ref Number", "ref_number"]:
        val = general_info.get(key, "")
        if val:
            match = re.search(r"RSP\d+", val, re.IGNORECASE)
            if match:
                return match.group(0).upper()
    return ""


def _parse_heterozygosity(value: str | None) -> float | None:
    """Parse heterozygosity from string like '1.2418%'."""
    if not value:
        return None
    try:
        return float(value.rstrip("%"))
    except ValueError:
        return None


def _build_chemical_profile(chemical_content: dict[str, Any]) -> ChemicalProfile:
    """Build ChemicalProfile from kannapedia chemical_content dict."""
    profile = ChemicalProfile()
    cannabinoids = chemical_content.get("cannabinoids", {})
    terpenoids = chemical_content.get("terpenoids", {})

    # Cannabinoid mapping
    cann_map = {
        "THC": "thc", "THCA": "thca", "CBD": "cbd", "CBDA": "cbda",
        "THCV": "thcv", "CBC": "cbc", "CBG": "cbg", "CBN": "cbn",
    }
    for raw_name, value_str in cannabinoids.items():
        # Use exact match (case-insensitive) to prevent 'THC' matching 'THCA'
        raw_upper = raw_name.strip().upper()
        matched_field = cann_map.get(raw_upper)
        if matched_field:
            try:
                setattr(profile, matched_field, float(str(value_str).rstrip("%")))
            except ValueError:
                pass
        else:
            # Fallback: try substring match for non-standard names
            for cann_key, field_name in cann_map.items():
                if cann_key.lower() in raw_name.lower():
                    try:
                        setattr(profile, field_name, float(str(value_str).rstrip("%")))
                    except ValueError:
                        pass
                    break

    # Terpenoid mapping
    terp_map = {
        "myrcene": "myrcene", "limonene": "limonene",
        "caryophyllene": "caryophyllene", "alpha-pinene": "pinene_alpha",
        "β-pinene": "pinene_beta", "beta-pinene": "pinene_beta",
        "linalool": "linalool", "humulene": "humulene",
        "terpinolene": "terpinolene", "ocimene": "ocimene",
        "nerolidol": "nerolidol", "bisabolol": "bisabolol",
        "borneol": "borneol", "camphene": "camphene",
        "carene": "carene", "fenchol": "fenchol",
        "geraniol": "geraniol", "phellandrene": "phellandrene",
        "terpineol": "terpineol",
    }
    for raw_name, value_str in terpenoids.items():
        name_lower = raw_name.lower()
        for terp_key, field_name in terp_map.items():
            if terp_key in name_lower:
                try:
                    setattr(profile, field_name, float(str(value_str).rstrip("%")))
                except ValueError:
                    pass
                break
        else:
            profile.raw_data[raw_name] = value_str

    return profile


def _build_genetic_relationships(
    genetic_rels: dict[str, Any],
    strain_name: str,
    rsp: str,
) -> list[GeneticRelationship]:
    """Build GeneticRelationship objects from scraper genetic_relationships dict."""
    relationships: list[GeneticRelationship] = []

    type_map = {
        "all_samples": "all_samples",
        "base_tree": "base_tree",
        "most_distant": "most_distant",
    }

    for rel_type, type_label in type_map.items():
        entries = genetic_rels.get(rel_type, [])
        for entry in entries:
            relationships.append(GeneticRelationship(
                strain_name_a=strain_name,
                strain_name_b=entry.get("strain", ""),
                rsp_a=rsp,
                rsp_b=entry.get("rsp", "").upper(),
                distance=entry.get("distance", 0.0),
                relationship_type=type_label,
            ))

    return relationships


def _flatten_genetic_relationships(genetic_rels: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten genetic relationships dict into a list for storage."""
    flat: list[dict[str, Any]] = []
    for rel_type, entries in genetic_rels.items():
        for entry in entries:
            flat.append({"type": rel_type, **entry})
    return flat


def _resolve_strain(
    name: str,
    existing_strains: dict[str, CanonicalStrain],
) -> CanonicalStrain:
    """Resolve a strain name to an existing canonical strain or create a new one."""
    name_stripped = name.strip()
    name_lower = name_stripped.lower()
    
    # 1. Exact match after lowercase / strip
    for existing_name, strain in existing_strains.items():
        if existing_name.lower().strip() == name_lower:
            return strain

    # 2. Normalized match (remove spaces, underscores, hyphens, and other non-alphanumeric chars)
    def normalize(val: str) -> str:
        return re.sub(r"[^a-z0-9]", "", val.lower().strip())
    
    name_norm = normalize(name_stripped)
    for existing_name, strain in existing_strains.items():
        if normalize(existing_name) == name_norm:
            return strain

    # Create new canonical strain
    strain = CanonicalStrain(primary_name=name)
    existing_strains[name] = strain
    return strain
