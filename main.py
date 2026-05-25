"""
treesearch-service — Unified cannabis data warehouse + analysis API.

Standalone backend service. Provides:
  - REST API for strain search/compare
  - Network data endpoints for the visualization frontend
  - ETL ingestion from scraper-service
  - Strain detail endpoints

The frontend is served separately by treesearch-client (nginx).
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import or_
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from src.genomics.data_loader import (
    csv_directory_to_samples,
)
from src.genomics.terpene_analysis import (
    normalize_terpene_profile,
)
from src.genomics.distance_matrix import (
    get_nearest_neighbors,
    create_distance_matrix,
)
from src.genomics.similarity import compute_combined_similarity
from src.viz.server import build_network_data
from src.etl.kannapedia_etl import ingest_kannapedia_record
from src.db import init_db, get_session
from src.models.orm import (
    CanonicalStrainORM,
    GenomicSampleORM,
    ChemicalProfileORM,
    GeneticRelationshipORM,
    SourceGenomicsRecordORM,
    StrainAliasORM,
    ObservationORM,
    ObservationImageORM,
    BreederORM,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


import asyncio
_db_state_cache = None
_db_state_lock = asyncio.Lock()
_resolved_name_cache = {}

def invalidate_db_state_cache():
    global _db_state_cache, _resolved_name_cache
    logger.info("Invalidating DB state and resolved name caches.")
    _db_state_cache = None
    _resolved_name_cache.clear()

async def get_canonical_strain_name(session, name: str) -> Optional[str]:
    """Resolve any case, punctuation, or alias variation of a strain name to its canonical primary name in the database."""
    if not name:
        return None
        
    name_key = name.lower().strip()
    if name_key in _resolved_name_cache:
        return _resolved_name_cache[name_key]
        
    # 1. Case-insensitive exact match
    stmt = select(CanonicalStrainORM.primary_name).where(CanonicalStrainORM.primary_name.ilike(name))
    res = (await session.execute(stmt)).scalar()
    if res:
        _resolved_name_cache[name_key] = res
        return res
        
    # 2. Case and punctuation-insensitive match
    import re
    norm = re.sub(r"[^a-z0-9]", "", name.lower())
    stmt2 = select(CanonicalStrainORM.primary_name).where(
        func.replace(func.replace(func.replace(func.lower(CanonicalStrainORM.primary_name), "_", ""), "-", ""), " ", "") == norm
    )
    res = (await session.execute(stmt2)).scalar()
    if res:
        _resolved_name_cache[name_key] = res
        return res
        
    # 3. Check aliases
    stmt_alias = select(CanonicalStrainORM.primary_name).join(
        StrainAliasORM, CanonicalStrainORM.id == StrainAliasORM.canonical_strain_id
    ).where(
        or_(
            StrainAliasORM.name.ilike(name),
            func.replace(func.replace(func.replace(func.lower(StrainAliasORM.name), "_", ""), "-", ""), " ", "") == norm
        )
    )
    res = (await session.execute(stmt_alias)).scalar()
    if res:
        _resolved_name_cache[name_key] = res
        return res
        
    return None


def clean_forum_image_url(url: str) -> str:
    """Extract direct image URL from XenForo/Rollitup proxy.php wrapper URLs if present."""
    if not url:
        return ""
    if "proxy.php?image=" in url or "/proxy.php?image=" in url:
        try:
            import urllib.parse
            import re
            parsed = urllib.parse.urlparse(url)
            query = urllib.parse.parse_qs(parsed.query)
            image_param = query.get("image")
            if image_param:
                return image_param[0]
        except Exception:
            import urllib.parse
            import re
            # Fallback regex if URL parsing fails
            match = re.search(r'[?&]image=([^&]+)', url)
            if match:
                return urllib.parse.unquote(match.group(1))
    return url


async def save_domain_models_to_db(session, result: dict):
    """Save resolved Kannapedia domain objects to Postgres."""
    # 1. Save / Update CanonicalStrain
    strain_domain = result["strain"]
    stmt = select(CanonicalStrainORM).where(CanonicalStrainORM.primary_name == strain_domain.primary_name)
    strain_orm = (await session.execute(stmt)).scalars().first()
    if not strain_orm:
        strain_orm = CanonicalStrainORM(
            id=strain_domain.id,
            primary_name=strain_domain.primary_name,
            strain_type=strain_domain.strain_type,
            lineage=strain_domain.lineage,
            description=strain_domain.description,
            avg_flowering_days=strain_domain.avg_flowering_days,
            avg_thc_pct=strain_domain.avg_thc_pct,
            avg_cbd_pct=strain_domain.avg_cbd_pct,
            dominant_terpenes=strain_domain.dominant_terpenes,
            aroma_tags=strain_domain.aroma_tags,
            effect_tags=strain_domain.effect_tags,
        )
        session.add(strain_orm)
    else:
        if strain_domain.avg_thc_pct is not None:
            strain_orm.avg_thc_pct = strain_domain.avg_thc_pct
        if strain_domain.avg_cbd_pct is not None:
            strain_orm.avg_cbd_pct = strain_domain.avg_cbd_pct
        if strain_domain.dominant_terpenes:
            strain_orm.dominant_terpenes = strain_domain.dominant_terpenes
            
    await session.flush()
    
    # 2. Save GenomicSample
    sample_domain = result["sample"]
    # Check if sample exists
    if sample_domain.rsp_number:
        stmt_sample = select(GenomicSampleORM).where(GenomicSampleORM.rsp_number == sample_domain.rsp_number)
        existing_sample = (await session.execute(stmt_sample)).scalars().first()
        if existing_sample:
            return
        
    sample_orm = GenomicSampleORM(
        id=sample_domain.id,
        canonical_strain_id=strain_orm.id,
        rsp_number=sample_domain.rsp_number,
        sample_name=sample_domain.sample_name,
        strain_name=sample_domain.strain_name,
        grower=sample_domain.grower,
        accession_date=sample_domain.accession_date,
        reported_sex=sample_domain.reported_sex,
        report_type=sample_domain.report_type,
        rarity=sample_domain.rarity,
        plant_type=sample_domain.plant_type,
        heterozygosity=sample_domain.heterozygosity,
        y_ratio=sample_domain.y_ratio,
        transaction_id=sample_domain.transaction_id,
        shasum_hash=sample_domain.shasum_hash,
        data_files=sample_domain.data_files,
        source=sample_domain.source,
        source_url=sample_domain.source_url,
        is_complete=sample_domain.is_complete,
    )
    session.add(sample_orm)
    await session.flush()
    
    # 3. Save ChemicalProfile
    if sample_domain.chemical_profile:
        cp_domain = sample_domain.chemical_profile
        cp_orm = ChemicalProfileORM(
            id=cp_domain.id,
            sample_id=sample_orm.id,
            thc=cp_domain.thc,
            thca=cp_domain.thca,
            cbd=cp_domain.cbd,
            cbda=cp_domain.cbda,
            thcv=cp_domain.thcv,
            cbc=cp_domain.cbc,
            cbg=cp_domain.cbg,
            cbn=cp_domain.cbn,
            myrcene=cp_domain.myrcene,
            limonene=cp_domain.limonene,
            caryophyllene=cp_domain.caryophyllene,
            pinene_alpha=cp_domain.pinene_alpha,
            pinene_beta=cp_domain.pinene_beta,
            linalool=cp_domain.linalool,
            humulene=cp_domain.humulene,
            terpinolene=cp_domain.terpinolene,
            ocimene=cp_domain.ocimene,
            nerolidol=cp_domain.nerolidol,
            bisabolol=cp_domain.bisabolol,
            borneol=cp_domain.borneol,
            camphene=cp_domain.camphene,
            carene=cp_domain.carene,
            caryophyllene_oxide=cp_domain.caryophyllene_oxide,
            fenchol=cp_domain.fenchol,
            geraniol=cp_domain.geraniol,
            phellandrene=cp_domain.phellandrene,
            terpineol=cp_domain.terpineol,
            terpinene_alpha=cp_domain.terpinene_alpha,
            terpinene_gamma=cp_domain.terpinene_gamma,
            raw_data=cp_domain.raw_data,
        )
        session.add(cp_orm)
        
    # 4. Save GeneticRelationships
    for rel_domain in sample_domain.genetic_relationships:
        rel_orm = GeneticRelationshipORM(
            id=rel_domain.id,
            sample_id_a=sample_orm.id,
            sample_id_b=rel_domain.sample_id_b,
            strain_name_a=rel_domain.strain_name_a,
            strain_name_b=rel_domain.strain_name_b,
            rsp_a=rel_domain.rsp_a,
            rsp_b=rel_domain.rsp_b,
            distance=rel_domain.distance,
            relationship_type=rel_domain.relationship_type,
            source=rel_domain.source,
        )
        session.add(rel_orm)
        
    # 5. Save StrainAlias
    alias_domain = result["alias"]
    alias_orm = StrainAliasORM(
        id=alias_domain.id,
        canonical_strain_id=strain_orm.id,
        name=alias_domain.name,
        source_name=alias_domain.source_name,
        source_id=alias_domain.source_id,
        confidence=alias_domain.confidence,
    )
    session.add(alias_orm)
    
    # 6. Save SourceGenomicsRecord
    src_domain = result["source_record"]
    src_orm = SourceGenomicsRecordORM(
        id=src_domain.id,
        genomic_sample_id=sample_orm.id,
        source_name=src_domain.source_name,
        source_id=src_domain.source_id,
        source_url=src_domain.source_url,
        metadata_fields=src_domain.metadata_fields,
        chemical_fields=src_domain.chemical_fields,
        variant_fields=src_domain.variant_fields,
        payload=src_domain.payload,
    )
    session.add(src_orm)

async def load_state_from_db(session) -> dict:
    """Wrapper function to load state with caching."""
    import sys
    if "pytest" in sys.modules:
        return await load_state_from_db_internal(session)
        
    global _db_state_cache
    if _db_state_cache is not None:
        return _db_state_cache
        
    async with _db_state_lock:
        if _db_state_cache is not None:
            return _db_state_cache
            
        logger.info("Rebuilding database state cache...")
        state = await load_state_from_db_internal(session)
        _db_state_cache = state
        return _db_state_cache

async def load_state_from_db_internal(session) -> dict:
    """Dynamically reconstruct state from DB to feed viz graph/matrices."""
    stmt_samples = select(GenomicSampleORM).outerjoin(ChemicalProfileORM).options(
        selectinload(GenomicSampleORM.chemical_profile)
    )
    samples_db = (await session.execute(stmt_samples)).scalars().all()
    
    # Pre-fetch all genetic relationships in one query to avoid N+1 query loop
    stmt_all_rels = select(GeneticRelationshipORM)
    rels_db_all = (await session.execute(stmt_all_rels)).scalars().all()
    
    # Group by sample_id_a in memory
    rels_by_sample = {}
    for r in rels_db_all:
        rels_by_sample.setdefault(r.sample_id_a, []).append(r)
        
    from src.models.genomic_sample import GenomicSample, ChemicalProfile as DomainChemicalProfile, GeneticRelationship as DomainGeneticRelationship
    
    domain_samples = []
    for s_orm in samples_db:
        cp_domain = None
        if s_orm.chemical_profile:
            cp_orm = s_orm.chemical_profile
            cp_domain = DomainChemicalProfile(
                id=cp_orm.id,
                sample_id=cp_orm.sample_id,
                thc=cp_orm.thc,
                thca=cp_orm.thca,
                cbd=cp_orm.cbd,
                cbda=cp_orm.cbda,
                thcv=cp_orm.thcv,
                cbc=cp_orm.cbc,
                cbg=cp_orm.cbg,
                cbn=cp_orm.cbn,
                myrcene=cp_orm.myrcene,
                limonene=cp_orm.limonene,
                caryophyllene=cp_orm.caryophyllene,
                pinene_alpha=cp_orm.pinene_alpha,
                pinene_beta=cp_orm.pinene_beta,
                linalool=cp_orm.linalool,
                humulene=cp_orm.humulene,
                terpinolene=cp_orm.terpinolene,
                ocimene=cp_orm.ocimene,
                nerolidol=cp_orm.nerolidol,
                bisabolol=cp_orm.bisabolol,
                borneol=cp_orm.borneol,
                camphene=cp_orm.camphene,
                carene=cp_orm.carene,
                caryophyllene_oxide=cp_orm.caryophyllene_oxide,
                fenchol=cp_orm.fenchol,
                geraniol=cp_orm.geraniol,
                phellandrene=cp_orm.phellandrene,
                terpineol=cp_orm.terpineol,
                terpinene_alpha=cp_orm.terpinene_alpha,
                terpinene_gamma=cp_orm.terpinene_gamma,
                raw_data=cp_orm.raw_data or {},
            )
            
        rels_db = rels_by_sample.get(s_orm.id, [])
        domain_rels = [
            DomainGeneticRelationship(
                id=r.id,
                sample_id_a=r.sample_id_a,
                sample_id_b=r.sample_id_b,
                strain_name_a=r.strain_name_a,
                strain_name_b=r.strain_name_b,
                rsp_a=r.rsp_a,
                rsp_b=r.rsp_b,
                distance=r.distance,
                relationship_type=r.relationship_type,
                source=r.source,
            ) for r in rels_db
        ]
        
        s_domain = GenomicSample(
            id=s_orm.id,
            canonical_strain_id=s_orm.canonical_strain_id,
            rsp_number=s_orm.rsp_number or "",
            sample_name=s_orm.sample_name or "",
            strain_name=s_orm.strain_name or "",
            grower=s_orm.grower,
            accession_date=s_orm.accession_date,
            reported_sex=s_orm.reported_sex,
            report_type=s_orm.report_type,
            rarity=s_orm.rarity,
            plant_type=s_orm.plant_type,
            heterozygosity=s_orm.heterozygosity,
            y_ratio=s_orm.y_ratio,
            transaction_id=s_orm.transaction_id,
            shasum_hash=s_orm.shasum_hash,
            data_files=s_orm.data_files or [],
            source=s_orm.source or "kannapedia",
            source_url=s_orm.source_url,
            is_complete=s_orm.is_complete or False,
            chemical_profile=cp_domain,
            genetic_relationships=domain_rels,
        )
        domain_samples.append(s_domain)

    # Load all canonical strains to ensure we have placeholder samples for strains without genomic data
    stmt_strains = select(CanonicalStrainORM).options(
        selectinload(CanonicalStrainORM.aliases)
    )
    strains_db = (await session.execute(stmt_strains)).scalars().all()
    
    sampled_strain_ids = {s.canonical_strain_id for s in samples_db if s.canonical_strain_id}
    for strain in strains_db:
        if strain.id not in sampled_strain_ids:
            # Try to determine source from aliases, default to 'forum'
            source = "forum"
            if strain.aliases:
                for alias in strain.aliases:
                    if alias.source_name in ("seedfinder", "forum"):
                        source = alias.source_name
                        break
            
            s_domain = GenomicSample(
                id=f"PLACEHOLDER-{strain.id}",
                canonical_strain_id=strain.id,
                rsp_number=f"PLACEHOLDER-{strain.primary_name}",
                sample_name=strain.primary_name,
                strain_name=strain.primary_name,
                source=source,
                is_complete=False,
            )
            domain_samples.append(s_domain)
        
    from src.genomics.data_loader import load_strain_data_from_samples
    strains_data, relationships = load_strain_data_from_samples(domain_samples)
    from src.genomics.terpene_analysis import calculate_terpene_relationships
    terpene_relationships = calculate_terpene_relationships(strains_data)
    
    # Extract lineage relationships from canonical strain lineage data
    # SeedFinder stores full ancestor trees, so we only use the first 2-3
    # entries as direct parents to avoid cluttering the graph.
    lineage_relationships = []
    seen_lineage_keys = set()
    known_strains = set(strains_data.keys())
    import re as _re
    for strain in strains_db:
        if not strain.lineage:
            continue
        parent_names = []
        if isinstance(strain.lineage, list):
            # Take only the first 2-3 entries as direct parents
            for entry in strain.lineage[:3]:
                if isinstance(entry, dict) and entry.get("name"):
                    parent_names.append(entry["name"])
                elif isinstance(entry, str):
                    parent_names.append(entry)
        elif isinstance(strain.lineage, dict):
            for key in ("mother", "father", "parent1", "parent2"):
                if key in strain.lineage:
                    parent_names.append(str(strain.lineage[key]))
        
        child_name = strain.primary_name
        for parent_name in parent_names:
            # Skip generic/meta parent names
            if parent_name.lower() in ("sativa", "indica", "hybrid", "ruderalis",
                                       "unknown strain", "unknown hybrid",
                                       "unknown mostly indica", "unknown mostly sativa"):
                continue
            # Try to match parent to an existing strain in the graph
            # using case-insensitive + underscore normalization
            matched_parent = None
            parent_norm = _re.sub(r"[^a-z0-9]", "", parent_name.lower())
            for known in known_strains:
                known_norm = _re.sub(r"[^a-z0-9]", "", known.lower())
                if known_norm == parent_norm:
                    matched_parent = known
                    break
            if matched_parent and matched_parent != child_name:
                lineage_key = (matched_parent, child_name)
                if lineage_key not in seen_lineage_keys:
                    seen_lineage_keys.add(lineage_key)
                    lineage_relationships.append({
                        "from": matched_parent,
                        "to": child_name,
                        "distance": 0.1,  # Close distance for direct parent-child
                        "type": "lineage",
                    })
    
    logger.info("Extracted %d lineage relationships from strain lineage data", len(lineage_relationships))
    
    return {
        "strains_data": strains_data,
        "relationships": relationships,
        "terpene_relationships": terpene_relationships,
        "lineage_relationships": lineage_relationships,
        "samples": domain_samples,
    }

import asyncio
CACHE_FILE = os.path.join(os.path.dirname(__file__), "data", "translations_cache.json")
cache_lock = asyncio.Lock()
_translation_cache = None

async def load_cache():
    import json
    global _translation_cache
    if _translation_cache is not None:
        return _translation_cache
    
    async with cache_lock:
        if _translation_cache is not None:
            return _translation_cache
            
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    _translation_cache = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load translation cache: {e}")
                _translation_cache = {}
        else:
            _translation_cache = {}
        return _translation_cache

async def save_cache():
    import json
    global _translation_cache
    if _translation_cache is None:
        return
    async with cache_lock:
        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(_translation_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save translation cache: {e}")

async def translate_to_english(text: str) -> dict:
    import httpx
    if not text:
        return {"translated_text": "", "detected_language": "en"}
    
    cleaned_text = text.strip()
    if not cleaned_text:
        return {"translated_text": "", "detected_language": "en"}
        
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": "en",
        "dt": "t",
        "q": cleaned_text
    }
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                translated_segments = []
                if data and isinstance(data, list) and len(data) > 0:
                    segments = data[0]
                    if isinstance(segments, list):
                        for segment in segments:
                            if isinstance(segment, list) and len(segment) > 0:
                                translated_segments.append(segment[0])
                    
                    detected_language = "auto"
                    if len(data) > 2 and isinstance(data[2], str):
                        detected_language = data[2]
                        
                    translated_text = "".join(translated_segments)
                    return {
                        "translated_text": translated_text,
                        "detected_language": detected_language
                    }
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        
    return {"translated_text": text, "detected_language": "unknown"}

async def get_translation_cached(text: str) -> dict:
    if not text:
        return {"translated_text": "", "detected_language": "en"}
        
    cache = await load_cache()
    key = text.strip()
    if key in cache:
        return cache[key]
        
    res = await translate_to_english(text)
    
    if res["detected_language"] != "unknown":
        cache[key] = res
        await save_cache()
        
    return res

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load initial data and bootstrap DB from CSV files on startup."""
    await init_db()
    data_dir = os.getenv("KANNAPEDIA_DATA_DIR", "")
    if data_dir and os.path.isdir(data_dir):
        logger.info("Checking database bootstrapping status from %s", data_dir)
        async for session in get_session():
            stmt = select(func.count(CanonicalStrainORM.id))
            count = (await session.execute(stmt)).scalar() or 0
            if count == 0:
                logger.info("Database is empty, bootstrapping from CSV data...")
                samples = csv_directory_to_samples(data_dir)
                existing_strains = {}
                for sample in samples:
                    # Re-use resolve strain
                    from src.etl.kannapedia_etl import _resolve_strain
                    from src.models.strain import StrainAlias
                    from src.models.source_record import SourceGenomicsRecord
                    
                    strain = _resolve_strain(sample.strain_name, existing_strains)
                    sample.canonical_strain_id = strain.id
                    
                    alias = StrainAlias(
                        canonical_strain_id=strain.id,
                        name=sample.strain_name,
                        source_name="kannapedia",
                        source_id=sample.rsp_number,
                    )
                    
                    source_record = SourceGenomicsRecord(
                        source_id=sample.rsp_number,
                        source_url=sample.source_url,
                        metadata_fields={},
                        chemical_fields={},
                        variant_fields=[],
                        payload={},
                    )
                    source_record.genomic_sample_id = sample.id
                    
                    result = {
                        "sample": sample,
                        "source_record": source_record,
                        "strain": strain,
                        "alias": alias,
                    }
                    await save_domain_models_to_db(session, result)
                await session.commit()
                invalidate_db_state_cache()
                logger.info("Successfully bootstrapped %d strains to database.", len(samples))
            else:
                logger.info("Database already contains %d strains. Skipping bootstrap.", count)
    else:
        logger.info("No KANNAPEDIA_DATA_DIR set, database starts as-is.")
        
    # Pre-populate cache on startup
    logger.info("Pre-populating database state cache on startup...")
    try:
        async for session in get_session():
            await load_state_from_db(session)
            break
    except Exception as e:
        logger.error(f"Failed to pre-populate database state cache: {e}")
        
    yield
    logger.info("treesearch-service stopped")

app = FastAPI(
    title="treesearch-service",
    description=(
        "Unified cannabis data warehouse + analysis API. "
        "Provides strain search, genomic analysis, terpene profiling, "
        "and network data for the visualization frontend."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# Allow requests from the treesearch-client frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- Health ----- #

@app.get("/health")
async def health():
    try:
        async for session in get_session():
            strain_count = (await session.execute(select(func.count(CanonicalStrainORM.id)))).scalar() or 0
            obs_count = (await session.execute(select(func.count(ObservationORM.id)))).scalar() or 0
            return {
                "status": "ok",
                "database": "connected",
                "strains_loaded": strain_count,
                "observations_loaded": obs_count,
            }
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

# ----- Root ----- #

@app.get("/")
async def root():
    """Root endpoint — redirects to docs."""
    return {"service": "treesearch-service", "docs": "/docs"}

# ----- Network Data API ----- #

@app.get("/api/network-data")
async def network_data():
    """Full network data payload for the frontend graph."""
    async for session in get_session():
        state = await load_state_from_db(session)
        data = build_network_data(
            state["strains_data"],
            state["relationships"],
            state["terpene_relationships"],
            state.get("lineage_relationships"),
        )
        return data

# ----- Strain List & Search ----- #

@app.get("/api/strains")
async def list_strains(
    complete_only: bool = False,
    search: str = "",
):
    """List all known strains with optional filtering, including live SeedFinder lookup fallback."""
    async for session in get_session():
        stmt = select(CanonicalStrainORM).options(
            selectinload(CanonicalStrainORM.genomic_samples).selectinload(GenomicSampleORM.chemical_profile)
        )
        if search:
            # Handle both spaces and underscores in strain names
            search_space = search.replace("_", " ")
            search_underscore = search.replace(" ", "_")
            stmt = stmt.where(
                or_(
                    CanonicalStrainORM.primary_name.ilike(f"%{search}%"),
                    CanonicalStrainORM.primary_name.ilike(f"%{search_space}%"),
                    CanonicalStrainORM.primary_name.ilike(f"%{search_underscore}%"),
                )
            )
        
        strains = (await session.execute(stmt)).scalars().all()
        results = []
        
        for s in strains:
            samples = s.genomic_samples
            sample = None
            if samples:
                def sample_pref(sm):
                    score = 0
                    if sm.is_complete:
                        score += 10
                    if sm.source == "manual":
                        score += 5
                    elif sm.source == "kannapedia":
                        score += 3
                    elif sm.source == "seedfinder":
                        score += 2
                    else:
                        score += 1
                    return score
                samples_sorted = sorted(samples, key=sample_pref, reverse=True)
                sample = samples_sorted[0]
            
            is_complete = sample.is_complete if sample else False
            
            terpene_summary = {}
            if sample and sample.chemical_profile:
                normalized = normalize_terpene_profile(sample.chemical_profile.terpene_dict)
                sorted_terps = sorted(normalized.items(), key=lambda x: x[1], reverse=True)[:3]
                terpene_summary = {k: round(v, 3) for k, v in sorted_terps}
                
            results.append({
                "name": s.primary_name,
                "rsp": sample.rsp_number if sample else "",
                "complete": is_complete,
                "has_terpenes": bool(terpene_summary),
                "dominant_terpenes": terpene_summary,
            })
            
        # If search query is non-empty and at least 3 characters, also query SeedFinder!
        if search and len(search.strip()) >= 3:
            try:
                from src.collectors.seedfinder_collector import search_seedfinder
                sf_results = await search_seedfinder(search, limit=10)
                local_names = {r["name"].lower().replace("_", " ") for r in results}
                for sf in sf_results:
                    sf_name_normalized = sf["name"].lower().replace("_", " ")
                    if sf_name_normalized not in local_names:
                        results.append({
                            "name": f"{sf['name']} ({sf['breeder']})",
                            "rsp": "",
                            "complete": False,
                            "has_terpenes": False,
                            "dominant_terpenes": {},
                            "source": "seedfinder",
                            "strain_slug": sf["strain_slug"],
                            "breeder_slug": sf["breeder_slug"],
                            "real_name": sf["name"],
                        })
            except Exception as e:
                logger.error(f"SeedFinder live search failed: {e}")
            
            # Forum search fallback: if no local or SeedFinder results found, search the forums!
            if not results:
                try:
                    import asyncio
                    from src.scraper_client import ScraperClient
                    scraper_client = ScraperClient()
                    
                    tasks = [
                        scraper_client.collect({
                            "source": "discourse",
                            "base_url": "https://overgrow.com",
                            "forum_name": "overgrow",
                            "query": search,
                            "limit": 1
                        }),
                        scraper_client.collect({
                            "source": "xenforo",
                            "base_url": "https://www.rollitup.org",
                            "forum_name": "rollitup",
                            "query": search,
                            "limit": 1
                        }),
                        scraper_client.collect({
                            "source": "xenforo",
                            "base_url": "https://www.thcfarmer.com",
                            "forum_name": "thcfarmer",
                            "query": search,
                            "limit": 1
                        }),
                        scraper_client.collect({
                            "source": "xenforo",
                            "base_url": "https://www.icmag.com",
                            "forum_name": "icmag",
                            "query": search,
                            "limit": 1
                        })
                    ]
                    forum_results = await asyncio.gather(*tasks, return_exceptions=True)
                    await scraper_client.close()
                    
                    total_posts = 0
                    for fr in forum_results:
                        if isinstance(fr, dict) and "items" in fr:
                            total_posts += len(fr["items"])
                            
                    if total_posts > 0:
                        results.append({
                            "name": f"{search} (Forum Import)",
                            "rsp": "",
                            "complete": False,
                            "has_terpenes": False,
                            "dominant_terpenes": {},
                            "source": "forum",
                            "strain_slug": search.lower().replace(" ", "-"),
                            "breeder_slug": "forum-import",
                            "real_name": search,
                        })
                except Exception as e:
                    logger.error(f"Forum fallback search failed: {e}")
            
        return {"strains": results, "count": len(results)}


def _is_post_relevant(body: str, title: str, strain_name: str) -> bool:
    if not strain_name:
        return False
    body_lower = (body or "").lower()
    title_lower = (title or "").lower()
    strain_lower = strain_name.lower()

    # 1. Direct match on either title or body
    if strain_lower in title_lower:
        # If thread title contains the strain name, the entire thread is relevant
        return True
    if strain_lower in body_lower:
        return True

    # 2. Match with punctuation/spacing normalized (e.g. "Jack's Cleaner" -> "jackscleaner")
    import re
    strain_norm = re.sub(r"[^a-z0-9]", "", strain_lower)
    title_norm = re.sub(r"[^a-z0-9]", "", title_lower)
    body_norm = re.sub(r"[^a-z0-9]", "", body_lower)

    if strain_norm in title_norm:
        return True
    if strain_norm in body_norm:
        return True

    # 3. Match without trailing 's' if the strain name is e.g. "jacks" (to catch "jack cleaner")
    if strain_norm.startswith("jacks"):
        alt_norm = strain_norm.replace("jacks", "jack", 1)
        if alt_norm in title_norm or alt_norm in body_norm:
            return True

    return False


async def _save_forum_posts_to_db(session, posts: list[dict], source_name: str, canonical_id: str, reported_strain: str) -> tuple[int, int]:
    from datetime import datetime
    from src.ml.clustering import classify_images_batch
    posts_saved = 0
    images_saved = 0

    # 1. Identify posts to be saved and gather all their image URLs
    valid_posts = []
    all_image_urls = []
    
    for p in posts:
        # Check if exists
        stmt = select(ObservationORM).where(ObservationORM.source_id == str(p.get("id")))
        existing = (await session.execute(stmt)).scalars().first()
        if existing:
            continue

        title = p.get("title", "")
        body = p.get("body", "")

        # Check relevance — skip filter for OP posts (post_number == 1)
        # ONLY if the thread title itself contains the strain name. Otherwise,
        # the OP post must be checked for relevance just like other posts.
        post_number = p.get("post_number")
        is_op = post_number == 1
        title_relevant = _is_post_relevant("", title, reported_strain)
        
        if not (is_op and title_relevant) and not _is_post_relevant(body, title, reported_strain):
            continue

        valid_posts.append(p)
        for url in p.get("image_urls", []):
            cleaned_url = clean_forum_image_url(url)
            if cleaned_url not in all_image_urls:
                all_image_urls.append(cleaned_url)

    # 2. Concurrently classify all unique image URLs in parallel batches (15 at a time)
    classified_results = {}
    if all_image_urls:
        logger.info(f"Concurrently classifying {len(all_image_urls)} image URLs for {reported_strain} ({source_name})...")
        classified_results = await classify_images_batch(all_image_urls, batch_size=15)

    # 3. Create observation records and save images that depict budding plants
    for p in valid_posts:
        title = p.get("title", "")
        body = p.get("body", "")
        created_at_str = p.get("created_at")
        dt = datetime.utcnow()
        if created_at_str:
            try:
                dt = datetime.fromisoformat(created_at_str).replace(tzinfo=None)
            except Exception:
                try:
                    dt = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    dt = datetime.utcnow()

        obs = ObservationORM(
            source_name=source_name,
            source_id=str(p.get("id")),
            source_url=p.get("url"),
            author=p.get("author"),
            observed_at=dt,
            reported_strain_name=reported_strain,
            canonical_strain_id=canonical_id,
            raw_text=f"Title: {title}\n\n{body}"
        )
        session.add(obs)
        await session.flush()
        posts_saved += 1

        for url in p.get("image_urls", []):
            cleaned_url = clean_forum_image_url(url)
            if classified_results.get(cleaned_url, True):  # Default to True on failure
                img_orm = ObservationImageORM(
                    observation_id=obs.id,
                    image_url=cleaned_url
                )
                session.add(img_orm)
                images_saved += 1
                
    return posts_saved, images_saved


def parse_genetics_from_snippets(snippets: list[str], strain_name: str) -> list[str]:
    import re
    # Normalize strain name
    name_norm = re.sub(r'[^a-zA-Z0-9]', '', strain_name.lower())
    
    # Try different regex strategies across all snippets
    for snip in snippets:
        snip_clean = snip.replace('\xa0', ' ').replace('\u200e', '')
        
        # Strategy 1: Look for "StrainName »»» Parent1 x Parent2"
        match = re.search(r'»»»\s*([^·\n]+)', snip_clean)
        if match:
            cross_text = match.group(1).strip()
            if any(x in cross_text.lower() for x in [' x ', '×', ' x']):
                parts = re.split(r'\s+[xX×]\s+|\s+x\s+|_x_|_X_', cross_text)
                parents = [p.strip() for p in parts if p.strip()]
                parents = [p for p in parents if len(p) > 2 and p.lower() not in ["mostly indica", "mostly sativa", "hybrid"]]
                if len(parents) >= 2:
                    return parents
                    
        # Strategy 2: Look for "Genetic:Parent1 x Parent2"
        match_genetic = re.search(r'Genetic\s*:\s*([^.\n]+)', snip_clean, re.IGNORECASE)
        if match_genetic:
            cross_text = match_genetic.group(1).strip()
            cross_text = re.split(r'flowering|characteristics|strong|medicinal', cross_text, flags=re.IGNORECASE)[0].strip()
            if any(x in cross_text.lower() for x in [' x ', '×', ' x']):
                parts = re.split(r'\s+[xX×]\s+|\s+x\s+|_x_|_X_', cross_text)
                parents = [p.strip() for p in parts if p.strip()]
                parents = [p for p in parents if len(p) > 2 and p.lower() not in ["mostly indica", "mostly sativa", "hybrid"]]
                if len(parents) >= 2:
                    return parents
                    
        # Strategy 3: Heuristic for Capitalized Words separated by 'x'
        for match in re.finditer(r'([A-Z][a-zA-Z0-9\s\']+)\s+[xX×*]\s+([A-Z][a-zA-Z0-9\s\']+)(?:\s+[xX×*]\s+([A-Z][a-zA-Z0-9\s\']+))?', snip_clean):
            parents = [p.strip() for p in match.groups() if p]
            parents = [p for p in parents if len(p) > 2 and p.lower() not in ["mostly indica", "mostly sativa", "hybrid"] and len(p) < 40]
            if len(parents) >= 2:
                return parents

    return []


async def fallback_search_genetics(strain_name: str) -> list[str]:
    import os
    import sys
    import json
    import asyncio
    
    # Candidate python paths with ddgs installed
    candidates = [
        "/home/lazycat/github/rods-project/sun/scraper-service/.venv/bin/python",
        "/home/lazycat/github/rods-project/sun/scraper-service/venv/bin/python",
        "/home/lazycat/github/rods-project/sun/trading-service/.venv/bin/python",
    ]
    
    python_exe = None
    for c in candidates:
        if os.path.exists(c):
            python_exe = c
            break
            
    if not python_exe:
        python_exe = sys.executable

    # Query DuckDuckGo
    query = f'site:seedfinder.eu "{strain_name}"'
    script = """
import sys
import json
try:
    from ddgs import DDGS
    with DDGS() as ddgs:
        results = list(ddgs.text(sys.argv[1], max_results=10))
    print(json.dumps({"success": True, "results": results}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}))
"""
    
    try:
        proc = await asyncio.create_subprocess_exec(
            python_exe, "-c", script, query,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            data = json.loads(stdout.decode().strip())
            if data.get("success"):
                snippets = [r.get("body", "") for r in data.get("results", []) if r.get("body")]
                return parse_genetics_from_snippets(snippets, strain_name)
            else:
                logger.error(f"DDG fallback search execution error: {data.get('error')}")
        else:
            logger.error(f"DDG fallback search process failed with code {proc.returncode}: {stderr.decode()}")
    except Exception as e:
        logger.error(f"Failed to execute DDG fallback search: {e}")
        
    return []


@app.post("/api/strains/import")
async def import_strain(request: Request):
    payload = await request.json()
    strain_slug = payload.get("strain_slug")
    breeder_slug = payload.get("breeder_slug")
    real_name = payload.get("real_name")  # Passed by SeedFinder search results
    force = payload.get("force", False)
    query = payload.get("query")

    if query and not strain_slug:
        strain_slug = query
    if query and not breeder_slug:
        breeder_slug = "free-text"

    if not strain_slug or not breeder_slug:
        return JSONResponse({"error": "Missing strain_slug or breeder_slug"}, status_code=400)

    from fastapi.responses import StreamingResponse
    import json

    async def event_generator():
        total_posts = 0
        total_images = 0

        yield json.dumps({"type": "progress", "message": "Initializing...", "posts": 0, "images": 0}) + "\n"

        async for session in get_session():
            nonlocal strain_slug, breeder_slug, real_name
            
            # If free-text search or query parameter is present, resolve it first
            if breeder_slug == "free-text" or query:
                lookup_name = query or strain_slug
                resolved_name = await get_canonical_strain_name(session, lookup_name)
                if resolved_name:
                    # Found in DB! Let's get the CanonicalStrainORM
                    stmt_cs = select(CanonicalStrainORM).where(CanonicalStrainORM.primary_name == resolved_name)
                    strain_orm = (await session.execute(stmt_cs)).scalars().first()
                    if strain_orm:
                        # Check if it has any observations or genomic sample
                        stmt_sample = select(GenomicSampleORM).where(GenomicSampleORM.canonical_strain_id == strain_orm.id)
                        has_sample = (await session.execute(stmt_sample)).scalars().first() is not None
                        has_data = (strain_orm.observation_count and strain_orm.observation_count > 0) or has_sample
                        
                        if has_data and not force:
                            logger.info(f"Free-text search '{lookup_name}' resolved to existing DB strain '{resolved_name}' with data. Returning directly.")
                            detail_data = await strain_detail(strain_orm.primary_name)
                            yield json.dumps({"type": "done", "data": detail_data}) + "\n"
                            return
                        else:
                            # Needs enrichment. Align slugs and names with this strain
                            logger.info(f"Free-text search '{lookup_name}' resolved to '{resolved_name}' but needs enrichment (force={force}, has_data={has_data})")
                            primary_name = strain_orm.primary_name
                            real_name = primary_name
                            strain_slug = primary_name.lower().replace("_", "-")
                            # Eagerly load the breeder name
                            stmt_br = select(BreederORM).where(BreederORM.id == strain_orm.breeder_id)
                            br = (await session.execute(stmt_br)).scalars().first() if strain_orm.breeder_id else None
                            
                            # Check aliases for a real seedfinder breeder slug
                            stmt_aliases = select(StrainAliasORM).where(StrainAliasORM.canonical_strain_id == strain_orm.id)
                            aliases = (await session.execute(stmt_aliases)).scalars().all()
                            found_breeder_slug = None
                            for a in aliases:
                                if a.source_name == "seedfinder" and a.source_id and ":" in a.source_id:
                                    parts = a.source_id.split(":", 1)
                                    if len(parts) > 1 and parts[1] != "seedfinder" and parts[1] != "forum-import":
                                        found_breeder_slug = parts[1]
                                        break
                                        
                            if found_breeder_slug:
                                breeder_slug = found_breeder_slug
                            elif br and br.name and br.name.lower() not in ("unknown", "unknown breeder", "seedfinder"):
                                breeder_slug = br.name.lower().replace(" ", "-").replace("'", "").replace("’", "").replace(".", "")
                            else:
                                breeder_slug = "forum-import"
                            if force:
                                from sqlalchemy import delete
                                obs_stmt = select(ObservationORM.id).where(ObservationORM.canonical_strain_id == strain_orm.id)
                                obs_ids = (await session.execute(obs_stmt)).scalars().all()
                                if obs_ids:
                                    await session.execute(delete(ObservationImageORM).where(ObservationImageORM.observation_id.in_(obs_ids)))
                                    await session.execute(delete(ObservationORM).where(ObservationORM.id.in_(obs_ids)))
                                strain_orm.observation_count = 0
                                await session.flush()
                else:
                    # Not found in DB! Let's query SeedFinder for matches
                    logger.info(f"Free-text search '{lookup_name}' not found in DB. Searching SeedFinder...")
                    yield json.dumps({"type": "progress", "message": f"Searching SeedFinder for '{lookup_name}'...", "posts": 0, "images": 0}) + "\n"
                    
                    from src.collectors.seedfinder_collector import search_seedfinder
                    try:
                        sf_results = await search_seedfinder(lookup_name, limit=5)
                    except Exception as e:
                        logger.error(f"SeedFinder search failed in free-text flow for '{lookup_name}': {e}")
                        sf_results = []
                        
                    if sf_results:
                        best_match = sf_results[0]
                        strain_slug = best_match["strain_slug"]
                        breeder_slug = best_match["breeder_slug"]
                        real_name = best_match["name"]
                        logger.info(f"Free-text search '{lookup_name}' matched SeedFinder strain '{real_name}' ({breeder_slug})")
                        yield json.dumps({"type": "progress", "message": f"Found match on SeedFinder: '{real_name}' by '{breeder_slug}'", "posts": 0, "images": 0}) + "\n"
                    else:
                        logger.info(f"Free-text search '{lookup_name}' had no SeedFinder matches. Falling back to Forum Import.")
                        strain_slug = lookup_name.lower().replace(" ", "-")
                        breeder_slug = "forum-import"
                        real_name = lookup_name
                        yield json.dumps({"type": "progress", "message": f"No SeedFinder match. Falling back to Forum Import for '{lookup_name}'...", "posts": 0, "images": 0}) + "\n"

            # ── Step 1: Check if already imported via alias ──
            alias_source_name = "forum" if breeder_slug == "forum-import" else "seedfinder"
            stmt_alias = select(StrainAliasORM).where(
                (StrainAliasORM.source_name == alias_source_name) & 
                (StrainAliasORM.source_id == f"{strain_slug}:{breeder_slug}")
            )
            alias = (await session.execute(stmt_alias)).scalars().first()
            if alias:
                stmt_cs = select(CanonicalStrainORM).where(CanonicalStrainORM.id == alias.canonical_strain_id)
                strain_orm = (await session.execute(stmt_cs)).scalars().first()
                if strain_orm and not force:
                    detail_data = await strain_detail(strain_orm.primary_name)
                    yield json.dumps({"type": "done", "data": detail_data}) + "\n"
                    return
                
                # If force=True, clean up existing observations and re-scrape
                if force:
                    if strain_orm:
                        from sqlalchemy import delete
                        obs_stmt = select(ObservationORM.id).where(ObservationORM.canonical_strain_id == strain_orm.id)
                        obs_ids = (await session.execute(obs_stmt)).scalars().all()
                        if obs_ids:
                            await session.execute(delete(ObservationImageORM).where(ObservationImageORM.observation_id.in_(obs_ids)))
                            await session.execute(delete(ObservationORM).where(ObservationORM.id.in_(obs_ids)))
                        strain_orm.observation_count = 0
                        await session.flush()
                    await session.delete(alias)
                    await session.flush()

            # ── Step 2: Check if strain already exists in DB by name ──
            # This handles CSV-bootstrapped strains and previously imported ones
            search_name = real_name or strain_slug.replace("-", " ").replace("_", " ")
            search_name_underscore = search_name.replace(" ", "_")
            stmt_existing = select(CanonicalStrainORM).where(
                or_(
                    CanonicalStrainORM.primary_name.ilike(search_name),
                    CanonicalStrainORM.primary_name.ilike(search_name_underscore),
                    CanonicalStrainORM.primary_name.ilike(search_name.replace(" ", "")),
                )
            )
            existing_strain = (await session.execute(stmt_existing)).scalars().first()

            yield json.dumps({"type": "progress", "message": "Fetching metadata...", "posts": 0, "images": 0}) + "\n"

            strain_orm = None
            primary_name = None
            sf_data = None

            if breeder_slug == "forum-import":
                primary_name = search_name.title()
                sf_data = {
                    "name": primary_name,
                    "breeder": "Unknown Breeder",
                    "type": "Unknown",
                    "flowering_time_days": None,
                    "description": f"Imported from forum discussions for {primary_name}.",
                    "lineage": {},
                }
            elif existing_strain and not force:
                # Strain exists in DB — use it directly, skip SeedFinder scrape
                strain_orm = existing_strain
                primary_name = strain_orm.primary_name
                logger.info(f"Found existing strain in DB: {primary_name} (id={strain_orm.id})")
                yield json.dumps({"type": "progress", "message": f"Found {primary_name} in database, enriching with forum data...", "posts": 0, "images": 0}) + "\n"
            else:
                # Try SeedFinder scrape
                from src.collectors.seedfinder_collector import scrape_seedfinder_strain
                sf_data = await scrape_seedfinder_strain(strain_slug, breeder_slug)
                if not sf_data or not sf_data.get("name"):
                    # SeedFinder failed (404, etc.) — fall back to creating strain from name
                    logger.warning(f"SeedFinder returned no data for {strain_slug}/{breeder_slug}, using name fallback")
                    primary_name = search_name.replace("-", " ").title()
                    sf_data = {
                        "name": primary_name,
                        "breeder": breeder_slug.replace("-", " ").title() if breeder_slug != "seedfinder" else "Unknown Breeder",
                        "type": None,
                        "flowering_time_days": None,
                        "description": None,
                        "lineage": {},
                    }
                    yield json.dumps({"type": "progress", "message": f"Querying DuckDuckGo fallback for {primary_name} lineage...", "posts": 0, "images": 0}) + "\n"
                    parsed_parents = await fallback_search_genetics(primary_name)
                    if parsed_parents:
                        logger.info(f"DuckDuckGo fallback successfully parsed parents for {primary_name}: {parsed_parents}")
                        sf_data["lineage"] = [{"name": p} for p in parsed_parents]
                elif not sf_data.get("lineage"):
                    primary_name = sf_data["name"]
                    yield json.dumps({"type": "progress", "message": f"Querying DuckDuckGo fallback for {primary_name} lineage...", "posts": 0, "images": 0}) + "\n"
                    parsed_parents = await fallback_search_genetics(primary_name)
                    if parsed_parents:
                        logger.info(f"DuckDuckGo fallback successfully parsed parents for {primary_name}: {parsed_parents}")
                        sf_data["lineage"] = [{"name": p} for p in parsed_parents]

            # ── Step 3: Create/update CanonicalStrain if needed ──
            if not strain_orm:
                # Need to create or find the strain
                breeder_name = (sf_data.get("breeder") or breeder_slug.replace("-", " ").title()) if sf_data else "Unknown Breeder"
                stmt_breeder = select(BreederORM).where(BreederORM.name.ilike(breeder_name))
                breeder = (await session.execute(stmt_breeder)).scalars().first()
                if not breeder:
                    breeder = BreederORM(name=breeder_name)
                    session.add(breeder)
                    await session.flush()

                primary_name = sf_data["name"] if sf_data else search_name.title()
                
                # Check if strain already exists by name
                stmt_cs_name = select(CanonicalStrainORM).where(CanonicalStrainORM.primary_name.ilike(primary_name))
                strain_orm = (await session.execute(stmt_cs_name)).scalars().first()
                if not strain_orm:
                    canonical_name = primary_name.replace(" ", "_")
                    stmt_cs_canon = select(CanonicalStrainORM).where(CanonicalStrainORM.primary_name.ilike(canonical_name))
                    strain_orm = (await session.execute(stmt_cs_canon)).scalars().first()
                    if not strain_orm:
                        strain_orm = CanonicalStrainORM(
                            primary_name=canonical_name,
                            breeder_id=breeder.id,
                            strain_type=sf_data.get("type") if sf_data else None,
                            avg_flowering_days=sf_data.get("flowering_time_days") if sf_data else None,
                            description=sf_data.get("description") if sf_data else None,
                            lineage=sf_data.get("lineage") or {} if sf_data else {},
                        )
                        session.add(strain_orm)
                        await session.flush()
                elif sf_data:
                    # Update existing strain with SeedFinder data
                    strain_orm.strain_type = sf_data.get("type") or strain_orm.strain_type
                    strain_orm.avg_flowering_days = sf_data.get("flowering_time_days") or strain_orm.avg_flowering_days
                    strain_orm.description = sf_data.get("description") or strain_orm.description
                    strain_orm.lineage = sf_data.get("lineage") or strain_orm.lineage
                    await session.flush()

            # Use the actual DB strain name for forum searches
            primary_name = strain_orm.primary_name
            # Normalize: "Head_Band" → "Head Band" for better forum search results
            search_query = primary_name.replace("_", " ")

            # ── Step 4: Create alias for caching ──
            alias_orm = StrainAliasORM(
                canonical_strain_id=strain_orm.id,
                name=primary_name,
                source_name=alias_source_name,
                source_id=f"{strain_slug}:{breeder_slug}",
            )
            session.add(alias_orm)
            try:
                await session.flush()
            except Exception:
                # Alias may already exist (unique constraint)
                await session.rollback()
                # Re-fetch session state after rollback
                async for session in get_session():
                    break

            # ── Kannapedia genomic data lookup ──
            yield json.dumps({"type": "progress", "message": "Searching Kannapedia for genomic data...", "posts": 0, "images": 0}) + "\n"

            from src.scraper_client import ScraperClient
            scraper_client = ScraperClient()
            try:
                kannapedia_results = await scraper_client.collect_kannapedia(
                    strain_name=primary_name,
                    limit=3,
                )

                kannapedia_ingested = 0
                for kanna_item in kannapedia_results:
                    try:
                        # Build existing_strains lookup for ETL
                        from src.models.strain import CanonicalStrain as DomainStrain
                        existing_strains_lookup = {
                            strain_orm.primary_name: DomainStrain(
                                id=strain_orm.id,
                                primary_name=strain_orm.primary_name,
                                strain_type=strain_orm.strain_type,
                                lineage=strain_orm.lineage or {},
                                description=strain_orm.description,
                            )
                        }

                        result = ingest_kannapedia_record(kanna_item, existing_strains_lookup)
                        await save_domain_models_to_db(session, result)
                        kannapedia_ingested += 1

                        # Update the canonical strain with chemical averages from the sample
                        sample_domain = result["sample"]
                        if sample_domain.chemical_profile:
                            cp = sample_domain.chemical_profile
                            if cp.thc is not None:
                                strain_orm.avg_thc_pct = cp.thc
                            if cp.cbd is not None:
                                strain_orm.avg_cbd_pct = cp.cbd
                            # Extract dominant terpenes
                            terp_dict = {}
                            for attr in ["myrcene", "limonene", "caryophyllene", "pinene_alpha",
                                         "linalool", "humulene", "terpinolene", "ocimene"]:
                                val = getattr(cp, attr, None)
                                if val and val > 0:
                                    terp_dict[attr] = val
                            if terp_dict:
                                sorted_terps = sorted(terp_dict.items(), key=lambda x: x[1], reverse=True)
                                strain_orm.dominant_terpenes = [t[0] for t in sorted_terps[:5]]
                            await session.flush()

                        logger.info(f"Ingested Kannapedia sample for {primary_name}: RSP={sample_domain.rsp_number}")
                    except Exception as kex:
                        logger.error(f"Failed to ingest Kannapedia record for {primary_name}: {kex}")

                if kannapedia_ingested > 0:
                    yield json.dumps({"type": "progress", "message": f"Kannapedia: {kannapedia_ingested} genomic sample(s) ingested.", "posts": 0, "images": 0}) + "\n"
                    # Clean up any incomplete placeholder samples since we now have real Kannapedia WGS data
                    from sqlalchemy import delete
                    await session.execute(
                        delete(GenomicSampleORM).where(
                            (GenomicSampleORM.canonical_strain_id == strain_orm.id) &
                            (GenomicSampleORM.source != "kannapedia") &
                            (GenomicSampleORM.is_complete == False)
                        )
                    )
                    await session.flush()
                else:
                    yield json.dumps({"type": "progress", "message": "No Kannapedia genomic data found. Creating community placeholder...", "posts": 0, "images": 0}) + "\n"
                    stmt_sample_check = select(GenomicSampleORM).where(GenomicSampleORM.canonical_strain_id == strain_orm.id)
                    existing_sample = (await session.execute(stmt_sample_check)).scalars().first()
                    if not existing_sample:
                        placeholder_source = "forum" if breeder_slug == "forum-import" else "seedfinder"
                        placeholder_sample = GenomicSampleORM(
                            canonical_strain_id=strain_orm.id,
                            rsp_number=f"PLACEHOLDER-{strain_orm.primary_name}",
                            strain_name=strain_orm.primary_name,
                            source=placeholder_source,
                            is_complete=False,
                        )
                        session.add(placeholder_sample)
                        await session.flush()
            except Exception as e:
                logger.error(f"Kannapedia lookup failed for {primary_name}: {e}")
                yield json.dumps({"type": "progress", "message": "Kannapedia lookup failed, creating community placeholder...", "posts": 0, "images": 0}) + "\n"
                stmt_sample_check = select(GenomicSampleORM).where(GenomicSampleORM.canonical_strain_id == strain_orm.id)
                existing_sample = (await session.execute(stmt_sample_check)).scalars().first()
                if not existing_sample:
                    placeholder_source = "forum" if breeder_slug == "forum-import" else "seedfinder"
                    placeholder_sample = GenomicSampleORM(
                        canonical_strain_id=strain_orm.id,
                        rsp_number=f"PLACEHOLDER-{strain_orm.primary_name}",
                        strain_name=strain_orm.primary_name,
                        source=placeholder_source,
                        is_complete=False,
                    )
                    session.add(placeholder_sample)
                    await session.flush()

            yield json.dumps({"type": "progress", "message": "Scraping community forums and Reddit concurrently...", "posts": total_posts, "images": total_images}) + "\n"
            
            async def fetch_overgrow():
                try:
                    from src.collectors.discourse_collector import DiscourseCollector
                    collector = DiscourseCollector(base_url="https://overgrow.com", forum_name="overgrow")
                    items = await collector.search(search_query, limit=30)
                    return "overgrow", items
                except Exception as ex:
                    logger.error(f"Failed to scrape Overgrow for {search_query}: {ex}")
                    return "overgrow", []

            async def fetch_rollitup():
                try:
                    from src.collectors.xenforo_collector import XenForoCollector
                    collector = XenForoCollector(base_url="https://www.rollitup.org", forum_name="rollitup")
                    items = await collector.search(search_query, limit=30)
                    return "rollitup", items
                except Exception as ex:
                    logger.error(f"Failed to scrape Rollitup for {search_query}: {ex}")
                    return "rollitup", []

            async def fetch_thcfarmer():
                try:
                    from src.collectors.xenforo_collector import XenForoCollector
                    collector = XenForoCollector(base_url="https://www.thcfarmer.com", forum_name="thcfarmer")
                    items = await collector.search(search_query, limit=30)
                    return "thcfarmer", items
                except Exception as ex:
                    logger.error(f"Failed to scrape THCFarmer for {search_query}: {ex}")
                    return "thcfarmer", []

            async def fetch_icmag():
                try:
                    from src.collectors.xenforo_collector import XenForoCollector
                    collector = XenForoCollector(base_url="https://www.icmag.com", forum_name="icmag")
                    items = await collector.search(search_query, limit=30)
                    return "icmag", items
                except Exception as ex:
                    logger.error(f"Failed to scrape ICMag for {search_query}: {ex}")
                    return "icmag", []

            async def fetch_reddit():
                try:
                    from src.collectors.reddit_collector import RedditCollector
                    collector = RedditCollector()
                    items = await collector.search(
                        query=search_query,
                        subreddits=["microgrowery", "cannabiscultivation", "trees", "GrowingMarijuana"],
                        limit=20
                    )
                    return "reddit", items
                except Exception as ex:
                    logger.error(f"Failed to scrape Reddit for {search_query}: {ex}")
                    return "reddit", []

            # Scrape forum threads for observations and pictures concurrently
            try:
                scrape_results = await asyncio.gather(
                    fetch_overgrow(),
                    fetch_rollitup(),
                    fetch_thcfarmer(),
                    fetch_icmag(),
                    fetch_reddit()
                )
            finally:
                await scraper_client.close()

            # Process and save results sequentially
            for source_name, items in scrape_results:
                if items:
                    p_saved, i_saved = await _save_forum_posts_to_db(session, items, source_name, strain_orm.id, search_query)
                    total_posts += p_saved
                    total_images += i_saved
                    yield json.dumps({"type": "progress", "message": f"Processed {source_name.title()} ({p_saved} posts, {i_saved} images).", "posts": total_posts, "images": total_images}) + "\n"

            # Update observation count on the canonical strain
            stmt_obs_count = select(func.count()).select_from(ObservationORM).where(
                ObservationORM.canonical_strain_id == strain_orm.id
            )
            obs_count = (await session.execute(stmt_obs_count)).scalar() or 0
            strain_orm.observation_count = obs_count

            await session.commit()
            invalidate_db_state_cache()
            
            detail_data = await strain_detail(strain_orm.primary_name)
            yield json.dumps({"type": "done", "data": detail_data}) + "\n"
            break

    return StreamingResponse(event_generator(), media_type="application/x-ndjson")

# ----- Strain Detail ----- #

@app.get("/api/strains/{strain_name}/detail")
async def strain_detail(strain_name: str):
    """Full detail for a single strain — metadata, chemicals, relationships, and observation notes/quotes."""
    async for session in get_session():
        resolved_name = await get_canonical_strain_name(session, strain_name)
        if not resolved_name:
            return JSONResponse({"error": f"Strain '{strain_name}' not found"}, status_code=404)
            
        stmt = select(CanonicalStrainORM).where(CanonicalStrainORM.primary_name == resolved_name)
        strain = (await session.execute(stmt)).scalars().first()
        if not strain:
            return JSONResponse({"error": f"Strain '{strain_name}' not found"}, status_code=404)
            
        stmt_samples = select(GenomicSampleORM).where(GenomicSampleORM.canonical_strain_id == strain.id).options(
            selectinload(GenomicSampleORM.chemical_profile)
        )
        samples = (await session.execute(stmt_samples)).scalars().all()
        sample = None
        if samples:
            def sample_pref(sm):
                score = 0
                if sm.is_complete:
                    score += 10
                if sm.source == "manual":
                    score += 5
                elif sm.source == "kannapedia":
                    score += 3
                elif sm.source == "seedfinder":
                    score += 2
                else:
                    score += 1
                return score
            samples_sorted = sorted(samples, key=sample_pref, reverse=True)
            sample = samples_sorted[0]

        # Eagerly load breeder for strain-level info
        breeder_name = ""
        if strain.breeder_id:
            stmt_br = select(BreederORM).where(BreederORM.id == strain.breeder_id)
            br = (await session.execute(stmt_br)).scalars().first()
            if br:
                breeder_name = br.name
        
        has_observations = bool(strain.observation_count and strain.observation_count > 0)

        # Translate description to English if not English
        original_desc = strain.description or ""
        translated_desc = ""
        detected_lang = "en"
        
        if original_desc:
            try:
                translation_res = await get_translation_cached(original_desc)
                translated_desc = translation_res.get("translated_text", "")
                detected_lang = translation_res.get("detected_language", "en")
            except Exception as e:
                logger.error(f"Error while translating strain description: {e}")

        # Locate strain_slug and breeder_slug from aliases
        strain_slug = ""
        breeder_slug = ""
        stmt_aliases = select(StrainAliasORM).where(StrainAliasORM.canonical_strain_id == strain.id)
        aliases = (await session.execute(stmt_aliases)).scalars().all()
        for a in aliases:
            if a.source_name in ("seedfinder", "forum") and a.source_id and ":" in a.source_id:
                parts = a.source_id.split(":", 1)
                strain_slug = parts[0]
                breeder_slug = parts[1]
                break
        
        if not strain_slug:
            strain_slug = strain.primary_name.lower().replace(" ", "-").replace("_", "-")
        if not breeder_slug:
            breeder_slug = "forum-import"

        result = {
            "name": strain.primary_name,
            "strain_slug": strain_slug,
            "breeder_slug": breeder_slug,
            "rsp": sample.rsp_number if sample else "",
            "complete": (sample.is_complete if sample else False) or has_observations,
            "source": sample.source if sample else "kannapedia",
            "description": original_desc,
            "translated_description": translated_desc if detected_lang != "en" and translated_desc != original_desc else None,
            "detected_language": detected_lang,
            "strain_type": strain.strain_type or "",
            "breeder": breeder_name,
            "lineage": strain.lineage or {},
            "avg_flowering_days": strain.avg_flowering_days,
            "metadata": {},
            "cannabinoids": {},
            "terpenes": {},
        }
        
        if sample:
            result["metadata"] = {
                "grower": sample.grower,
                "accession_date": sample.accession_date,
                "reported_sex": sample.reported_sex,
                "report_type": sample.report_type,
                "rarity": sample.rarity,
                "plant_type": sample.plant_type,
                "heterozygosity": sample.heterozygosity,
            }
            if sample.chemical_profile:
                cp = sample.chemical_profile
                result["cannabinoids"] = {
                    k: v for k, v in {
                        "THC": cp.thc, "THCA": cp.thca,
                        "CBD": cp.cbd, "CBDA": cp.cbda,
                        "THCV": cp.thcv, "CBC": cp.cbc,
                        "CBG": cp.cbg, "CBN": cp.cbn,
                    }.items() if v is not None
                }
                result["total_thc"] = cp.total_thc
                result["total_cbd"] = cp.total_cbd
                result["terpenes"] = cp.terpene_dict
                
            if sample.transaction_id:
                result["blockchain"] = {
                    "txid": sample.transaction_id,
                    "shasum": sample.shasum_hash,
                }
                
        # Reconstruct relationships dynamically
        state = await load_state_from_db(session)
        
        # Genetic neighbors
        genetic_neighbors = []
        for s1, s2, dist in state["relationships"]:
            if s1 == strain.primary_name:
                genetic_neighbors.append({"strain": s2, "distance": dist})
            elif s2 == strain.primary_name:
                genetic_neighbors.append({"strain": s1, "distance": dist})
        genetic_neighbors.sort(key=lambda x: x["distance"])
        result["genetic_neighbors"] = genetic_neighbors[:20]
        
        # Terpene neighbors
        terpene_neighbors = []
        for rel in state["terpene_relationships"]:
            if rel["from"] == strain.primary_name:
                terpene_neighbors.append({"strain": rel["to"], "distance": rel["distance"]})
            elif rel["to"] == strain.primary_name:
                terpene_neighbors.append({"strain": rel["from"], "distance": rel["distance"]})
        terpene_neighbors.sort(key=lambda x: x["distance"])
        result["terpene_neighbors"] = terpene_neighbors[:20]
        
        # Fetch forum observation quotes, source links, and images
        stmt_obs = select(ObservationORM).where(
            (ObservationORM.canonical_strain_id == strain.id) |
            (ObservationORM.reported_strain_name.ilike(strain.primary_name))
        ).options(
            selectinload(ObservationORM.images)
        )
        observations = (await session.execute(stmt_obs)).scalars().all()
        
        observations_data = []
        for obs in observations:
            imgs = obs.images
            
            observations_data.append({
                "id": obs.id,
                "source_name": obs.source_name,
                "source_url": obs.source_url,
                "author": obs.author,
                "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
                "reported_strain_name": obs.reported_strain_name,
                "raw_text": obs.raw_text,
                "images": [
                    {
                        "id": img.id,
                        "image_url": img.image_url,
                        "local_path": img.local_path,
                        "cluster_id": img.cluster_id,
                    } for img in imgs
                ]
            })
        result["observations"] = observations_data
        
        return result


@app.put("/api/strains/{strain_name}/update")
async def update_strain_metadata(strain_name: str, request: Request):
    """Manually update canonical strain info: breeder, type, flowering days, description, lineage."""
    payload = await request.json()
    async for session in get_session():
        resolved_name = await get_canonical_strain_name(session, strain_name)
        if not resolved_name:
            return JSONResponse({"error": f"Strain '{strain_name}' not found"}, status_code=404)
            
        stmt = select(CanonicalStrainORM).where(CanonicalStrainORM.primary_name == resolved_name)
        strain = (await session.execute(stmt)).scalars().first()
        if not strain:
            return JSONResponse({"error": f"Strain '{strain_name}' not found"}, status_code=404)
            
        # Update breeder if provided
        if "breeder" in payload:
            breeder_name = payload["breeder"].strip() if payload["breeder"] else ""
            if breeder_name:
                stmt_breeder = select(BreederORM).where(BreederORM.name.ilike(breeder_name))
                breeder = (await session.execute(stmt_breeder)).scalars().first()
                if not breeder:
                    breeder = BreederORM(name=breeder_name)
                    session.add(breeder)
                    await session.flush()
                strain.breeder_id = breeder.id
            else:
                strain.breeder_id = None
                
        # Update other fields
        if "strain_type" in payload:
            strain.strain_type = payload["strain_type"] or None
        if "avg_flowering_days" in payload:
            try:
                days = payload["avg_flowering_days"]
                strain.avg_flowering_days = float(days) if days is not None and str(days).strip() != "" else None
            except (ValueError, TypeError):
                pass
        if "description" in payload:
            strain.description = payload["description"] or None
        if "lineage" in payload:
            lineage_val = payload["lineage"]
            if isinstance(lineage_val, list):
                strain.lineage = [{"name": p.strip()} for p in lineage_val if isinstance(p, str) and p.strip()]
            elif isinstance(lineage_val, dict):
                strain.lineage = lineage_val
            elif isinstance(lineage_val, str):
                import re
                parts = re.split(r'[,xX×·]', lineage_val)
                strain.lineage = [{"name": p.strip()} for p in parts if p.strip()]
            else:
                strain.lineage = {}
                
        # Update cannabinoids and terpenes if provided
        if "cannabinoids" in payload or "terpenes" in payload:
            # 1. Locate or create GenomicSampleORM linked to this strain
            stmt_samples = select(GenomicSampleORM).where(GenomicSampleORM.canonical_strain_id == strain.id)
            samples = (await session.execute(stmt_samples)).scalars().all()
            
            # Find a manual/seedfinder/forum sample to modify, or if none, create a new manual sample
            sample_to_edit = next((s for s in samples if s.source != "kannapedia"), None)
            if not sample_to_edit:
                sample_to_edit = GenomicSampleORM(
                    canonical_strain_id=strain.id,
                    rsp_number=f"MANUAL-{strain.id[:8]}",
                    strain_name=strain.primary_name,
                    source="manual",
                    is_complete=True,
                )
                session.add(sample_to_edit)
                await session.flush()
            else:
                # Update source to manual if the user is editing it, and mark as complete
                sample_to_edit.source = "manual"
                sample_to_edit.is_complete = True
                await session.flush()
                
            # 2. Get or create ChemicalProfileORM
            stmt_profile = select(ChemicalProfileORM).where(ChemicalProfileORM.sample_id == sample_to_edit.id)
            profile = (await session.execute(stmt_profile)).scalars().first()
            if not profile:
                profile = ChemicalProfileORM(sample_id=sample_to_edit.id)
                session.add(profile)
                await session.flush()
                
            # 3. Update Cannabinoids
            if "cannabinoids" in payload:
                cann_data = payload["cannabinoids"] or {}
                if "thc" in cann_data:
                    try:
                        profile.thc = float(cann_data["thc"]) if cann_data["thc"] is not None and str(cann_data["thc"]).strip() != "" else None
                    except (ValueError, TypeError):
                        pass
                if "cbd" in cann_data:
                    try:
                        profile.cbd = float(cann_data["cbd"]) if cann_data["cbd"] is not None and str(cann_data["cbd"]).strip() != "" else None
                    except (ValueError, TypeError):
                        pass
                    
            # 4. Update Terpenes
            if "terpenes" in payload:
                terp_data = payload["terpenes"] or {}
                terp_fields = [
                    "myrcene", "limonene", "caryophyllene", "pinene_alpha",
                    "pinene_beta", "linalool", "humulene", "terpinolene",
                    "ocimene", "nerolidol", "bisabolol", "borneol", "camphene",
                    "carene", "caryophyllene_oxide", "fenchol", "geraniol",
                    "phellandrene", "terpineol", "terpinene_alpha", "terpinene_gamma",
                ]
                for t_field in terp_fields:
                    if t_field in terp_data:
                        val = terp_data[t_field]
                        try:
                            setattr(profile, t_field, float(val) if val is not None and str(val).strip() != "" else None)
                        except (ValueError, TypeError):
                            pass
            
            await session.flush()
            
            # 5. Recalculate average THC/CBD and dominant terpenes on CanonicalStrainORM
            if profile.thc is not None:
                strain.avg_thc_pct = profile.thc
            if profile.cbd is not None:
                strain.avg_cbd_pct = profile.cbd
                
            terp_dict = profile.terpene_dict
            if terp_dict:
                sorted_terps = sorted(terp_dict.items(), key=lambda x: x[1], reverse=True)
                strain.dominant_terpenes = [t[0] for t in sorted_terps[:5]]
            else:
                strain.dominant_terpenes = []
                
        await session.flush()
        await session.commit()
        invalidate_db_state_cache()
        
        detail = await strain_detail(resolved_name)
        return detail


# ----- Neighbors & Similarity ----- #

@app.get("/api/strains/{strain_name}/neighbors")
async def strain_neighbors(strain_name: str, k: int = 10):
    """Find nearest genetic neighbors for a strain."""
    async for session in get_session():
        resolved_name = await get_canonical_strain_name(session, strain_name)
        if not resolved_name:
            resolved_name = strain_name
            
        state = await load_state_from_db(session)
        if not state["relationships"]:
            return {"error": "No data loaded"}
            
        distances, names = create_distance_matrix(state["strains_data"], state["relationships"])
        neighbors = get_nearest_neighbors(distances, names, resolved_name, k=k)
        return {"strain": resolved_name, "neighbors": neighbors}

@app.get("/api/strains/{strain_name}/similarity")
async def strain_similarity(strain_name: str):
    """Combined genetic + terpene similarity for a strain."""
    async for session in get_session():
        resolved_name = await get_canonical_strain_name(session, strain_name)
        if not resolved_name:
            resolved_name = strain_name
            
        state = await load_state_from_db(session)
        if not state["relationships"]:
            return {"error": "No data loaded"}
            
        all_similarities = compute_combined_similarity(
            state["strains_data"], state["relationships"],
        )
        results = all_similarities.get(resolved_name, [])
        return {"strain": resolved_name, "similar": results}

# ----- Terpene APIs ----- #

@app.get("/api/strains/{strain_name}/terpene-profile")
async def terpene_profile(strain_name: str):
    """Normalized terpene profile for radar chart display."""
    async for session in get_session():
        resolved_name = await get_canonical_strain_name(session, strain_name)
        if not resolved_name:
            return JSONResponse({"error": "Strain not found"}, status_code=404)
            
        stmt = select(CanonicalStrainORM).where(CanonicalStrainORM.primary_name == resolved_name)
        strain = (await session.execute(stmt)).scalars().first()
        if not strain:
            return JSONResponse({"error": "Strain not found"}, status_code=404)
            
        stmt_sample = select(GenomicSampleORM).where(GenomicSampleORM.canonical_strain_id == strain.id).options(
            selectinload(GenomicSampleORM.chemical_profile)
        )
        sample = (await session.execute(stmt_sample)).scalars().first()
        
        if not sample or not sample.chemical_profile:
            return JSONResponse({"error": "No chemical profile found"}, status_code=404)
            
        normalized = normalize_terpene_profile(sample.chemical_profile.terpene_dict)
        total = sum(normalized.values())
        return {
            "strain": strain.primary_name,
            "terpenes": normalized,
            "total": round(total, 3),
            "dominant": max(normalized, key=normalized.get) if normalized else None,
        }

@app.get("/api/terpene-heatmap")
async def terpene_heatmap():
    """Matrix data: strains × terpenes for heatmap visualization."""
    async for session in get_session():
        stmt_samples = select(GenomicSampleORM).outerjoin(ChemicalProfileORM).where(GenomicSampleORM.is_complete == True).options(
            selectinload(GenomicSampleORM.chemical_profile)
        )
        samples = (await session.execute(stmt_samples)).scalars().all()
        
        rows = []
        all_terpenes = set()
        
        for s in samples:
            if not s.chemical_profile:
                continue
            normalized = normalize_terpene_profile(s.chemical_profile.terpene_dict)
            all_terpenes.update(normalized.keys())
            rows.append({"strain": s.strain_name, "values": normalized})
            
        terpene_cols = sorted(all_terpenes)
        return {
            "strains": [r["strain"] for r in rows],
            "terpenes": terpene_cols,
            "matrix": [
                [r["values"].get(t, 0.0) for t in terpene_cols]
                for r in rows
            ],
        }

# ----- ML / Clustering API ----- #

@app.post("/api/ml/cluster")
async def trigger_clustering():
    """Trigger ML image clustering for all unclustered images."""
    from src.ml.clustering import run_image_clustering
    async for session in get_session():
        count = await run_image_clustering(session)
        invalidate_db_state_cache()
        return {"success": True, "clustered_count": count}

# ----- ETL Ingestion ----- #

@app.post("/api/ingest/kannapedia")
async def ingest_kannapedia(request: Request):
    """Ingest a raw Kannapedia scraper payload into the warehouse."""
    payload = await request.json()
    
    async for session in get_session():
        # Build existing canonical strains dictionary
        stmt = select(CanonicalStrainORM)
        strains_db = (await session.execute(stmt)).scalars().all()
        
        from src.models.strain import CanonicalStrain
        existing_strains = {}
        for s in strains_db:
            # Map ORM to domain models for ETL compatibility
            existing_strains[s.primary_name] = CanonicalStrain(
                id=s.id,
                primary_name=s.primary_name,
                strain_type=s.strain_type,
                lineage=s.lineage or {},
                description=s.description,
            )
            
        result = ingest_kannapedia_record(payload, existing_strains)
        await save_domain_models_to_db(session, result)
        await session.commit()
        invalidate_db_state_cache()
        
        sample = result["sample"]
        strain = result["strain"]
        
        return {
            "success": True,
            "sample_id": sample.id,
            "strain_id": strain.id,
            "strain_name": sample.strain_name,
            "rsp": sample.rsp_number,
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8005")),
        reload=True,
    )
