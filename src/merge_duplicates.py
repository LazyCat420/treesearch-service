import asyncio
import logging
import sys
import re
from collections import defaultdict
from sqlalchemy import select, func, update
from sqlalchemy.orm import selectinload

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("merge_duplicates")

# Add Cwd to python path to resolve src imports
sys.path.append("/home/lazycat/github/projects/sun/treesearch-service")

from src.db import get_session, engine
from src.models.orm import (
    CanonicalStrainORM,
    GenomicSampleORM,
    StrainAliasORM,
    ObservationORM,
    SourceStrainRecordORM,
    BreederORM
)
from src.genomics.normalization import normalize_strain_name

def normalize_for_grouping(name: str) -> str:
    if not name:
        return ""
    # Replace underscores with spaces
    name_clean = name.replace("_", " ")
    # Lowercase
    name_clean = name_clean.lower()
    # Strip parenthesized content: e.g. "headband (unknown or legendary)" -> "headband "
    name_clean = re.sub(r"\s*\([^)]*\)", "", name_clean)
    # Strip common breeding/phenotype/type suffixes as whole words
    name_clean = re.sub(r"\b(bx\d*|auto|f\d*|s\d*|ix)\b", "", name_clean)
    # Remove all non-alphanumeric characters
    return re.sub(r"[^a-z0-9]", "", name_clean)

async def merge_strains(session):
    logger.info("Scanning canonical strains for duplicates...")
    stmt = select(CanonicalStrainORM).options(
        selectinload(CanonicalStrainORM.genomic_samples),
        selectinload(CanonicalStrainORM.aliases),
        selectinload(CanonicalStrainORM.observations)
    )
    result = await session.execute(stmt)
    strains = result.scalars().all()
    
    # Group by normalized name for grouping/merging
    groups = defaultdict(list)
    for s in strains:
        norm = normalize_for_grouping(s.primary_name)
        groups[norm].append(s)
        
    duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}
    logger.info(f"Found {len(duplicate_groups)} groups of duplicate strains.")
    
    for norm, group in duplicate_groups.items():
        logger.info(f"Merging group '{norm}' ({len(group)} strains):")
        for s in group:
            logger.info(f"  - ID: {s.id}, Name: {s.primary_name}, Samples: {len(s.genomic_samples)}, Terps: {s.dominant_terpenes}, Breeder ID: {s.breeder_id}")
            
        # Select primary: prefer strain with dominant terpenes, then most genomic samples, then most aliases, then earliest created_at
        def primary_score(st):
            score = 0
            if st.dominant_terpenes:
                score += 100
            score += len(st.genomic_samples) * 10
            score += len(st.aliases)
            return score
            
        group_sorted = sorted(group, key=primary_score, reverse=True)
        primary = group_sorted[0]
        duplicates = group_sorted[1:]
        
        logger.info(f"  Selected Primary: {primary.id} ({primary.primary_name})")
        
        # Consolidate metadata
        for dup in duplicates:
            if not primary.description and dup.description:
                primary.description = dup.description
            if not primary.lineage and dup.lineage:
                primary.lineage = dup.lineage
            if not primary.breeder_id and dup.breeder_id:
                primary.breeder_id = dup.breeder_id
            if not primary.strain_type and dup.strain_type:
                primary.strain_type = dup.strain_type
            if primary.avg_flowering_days is None and dup.avg_flowering_days is not None:
                primary.avg_flowering_days = dup.avg_flowering_days
            if primary.avg_thc_pct is None and dup.avg_thc_pct is not None:
                primary.avg_thc_pct = dup.avg_thc_pct
            if primary.avg_cbd_pct is None and dup.avg_cbd_pct is not None:
                primary.avg_cbd_pct = dup.avg_cbd_pct
                
            # Merge lists
            if dup.dominant_terpenes:
                for t in dup.dominant_terpenes:
                    if t not in primary.dominant_terpenes:
                        primary.dominant_terpenes.append(t)
            if dup.aroma_tags:
                for t in dup.aroma_tags:
                    if t not in primary.aroma_tags:
                        primary.aroma_tags.append(t)
            if dup.effect_tags:
                for t in dup.effect_tags:
                    if t not in primary.effect_tags:
                        primary.effect_tags.append(t)
                        
            # Sum up observation counts
            primary.observation_count += dup.observation_count
            
            # Re-parent child relationships using direct SQL update statements.
            # This avoids SQLAlchemy collection tracking and null-constraint violation errors.
            
            # 1. Genomic Samples
            await session.execute(
                update(GenomicSampleORM)
                .where(GenomicSampleORM.canonical_strain_id == dup.id)
                .values(canonical_strain_id=primary.id)
            )
            logger.info(f"    Re-parented Genomic Samples for duplicate: {dup.id}")
                
            # 2. Aliases
            # Deduplicate aliases: delete those that are already defined in primary
            primary_alias_names = {a.name.lower() for a in primary.aliases}
            
            # Add dup's own primary name as an alias to primary if not already present
            clean_dup_name = dup.primary_name.replace("_", " ")
            if clean_dup_name.lower() not in primary_alias_names and dup.primary_name.lower() not in primary_alias_names:
                new_alias = StrainAliasORM(
                    canonical_strain_id=primary.id,
                    name=clean_dup_name,
                    source_name="merge",
                    source_id=f"merged:{dup.id}",
                    confidence=1.0
                )
                session.add(new_alias)
                logger.info(f"    Added duplicate primary name '{clean_dup_name}' as alias to primary")
                primary_alias_names.add(clean_dup_name.lower())
                primary_alias_names.add(dup.primary_name.lower())

            for alias in list(dup.aliases):
                if alias.name.lower() in primary_alias_names:
                    await session.delete(alias)
                    logger.info(f"    Deleted duplicate alias: {alias.name}")
            await session.flush()
            
            # Re-parent the remaining aliases
            await session.execute(
                update(StrainAliasORM)
                .where(StrainAliasORM.canonical_strain_id == dup.id)
                .values(canonical_strain_id=primary.id)
            )
            logger.info(f"    Re-parented non-duplicate Aliases for duplicate: {dup.id}")
                    
            # 3. Observations
            await session.execute(
                update(ObservationORM)
                .where(ObservationORM.canonical_strain_id == dup.id)
                .values(canonical_strain_id=primary.id)
            )
            logger.info(f"    Re-parented Observations for duplicate: {dup.id}")
                
            # 4. Source Strain Records
            await session.execute(
                update(SourceStrainRecordORM)
                .where(SourceStrainRecordORM.canonical_strain_id == dup.id)
                .values(canonical_strain_id=primary.id)
            )
            
            dup_id = dup.id
            # Expire relationships of dup before deleting it
            session.expire(dup)
            
            # Delete the duplicate strain
            await session.delete(dup)
            logger.info(f"    Deleted duplicate strain record: {dup_id}")
            
        await session.flush()

async def merge_breeders(session):
    logger.info("Scanning breeders for duplicates...")
    stmt = select(BreederORM)
    result = await session.execute(stmt)
    breeders = result.scalars().all()
    
    # Group by lowercase name
    groups = defaultdict(list)
    for b in breeders:
        norm = b.name.strip().lower()
        groups[norm].append(b)
        
    duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}
    logger.info(f"Found {len(duplicate_groups)} groups of duplicate breeders.")
    
    for name, group in duplicate_groups.items():
        logger.info(f"Merging breeder group '{name}' ({len(group)} breeders):")
        for b in group:
            logger.info(f"  - ID: {b.id}, Name: {b.name}, Regions: {b.region}")
            
        # Select primary
        def primary_score(br):
            score = 0
            if br.website:
                score += 10
            if br.region:
                score += 5
            return score
            
        group_sorted = sorted(group, key=primary_score, reverse=True)
        primary = group_sorted[0]
        duplicates = group_sorted[1:]
        
        logger.info(f"  Selected Primary: {primary.id} ({primary.name})")
        
        # Consolidate metadata
        for dup in duplicates:
            if not primary.website and dup.website:
                primary.website = dup.website
            if not primary.region and dup.region:
                primary.region = dup.region
            if not primary.notes and dup.notes:
                primary.notes = dup.notes
                
            # Re-parent canonical strains
            await session.execute(
                update(CanonicalStrainORM)
                .where(CanonicalStrainORM.breeder_id == dup.id)
                .values(breeder_id=primary.id)
            )
            dup_id = dup.id
            # Delete the duplicate breeder
            await session.delete(dup)
            logger.info(f"    Deleted duplicate breeder record: {dup_id}")
            
        await session.flush()

async def main():
    db_url = str(engine.url)
    logger.info(f"Starting merge tool against: {db_url}")
    
    async for session in get_session():
        try:
            await merge_strains(session)
            await merge_breeders(session)
            await session.commit()
            logger.info("Successfully merged all duplicate records and committed transactions.")
        except Exception as e:
            await session.rollback()
            logger.error(f"Merge transaction failed, rolled back changes. Error: {e}")
            raise e
        break

if __name__ == "__main__":
    asyncio.run(main())
