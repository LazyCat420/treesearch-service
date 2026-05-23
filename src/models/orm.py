import uuid
from datetime import datetime
from sqlalchemy import String, Float, Integer, JSON, Boolean, ForeignKey, DateTime, Column, Text
from sqlalchemy.orm import relationship, Mapped, mapped_column
from typing import Any, List

from src.db import Base

class BreederORM(Base):
    __tablename__ = "breeders"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    website: Mapped[str | None] = mapped_column(String, nullable=True)
    region: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    strains: Mapped[List["CanonicalStrainORM"]] = relationship("CanonicalStrainORM", back_populates="breeder")

class StrainAliasORM(Base):
    __tablename__ = "strain_aliases"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    canonical_strain_id: Mapped[str] = mapped_column(String, ForeignKey("canonical_strains.id"), index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    source_name: Mapped[str] = mapped_column(String)
    source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    canonical_strain: Mapped["CanonicalStrainORM"] = relationship("CanonicalStrainORM", back_populates="aliases")

class CanonicalStrainORM(Base):
    __tablename__ = "canonical_strains"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    primary_name: Mapped[str] = mapped_column(String, index=True)
    breeder_id: Mapped[str | None] = mapped_column(String, ForeignKey("breeders.id"), nullable=True)
    strain_type: Mapped[str | None] = mapped_column(String, nullable=True)
    lineage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    description: Mapped[str | None] = mapped_column(String, nullable=True)

    avg_flowering_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_thc_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_cbd_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    dominant_terpenes: Mapped[list[str]] = mapped_column(JSON, default=list)
    aroma_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    effect_tags: Mapped[list[str]] = mapped_column(JSON, default=list)

    genetic_vector: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    phenotype_text_vector: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    structure_vector: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)

    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    breeder: Mapped["BreederORM"] = relationship("BreederORM", back_populates="strains")
    aliases: Mapped[List["StrainAliasORM"]] = relationship("StrainAliasORM", back_populates="canonical_strain")
    genomic_samples: Mapped[List["GenomicSampleORM"]] = relationship("GenomicSampleORM", back_populates="canonical_strain")
    observations: Mapped[List["ObservationORM"]] = relationship("ObservationORM", back_populates="canonical_strain")


# --------------------------------------------------------------------------- #
# Genomic sample tables (ported from kannapedia-scraper)
# --------------------------------------------------------------------------- #

class GenomicSampleORM(Base):
    """A single sequenced sample from Kannapedia or other WGS/SNP source."""
    __tablename__ = "genomic_samples"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    canonical_strain_id: Mapped[str | None] = mapped_column(String, ForeignKey("canonical_strains.id"), nullable=True, index=True)

    rsp_number: Mapped[str] = mapped_column(String, index=True)
    sample_name: Mapped[str] = mapped_column(String, default="")
    strain_name: Mapped[str] = mapped_column(String, index=True)

    grower: Mapped[str | None] = mapped_column(String, nullable=True)
    accession_date: Mapped[str | None] = mapped_column(String, nullable=True)
    reported_sex: Mapped[str | None] = mapped_column(String, nullable=True)
    report_type: Mapped[str | None] = mapped_column(String, nullable=True)
    rarity: Mapped[str | None] = mapped_column(String, nullable=True)
    plant_type: Mapped[str | None] = mapped_column(String, nullable=True)
    heterozygosity: Mapped[float | None] = mapped_column(Float, nullable=True)
    y_ratio: Mapped[str | None] = mapped_column(String, nullable=True)

    transaction_id: Mapped[str | None] = mapped_column(String, nullable=True)
    shasum_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    data_files: Mapped[list[str]] = mapped_column(JSON, default=list)

    source: Mapped[str] = mapped_column(String, default="kannapedia")
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    canonical_strain: Mapped["CanonicalStrainORM"] = relationship("CanonicalStrainORM", back_populates="genomic_samples")
    chemical_profile: Mapped["ChemicalProfileORM"] = relationship("ChemicalProfileORM", back_populates="sample", uselist=False)
    genetic_relationships: Mapped[List["GeneticRelationshipORM"]] = relationship(
        "GeneticRelationshipORM",
        foreign_keys="GeneticRelationshipORM.sample_id_a",
        back_populates="sample_a",
    )


class ChemicalProfileORM(Base):
    """Cannabinoid + terpenoid assay results for a sample."""
    __tablename__ = "chemical_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sample_id: Mapped[str] = mapped_column(String, ForeignKey("genomic_samples.id"), unique=True, index=True)

    # Cannabinoids
    thc: Mapped[float | None] = mapped_column(Float, nullable=True)
    thca: Mapped[float | None] = mapped_column(Float, nullable=True)
    cbd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cbda: Mapped[float | None] = mapped_column(Float, nullable=True)
    thcv: Mapped[float | None] = mapped_column(Float, nullable=True)
    cbc: Mapped[float | None] = mapped_column(Float, nullable=True)
    cbg: Mapped[float | None] = mapped_column(Float, nullable=True)
    cbn: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Terpenoids — primary
    myrcene: Mapped[float | None] = mapped_column(Float, nullable=True)
    limonene: Mapped[float | None] = mapped_column(Float, nullable=True)
    caryophyllene: Mapped[float | None] = mapped_column(Float, nullable=True)
    pinene_alpha: Mapped[float | None] = mapped_column(Float, nullable=True)
    pinene_beta: Mapped[float | None] = mapped_column(Float, nullable=True)
    linalool: Mapped[float | None] = mapped_column(Float, nullable=True)
    humulene: Mapped[float | None] = mapped_column(Float, nullable=True)
    terpinolene: Mapped[float | None] = mapped_column(Float, nullable=True)
    ocimene: Mapped[float | None] = mapped_column(Float, nullable=True)
    nerolidol: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Terpenoids — extended
    bisabolol: Mapped[float | None] = mapped_column(Float, nullable=True)
    borneol: Mapped[float | None] = mapped_column(Float, nullable=True)
    camphene: Mapped[float | None] = mapped_column(Float, nullable=True)
    carene: Mapped[float | None] = mapped_column(Float, nullable=True)
    caryophyllene_oxide: Mapped[float | None] = mapped_column(Float, nullable=True)
    fenchol: Mapped[float | None] = mapped_column(Float, nullable=True)
    geraniol: Mapped[float | None] = mapped_column(Float, nullable=True)
    phellandrene: Mapped[float | None] = mapped_column(Float, nullable=True)
    terpineol: Mapped[float | None] = mapped_column(Float, nullable=True)
    terpinene_alpha: Mapped[float | None] = mapped_column(Float, nullable=True)
    terpinene_gamma: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    sample: Mapped["GenomicSampleORM"] = relationship("GenomicSampleORM", back_populates="chemical_profile")

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


class GeneticRelationshipORM(Base):
    """Pairwise genetic distance between two samples."""
    __tablename__ = "genetic_relationships"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sample_id_a: Mapped[str] = mapped_column(String, ForeignKey("genomic_samples.id"), index=True)
    sample_id_b: Mapped[str] = mapped_column(String, default="")
    strain_name_a: Mapped[str] = mapped_column(String, default="")
    strain_name_b: Mapped[str] = mapped_column(String, default="")
    rsp_a: Mapped[str] = mapped_column(String, default="")
    rsp_b: Mapped[str] = mapped_column(String, default="")
    distance: Mapped[float] = mapped_column(Float, default=0.0)
    relationship_type: Mapped[str] = mapped_column(String, default="all_samples")
    source: Mapped[str] = mapped_column(String, default="kannapedia")

    sample_a: Mapped["GenomicSampleORM"] = relationship("GenomicSampleORM", back_populates="genetic_relationships")


# --------------------------------------------------------------------------- #
# Observation table (forum/grow reports)
# --------------------------------------------------------------------------- #

class ObservationORM(Base):
    """Single grow/smoke report from any source."""
    __tablename__ = "observations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    canonical_strain_id: Mapped[str | None] = mapped_column(String, ForeignKey("canonical_strains.id"), nullable=True, index=True)

    source_name: Mapped[str] = mapped_column(String, index=True)
    source_id: Mapped[str] = mapped_column(String, default="")
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    reported_strain_name: Mapped[str] = mapped_column(String, default="", index=True)
    reported_breeder: Mapped[str | None] = mapped_column(String, nullable=True)

    flowering_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    veg_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    yield_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    thc_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cbd_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    aroma_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    effect_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    structure_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    color_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    mentioned_terpenes: Mapped[list[str]] = mapped_column(JSON, default=list)

    grow_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    smoke_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    text_embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    quality_score: Mapped[float] = mapped_column(Float, default=0.5)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    canonical_strain: Mapped["CanonicalStrainORM"] = relationship("CanonicalStrainORM", back_populates="observations")
    images: Mapped[list["ObservationImageORM"]] = relationship("ObservationImageORM", back_populates="observation", cascade="all, delete-orphan")


class ObservationImageORM(Base):
    """Image associated with a forum observation."""
    __tablename__ = "observation_images"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    observation_id: Mapped[str] = mapped_column(String, ForeignKey("observations.id"), index=True)
    image_url: Mapped[str] = mapped_column(String)
    local_path: Mapped[str | None] = mapped_column(String, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    cluster_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    observation: Mapped["ObservationORM"] = relationship("ObservationORM", back_populates="images")


# --------------------------------------------------------------------------- #
# Per-source evidence tables
# --------------------------------------------------------------------------- #

class SourceStrainRecordORM(Base):
    """Raw strain record from a single source before entity resolution."""
    __tablename__ = "source_strain_records"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    canonical_strain_id: Mapped[str | None] = mapped_column(String, ForeignKey("canonical_strains.id"), nullable=True, index=True)

    source_name: Mapped[str] = mapped_column(String, index=True)
    source_id: Mapped[str] = mapped_column(String, default="")
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)

    strain_name: Mapped[str] = mapped_column(String, index=True)
    breeder_name: Mapped[str | None] = mapped_column(String, nullable=True)
    strain_type: Mapped[str | None] = mapped_column(String, nullable=True)
    lineage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    flowering_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thc_range: Mapped[str | None] = mapped_column(String, nullable=True)
    cbd_range: Mapped[str | None] = mapped_column(String, nullable=True)
    terpene_list: Mapped[list[str]] = mapped_column(JSON, default=list)
    aroma_descriptors: Mapped[list[str]] = mapped_column(JSON, default=list)
    effect_descriptors: Mapped[list[str]] = mapped_column(JSON, default=list)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SourceGenomicsRecordORM(Base):
    """Raw genomics record before normalization into GenomicSample."""
    __tablename__ = "source_genomics_records"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    genomic_sample_id: Mapped[str | None] = mapped_column(String, ForeignKey("genomic_samples.id"), nullable=True, index=True)

    source_name: Mapped[str] = mapped_column(String, default="kannapedia")
    source_id: Mapped[str] = mapped_column(String, default="", index=True)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)

    metadata_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    chemical_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    variant_fields: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

