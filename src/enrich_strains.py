import asyncio
import logging
import sys
import os
import re
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("enrich_strains")

# Add Cwd to python path to resolve src imports
sys.path.append("/home/lazycat/github/projects/sun/treesearch-service")

from src.db import get_session, engine
from src.models.orm import (
    CanonicalStrainORM,
    GenomicSampleORM,
    StrainAliasORM,
    ChemicalProfileORM,
    BreederORM
)
from src.genomics.normalization import normalize_strain_name
from src.scraper_client import ScraperClient

_group_normalized_cache = {}

async def resolve_strain_name(session, name: str) -> str | None:
    """Resolve any case, punctuation, or alias variation of a strain name to its canonical primary name in the database."""
    if not name:
        return None
    norm = normalize_strain_name(name)
    
    # 1. Case-insensitive exact match
    stmt = select(CanonicalStrainORM.primary_name).where(CanonicalStrainORM.primary_name.ilike(name))
    res = (await session.execute(stmt)).scalar()
    if res:
        return res
        
    # 2. Case and punctuation-insensitive match
    stmt2 = select(CanonicalStrainORM.primary_name).where(
        func.regexp_replace(func.lower(CanonicalStrainORM.primary_name), '[^a-z0-9]', '', 'g') == norm
    )
    res = (await session.execute(stmt2)).scalar()
    if res:
        return res
        
    # 3. Check aliases
    stmt_alias = select(CanonicalStrainORM.primary_name).join(
        StrainAliasORM, CanonicalStrainORM.id == StrainAliasORM.canonical_strain_id
    ).where(
        or_(
            StrainAliasORM.name.ilike(name),
            func.regexp_replace(func.lower(StrainAliasORM.name), '[^a-z0-9]', '', 'g') == norm
        )
    )
    res = (await session.execute(stmt_alias)).scalar()
    if res:
        return res
        
    # 4. Try matching using group normalization
    global _group_normalized_cache
    from src.genomics.normalization import normalize_for_grouping
    norm_group = normalize_for_grouping(name)
    if norm_group:
        if norm_group in _group_normalized_cache:
            return _group_normalized_cache[norm_group]
            
        stmt_all = select(CanonicalStrainORM).options(selectinload(CanonicalStrainORM.aliases))
        all_strains = (await session.execute(stmt_all)).scalars().all()
        for s in all_strains:
            s_norm = normalize_for_grouping(s.primary_name)
            if s_norm:
                _group_normalized_cache[s_norm] = s.primary_name
            for a in s.aliases:
                a_norm = normalize_for_grouping(a.name)
                if a_norm:
                    _group_normalized_cache[a_norm] = s.primary_name
                    
        if norm_group in _group_normalized_cache:
            return _group_normalized_cache[norm_group]
            
    return None

async def create_parent_placeholder(session, parent_name: str) -> CanonicalStrainORM:
    """Create a placeholder canonical strain and placeholder genomic sample for a parent strain."""
    # Ensure "Unknown Breeder" exists
    stmt_breeder = select(BreederORM).where(BreederORM.name.ilike("Unknown Breeder"))
    breeder = (await session.execute(stmt_breeder)).scalars().first()
    if not breeder:
        breeder = BreederORM(name="Unknown Breeder")
        session.add(breeder)
        await session.flush()

    canonical_name = parent_name.replace(" ", "_")
    logger.info(f"Creating parent placeholder strain: '{canonical_name}'")
    
    parent_strain = CanonicalStrainORM(
        primary_name=canonical_name,
        breeder_id=breeder.id,
        description=f"Auto-generated lineage placeholder for {parent_name}.",
        lineage={},
    )
    session.add(parent_strain)
    await session.flush()
    
    # Create placeholder genomic sample
    parent_sample = GenomicSampleORM(
        canonical_strain_id=parent_strain.id,
        rsp_number=f"PLACEHOLDER-{parent_strain.primary_name}",
        strain_name=parent_strain.primary_name,
        source="seedfinder",
        is_complete=False,
    )
    session.add(parent_sample)
    await session.flush()
    
    # Create alias
    parent_alias = StrainAliasORM(
        canonical_strain_id=parent_strain.id,
        name=parent_name,
        source_name="seedfinder",
        source_id=f"{parent_name.lower().replace(' ', '-')}:seedfinder",
    )
    session.add(parent_alias)
    await session.flush()
    
    return parent_strain

async def enrich_all_strains(session, force_terpenes: bool = False):
    """Enrich all canonical strains with missing Leafly terpenes and auto-create lineage parent placeholders."""
    logger.info("Starting database-wide strain enrichment...")

    # Consolidate duplicate strains and breeders first
    try:
        from src.merge_duplicates import merge_strains, merge_breeders
        logger.info("Running duplicate consolidation before enrichment...")
        await merge_strains(session)
        await merge_breeders(session)
        await session.flush()
    except Exception as merge_ex:
        logger.error(f"Failed to merge duplicate strains/breeders: {merge_ex}")

    scraper_client = ScraperClient()
    enriched_count = 0
    parent_placeholders_count = 0
    lineage_enriched_count = 0

    processed_ids = set()

    try:
        while True:
            # Re-query all strains to catch any newly created parent placeholders
            stmt = select(CanonicalStrainORM).options(
                selectinload(CanonicalStrainORM.genomic_samples)
            )
            result = await session.execute(stmt)
            strains = result.scalars().all()
            
            unprocessed = [s for s in strains if s.id not in processed_ids]
            if not unprocessed:
                break

            logger.info(f"Processing batch of {len(unprocessed)} unprocessed strains (total unique processed so far: {len(processed_ids)})...")
            
            for strain in unprocessed:
                processed_ids.add(strain.id)
                primary_name = strain.primary_name
                logger.info(f"Processing strain '{primary_name}'...")

                # 0. Lineage Scraping from SeedFinder (if missing)
                if not strain.lineage:
                    logger.info(f"  Lineage missing for '{primary_name}'. Querying SeedFinder...")
                    try:
                        from src.collectors.seedfinder_collector import search_seedfinder, scrape_seedfinder_strain
                        sf_results = await search_seedfinder(primary_name)
                        if sf_results:
                            best_match = sf_results[0]
                            strain_slug = best_match["strain_slug"]
                            breeder_slug = best_match["breeder_slug"]
                            logger.info(f"  Found SeedFinder match for '{primary_name}': {strain_slug} ({breeder_slug})")
                            sf_data = await scrape_seedfinder_strain(strain_slug, breeder_slug)
                            if sf_data and sf_data.get("lineage"):
                                strain.lineage = sf_data["lineage"]
                                if sf_data.get("type") and not strain.strain_type:
                                    strain.strain_type = sf_data["type"]
                                if sf_data.get("description") and not strain.description:
                                    strain.description = sf_data["description"]
                                if sf_data.get("flowering_time_days") and not strain.avg_flowering_days:
                                    strain.avg_flowering_days = float(sf_data["flowering_time_days"])
                                await session.flush()
                                logger.info(f"  Successfully retrieved lineage for '{primary_name}' from SeedFinder.")
                                lineage_enriched_count += 1
                            else:
                                logger.info(f"  No lineage details parsed from SeedFinder for '{primary_name}'.")
                        else:
                            logger.info(f"  No SeedFinder match found for '{primary_name}'. Trying DuckDuckGo fallback...")
                            from main import fallback_search_genetics
                            parsed_parents = await fallback_search_genetics(primary_name)
                            if parsed_parents:
                                strain.lineage = [{"name": p} for p in parsed_parents]
                                await session.flush()
                                logger.info(f"  Successfully retrieved fallback lineage for '{primary_name}' from DuckDuckGo.")
                                lineage_enriched_count += 1
                            else:
                                logger.info(f"  No fallback lineage found for '{primary_name}'.")
                    except Exception as line_ex:
                        logger.error(f"  Lineage scraping failed for '{primary_name}': {line_ex}")

                # 1. Leafly Terpene Enrichment
                has_leafly_sample = any(s.source in ("leafly", "leafly_fallback") for s in strain.genomic_samples)
                has_terps = bool(strain.dominant_terpenes)

                if force_terpenes or not has_terps or not has_leafly_sample:
                    logger.info(f"  Querying Leafly for '{primary_name}'...")
                    leafly_result = None
                    try:
                        leafly_result = await scraper_client.collect_leafly(strain_name=primary_name)
                    except Exception as lex:
                        logger.error(f"  Leafly terpene lookup failed for '{primary_name}': {lex}")

                    terpene_profile = None
                    source_used = "leafly"

                    if leafly_result and "terpenes" in leafly_result and leafly_result["terpenes"]:
                        terpene_profile = leafly_result["terpenes"]
                    else:
                        logger.info(f"  No Leafly terpene data for '{primary_name}'. Trying web search fallback...")
                        try:
                            from main import fallback_search_terpenes
                            terpene_profile = await fallback_search_terpenes(primary_name)
                            if terpene_profile:
                                source_used = "leafly_fallback"
                        except Exception as fex:
                            logger.error(f"  Web search terpene fallback failed for '{primary_name}': {fex}")

                    if terpene_profile:
                        stmt_lf = select(GenomicSampleORM).where(
                            (GenomicSampleORM.canonical_strain_id == strain.id) &
                            (GenomicSampleORM.source.in_(["leafly", "leafly_fallback"]))
                        )
                        existing_lf_sample = (await session.execute(stmt_lf)).scalars().first()
                        
                        if not existing_lf_sample:
                            lf_sample = GenomicSampleORM(
                                canonical_strain_id=strain.id,
                                rsp_number=f"LEAFLY-{strain.primary_name.upper().replace(' ', '_')}",
                                strain_name=strain.primary_name,
                                source=source_used,
                                is_complete=True,
                                raw_payload=leafly_result if leafly_result else {"terpenes": terpene_profile},
                            )
                            session.add(lf_sample)
                            await session.flush()
                            
                            cp = ChemicalProfileORM(sample_id=lf_sample.id)
                            session.add(cp)
                        else:
                            lf_sample = existing_lf_sample
                            lf_sample.source = source_used
                            stmt_cp = select(ChemicalProfileORM).where(ChemicalProfileORM.sample_id == lf_sample.id)
                            cp = (await session.execute(stmt_cp)).scalars().first()
                            if not cp:
                                cp = ChemicalProfileORM(sample_id=lf_sample.id)
                                session.add(cp)
                        
                        terp_map = {
                            "myrcene": "myrcene",
                            "limonene": "limonene",
                            "caryophyllene": "caryophyllene",
                            "pinene": "pinene_alpha",
                            "pinene_alpha": "pinene_alpha",
                            "pinene_beta": "pinene_beta",
                            "linalool": "linalool",
                            "humulene": "humulene",
                            "terpinolene": "terpinolene",
                            "ocimene": "ocimene",
                            "alpha-pinene": "pinene_alpha",
                            "beta-pinene": "pinene_beta",
                            "alpha-humulene": "humulene",
                            "beta-caryophyllene": "caryophyllene"
                        }
                        
                        # Reset all terpene fields to None first to clear old data if updating
                        for attr in ["myrcene", "limonene", "caryophyllene", "pinene_alpha",
                                     "pinene_beta", "linalool", "humulene", "terpinolene", "ocimene", "nerolidol"]:
                            setattr(cp, attr, None)

                        for raw_name, score in terpene_profile.items():
                            field_name = terp_map.get(raw_name.lower())
                            if field_name:
                                setattr(cp, field_name, float(score))
                                
                        await session.flush()
                        
                        terp_dict = {}
                        for attr in ["myrcene", "limonene", "caryophyllene", "pinene_alpha",
                                     "linalool", "humulene", "terpinolene", "ocimene"]:
                            val = getattr(cp, attr, None)
                            if val and val > 0:
                                terp_dict[attr] = val
                        if terp_dict:
                            sorted_terps = sorted(terp_dict.items(), key=lambda x: x[1], reverse=True)
                            strain.dominant_terpenes = [t[0] for t in sorted_terps[:5]]
                            await session.flush()
                        
                        logger.info(f"  Successfully enriched terpenes for '{primary_name}' from {source_used}.")
                        enriched_count += 1
                    else:
                        logger.info(f"  No terpene profile found for '{primary_name}' on Leafly or via search fallback.")

                # 2. Lineage Parent Placeholders Creation
                if strain.lineage:
                    parent_entries = []
                    if isinstance(strain.lineage, list):
                        for entry in strain.lineage[:3]:
                            if isinstance(entry, dict) and entry.get("name"):
                                parent_entries.append(entry)
                            elif isinstance(entry, str):
                                parent_entries.append({"name": entry})
                    elif isinstance(strain.lineage, dict):
                        for key in ("mother", "father", "parent1", "parent2"):
                            if key in strain.lineage:
                                parent_entries.append({"name": str(strain.lineage[key])})

                    for entry in parent_entries:
                        parent_name = entry["name"].strip()
                        if parent_name.lower() in ("sativa", "indica", "hybrid", "ruderalis",
                                                   "unknown strain", "unknown hybrid",
                                                   "unknown mostly indica", "unknown mostly sativa",
                                                   "unknown"):
                            continue

                        resolved_parent = await resolve_strain_name(session, parent_name)
                        if resolved_parent and normalize_strain_name(resolved_parent) == normalize_strain_name(strain.primary_name):
                            resolved_parent = None

                        if not resolved_parent:
                            parent_primary_name = parent_name
                            if normalize_strain_name(parent_primary_name) == normalize_strain_name(strain.primary_name):
                                parent_breeder = entry.get("breeder")
                                if parent_breeder and parent_breeder.lower() not in ("unknown", "unknown breeder"):
                                    parent_primary_name = f"{parent_name} ({parent_breeder})"
                                else:
                                    parent_primary_name = f"{parent_name} (Parent)"

                            resolved_unique = await resolve_strain_name(session, parent_primary_name)
                            if not resolved_unique:
                                logger.info(f"  Lineage parent '{parent_primary_name}' not found in DB.")
                                await create_parent_placeholder(session, parent_primary_name)
                                parent_placeholders_count += 1
                            else:
                                logger.debug(f"  Lineage parent '{parent_primary_name}' already resolved to '{resolved_unique}'.")
                        else:
                            logger.debug(f"  Lineage parent '{parent_name}' already resolved to '{resolved_parent}'.")

        logger.info(f"Enrichment session completed: Enriched {enriched_count} strains, created {parent_placeholders_count} parent placeholders, enriched lineage for {lineage_enriched_count} strains.")
    finally:
        await scraper_client.close()

async def main():
    logger.info("Starting manual enrichment script...")
    async for session in get_session():
        try:
            await enrich_all_strains(session)
            await session.commit()
            logger.info("Successfully committed all enriched changes.")
        except Exception as e:
            await session.rollback()
            logger.error(f"Enrichment transaction failed, rolled back changes. Error: {e}")
            raise e
        break

if __name__ == "__main__":
    asyncio.run(main())
