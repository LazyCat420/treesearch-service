"""
Canonical strain, breeder, and alias models.

These represent the "truth-ish" layer — one entity per genetic concept,
with aliases tracking every name seen across any source.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Breeder:
    """Canonical breeder / seedbank entity."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    aliases: list[str] = field(default_factory=list)
    website: str | None = None
    region: str | None = None
    notes: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "aliases": self.aliases,
            "website": self.website,
            "region": self.region,
            "notes": self.notes,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class StrainAlias:
    """Every name seen for a strain across any source.

    Tracks provenance so we know *where* a name was observed,
    enabling rename detection and cross-source correlation.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    canonical_strain_id: str = ""
    name: str = ""
    source_name: str = ""  # e.g. "kannapedia", "seedfinder", "overgrow"
    source_id: str | None = None
    confidence: float = 1.0  # 0..1 how confident the name→strain link is
    first_seen: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canonical_strain_id": self.canonical_strain_id,
            "name": self.name,
            "source_name": self.source_name,
            "source_id": self.source_id,
            "confidence": self.confidence,
            "first_seen": self.first_seen.isoformat(),
        }


@dataclass
class CanonicalStrain:
    """Canonical strain entity — one per 'genetic concept'.

    The strain is the central node that genomic samples, observations,
    aliases, and source records all link back to.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    primary_name: str = ""
    breeder_id: str | None = None
    strain_type: str | None = None  # "indica", "sativa", "hybrid", "ruderalis"
    lineage: dict[str, Any] = field(default_factory=dict)  # {"mother": ..., "father": ...}
    description: str | None = None

    # Aggregated phenotype fields (computed from observations)
    avg_flowering_days: float | None = None
    avg_thc_pct: float | None = None
    avg_cbd_pct: float | None = None
    dominant_terpenes: list[str] = field(default_factory=list)
    aroma_tags: list[str] = field(default_factory=list)
    effect_tags: list[str] = field(default_factory=list)

    # Feature vectors (computed by analysis pipeline)
    genetic_vector: list[float] | None = None
    phenotype_text_vector: list[float] | None = None
    structure_vector: list[float] | None = None

    # Metadata
    observation_count: int = 0
    source_count: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "primary_name": self.primary_name,
            "breeder_id": self.breeder_id,
            "strain_type": self.strain_type,
            "lineage": self.lineage,
            "description": self.description,
            "avg_flowering_days": self.avg_flowering_days,
            "avg_thc_pct": self.avg_thc_pct,
            "avg_cbd_pct": self.avg_cbd_pct,
            "dominant_terpenes": self.dominant_terpenes,
            "aroma_tags": self.aroma_tags,
            "effect_tags": self.effect_tags,
            "observation_count": self.observation_count,
            "source_count": self.source_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
