"""
Observation model — a single grow/smoke report from any source.

Each observation is a post-level record with parsed descriptors
and numeric fields (flower time, height, THC%, etc.). Observations
are linked to canonical strains and can come from forums (Overgrow,
Rollitup, THCFarmer), structured DBs (Leafly, SeedFinder), or
direct lab data.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Observation:
    """Single grow/smoke report with parsed descriptors.

    These are the building blocks for phenotype clustering and
    cross-source agreement analysis.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    canonical_strain_id: str | None = None

    # Source provenance
    source_name: str = ""  # "overgrow", "rollitup", "thcfarmer", "leafly", etc.
    source_id: str = ""    # original post/review ID
    source_url: str | None = None
    author: str | None = None
    observed_at: datetime | None = None

    # The name used by this source (may not match canonical)
    reported_strain_name: str = ""
    reported_breeder: str | None = None

    # Grow report numeric fields
    flowering_days: int | None = None
    veg_days: int | None = None
    height_cm: float | None = None
    yield_g: float | None = None
    thc_pct: float | None = None
    cbd_pct: float | None = None

    # Parsed phenotype tags
    aroma_tags: list[str] = field(default_factory=list)
    effect_tags: list[str] = field(default_factory=list)
    structure_tags: list[str] = field(default_factory=list)  # "dense", "fluffy", "foxtail", etc.
    color_tags: list[str] = field(default_factory=list)

    # Terpene mentions (from text, not lab)
    mentioned_terpenes: list[str] = field(default_factory=list)

    # Free text
    grow_notes: str | None = None
    smoke_notes: str | None = None
    raw_text: str | None = None

    # Computed vectors (filled by analysis pipeline)
    text_embedding: list[float] | None = None

    # Quality / trust score
    quality_score: float = 0.5  # 0..1
    is_verified: bool = False

    # Timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canonical_strain_id": self.canonical_strain_id,
            "source_name": self.source_name,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "author": self.author,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "reported_strain_name": self.reported_strain_name,
            "reported_breeder": self.reported_breeder,
            "flowering_days": self.flowering_days,
            "veg_days": self.veg_days,
            "height_cm": self.height_cm,
            "thc_pct": self.thc_pct,
            "cbd_pct": self.cbd_pct,
            "aroma_tags": self.aroma_tags,
            "effect_tags": self.effect_tags,
            "structure_tags": self.structure_tags,
            "color_tags": self.color_tags,
            "mentioned_terpenes": self.mentioned_terpenes,
            "quality_score": self.quality_score,
            "created_at": self.created_at.isoformat(),
        }
