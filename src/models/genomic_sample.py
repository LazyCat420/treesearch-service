"""
Genomic sample models — derived from Kannapedia and other WGS/SNP datasets.

Each GenomicSample represents a single sequenced sample linked to a
canonical strain when possible. ChemicalProfile stores cannabinoid +
terpenoid assay data. GeneticRelationship stores pairwise distances
between samples.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ChemicalProfile:
    """Cannabinoid + terpenoid assay results for a single sample.

    Values are stored as percentages (0.0–100.0) or None if not assayed.
    Maps directly to the chemicals CSV from kannapedia-scraper.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sample_id: str = ""

    # Cannabinoids
    thc: float | None = None
    thca: float | None = None
    cbd: float | None = None
    cbda: float | None = None
    thcv: float | None = None
    cbc: float | None = None
    cbg: float | None = None
    cbn: float | None = None

    # Terpenoids — primary panel
    myrcene: float | None = None
    limonene: float | None = None
    caryophyllene: float | None = None
    pinene_alpha: float | None = None
    pinene_beta: float | None = None
    linalool: float | None = None
    humulene: float | None = None
    terpinolene: float | None = None
    ocimene: float | None = None
    nerolidol: float | None = None

    # Terpenoids — extended panel
    bisabolol: float | None = None
    borneol: float | None = None
    camphene: float | None = None
    carene: float | None = None
    caryophyllene_oxide: float | None = None
    fenchol: float | None = None
    geraniol: float | None = None
    phellandrene: float | None = None
    terpineol: float | None = None
    terpinene_alpha: float | None = None
    terpinene_gamma: float | None = None

    # Raw payload for any extra fields
    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def total_thc(self) -> float | None:
        """Total THC = THC + (THCA × 0.877)."""
        if self.thc is None and self.thca is None:
            return None
        return (self.thc or 0.0) + (self.thca or 0.0) * 0.877

    @property
    def total_cbd(self) -> float | None:
        """Total CBD = CBD + (CBDA × 0.877)."""
        if self.cbd is None and self.cbda is None:
            return None
        return (self.cbd or 0.0) + (self.cbda or 0.0) * 0.877

    @property
    def terpene_dict(self) -> dict[str, float]:
        """Return non-None terpene values as a flat dict."""
        terpenes = {}
        for name in [
            "myrcene", "limonene", "caryophyllene", "pinene_alpha",
            "pinene_beta", "linalool", "humulene", "terpinolene",
            "ocimene", "nerolidol", "bisabolol", "borneol", "camphene",
            "carene", "caryophyllene_oxide", "fenchol", "geraniol",
            "phellandrene", "terpineol", "terpinene_alpha", "terpinene_gamma",
        ]:
            val = getattr(self, name)
            if val is not None:
                terpenes[name] = val
        return terpenes

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "sample_id": self.sample_id,
            "total_thc": self.total_thc,
            "total_cbd": self.total_cbd,
        }
        result["cannabinoids"] = {
            k: v for k, v in {
                "thc": self.thc, "thca": self.thca,
                "cbd": self.cbd, "cbda": self.cbda,
                "thcv": self.thcv, "cbc": self.cbc,
                "cbg": self.cbg, "cbn": self.cbn,
            }.items() if v is not None
        }
        result["terpenes"] = self.terpene_dict
        return result


@dataclass
class GeneticRelationship:
    """Pairwise genetic distance between two samples.

    Distances are typically 0.0 (identical) to ~0.5+ (very distant).
    Relationship type distinguishes all_samples vs base_tree vs most_distant
    as provided by Kannapedia.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sample_id_a: str = ""
    sample_id_b: str = ""
    strain_name_a: str = ""
    strain_name_b: str = ""
    rsp_a: str = ""
    rsp_b: str = ""
    distance: float = 0.0
    relationship_type: str = "all_samples"  # "all_samples", "base_tree", "most_distant"
    source: str = "kannapedia"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sample_id_a": self.sample_id_a,
            "sample_id_b": self.sample_id_b,
            "strain_name_a": self.strain_name_a,
            "strain_name_b": self.strain_name_b,
            "rsp_a": self.rsp_a,
            "rsp_b": self.rsp_b,
            "distance": self.distance,
            "relationship_type": self.relationship_type,
            "source": self.source,
        }


@dataclass
class GenomicSample:
    """A single sequenced sample from Kannapedia or other WGS/SNP dataset.

    Linked to a canonical strain when possible. Contains metadata about
    the sample, its chemical profile, and its genetic relationships.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    canonical_strain_id: str | None = None

    # Kannapedia identifiers
    rsp_number: str = ""  # e.g. "RSP10143"
    sample_name: str = ""
    strain_name: str = ""

    # Metadata from Kannapedia
    grower: str | None = None
    accession_date: str | None = None
    reported_sex: str | None = None
    report_type: str | None = None
    rarity: str | None = None
    plant_type: str | None = None
    heterozygosity: float | None = None
    y_ratio: str | None = None

    # Blockchain provenance
    transaction_id: str | None = None
    shasum_hash: str | None = None
    data_files: list[str] = field(default_factory=list)

    # Linked data
    chemical_profile: ChemicalProfile | None = None
    genetic_relationships: list[GeneticRelationship] = field(default_factory=list)

    # Source tracking
    source: str = "kannapedia"
    source_url: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)

    # Metadata
    is_complete: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canonical_strain_id": self.canonical_strain_id,
            "rsp_number": self.rsp_number,
            "sample_name": self.sample_name,
            "strain_name": self.strain_name,
            "grower": self.grower,
            "accession_date": self.accession_date,
            "reported_sex": self.reported_sex,
            "report_type": self.report_type,
            "rarity": self.rarity,
            "plant_type": self.plant_type,
            "heterozygosity": self.heterozygosity,
            "transaction_id": self.transaction_id,
            "shasum_hash": self.shasum_hash,
            "data_files": self.data_files,
            "chemical_profile": self.chemical_profile.to_dict() if self.chemical_profile else None,
            "genetic_relationships": [r.to_dict() for r in self.genetic_relationships],
            "source": self.source,
            "source_url": self.source_url,
            "is_complete": self.is_complete,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
