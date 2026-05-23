"""
Per-source evidence tables — raw records before canonical resolution.

Instead of smashing everything together, we keep explicit source-level
records so we can inspect conflicting claims and debug entity resolution.
Each record has source_name, source_id, and a jsonb-style payload.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SourceStrainRecord:
    """Raw strain record from a single source.

    "SeedFinder says X", "Leafly says Y", "Cannapedia AI says Z"
    for a given marketing name. FKs back to canonical entities
    when resolved.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    canonical_strain_id: str | None = None  # set after entity resolution

    source_name: str = ""  # "seedfinder", "leafly", "cannapedia_ai", "overgrow"
    source_id: str = ""    # original ID in that source
    source_url: str | None = None

    # The name as this source calls it
    strain_name: str = ""
    breeder_name: str | None = None

    # Structured fields the source provides
    strain_type: str | None = None   # indica/sativa/hybrid
    lineage: dict[str, Any] = field(default_factory=dict)
    flowering_time_days: int | None = None
    thc_range: str | None = None     # "18-24%"
    cbd_range: str | None = None
    terpene_list: list[str] = field(default_factory=list)
    aroma_descriptors: list[str] = field(default_factory=list)
    effect_descriptors: list[str] = field(default_factory=list)
    description: str | None = None

    # Full payload for anything we didn't parse into typed fields
    payload: dict[str, Any] = field(default_factory=dict)

    # Timestamps
    scraped_at: datetime = field(default_factory=datetime.utcnow)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canonical_strain_id": self.canonical_strain_id,
            "source_name": self.source_name,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "strain_name": self.strain_name,
            "breeder_name": self.breeder_name,
            "strain_type": self.strain_type,
            "lineage": self.lineage,
            "flowering_time_days": self.flowering_time_days,
            "thc_range": self.thc_range,
            "cbd_range": self.cbd_range,
            "terpene_list": self.terpene_list,
            "aroma_descriptors": self.aroma_descriptors,
            "effect_descriptors": self.effect_descriptors,
            "description": self.description,
            "payload": self.payload,
            "scraped_at": self.scraped_at.isoformat(),
        }


@dataclass
class SourceGenomicsRecord:
    """Raw genomics record from Kannapedia (or other WGS source).

    Stores the unprocessed metadata, chemicals, and variants before
    normalization into GenomicSample. Preserves the original CSV fields
    exactly as they were scraped.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    genomic_sample_id: str | None = None  # set after normalization

    source_name: str = "kannapedia"
    source_id: str = ""  # RSP number
    source_url: str | None = None

    # Raw CSV data preserved exactly
    metadata_fields: dict[str, Any] = field(default_factory=dict)
    chemical_fields: dict[str, Any] = field(default_factory=dict)
    variant_fields: list[dict[str, Any]] = field(default_factory=list)

    # Full raw payload
    payload: dict[str, Any] = field(default_factory=dict)

    # Timestamps
    scraped_at: datetime = field(default_factory=datetime.utcnow)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "genomic_sample_id": self.genomic_sample_id,
            "source_name": self.source_name,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "metadata_fields": self.metadata_fields,
            "chemical_fields": self.chemical_fields,
            "variant_fields": self.variant_fields,
            "payload": self.payload,
            "scraped_at": self.scraped_at.isoformat(),
        }
