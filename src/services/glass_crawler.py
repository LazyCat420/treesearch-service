"""
glass_crawler.py
-----------------
Background ingestion service for crawling glass blower links via scraper-service
and persisting discovered artists and collaborations into treesearch-service database.
"""

import logging
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from src.models.orm import GlassArtistORM, GlassCollaborationORM, GlassSubmissionORM
from src.scraper_client import ScraperClient

logger = logging.getLogger(__name__)

async def crawl_and_ingest_glass_url(session: AsyncSession, target_url: str, submission_id: Optional[str] = None) -> Dict[str, Any]:
    """Crawl a glass artist URL via scraper-service and persist discovered entities."""
    logger.info(f"[glass_crawler] Starting background crawl for URL: {target_url}")

    client = ScraperClient()
    try:
        data = await client.collect_glass_artist(target_url)
        await client.close()
    except Exception as e:
        logger.error(f"[glass_crawler] Scraper error for {target_url}: {e}")
        return {"success": False, "error": str(e)}

    if not data or not data.get("success"):
        logger.warning(f"[glass_crawler] Scraper returned no data or failure for {target_url}")
        return {"success": False, "error": data.get("error", "No data returned")}

    ingested_artists = 0
    ingested_collabs = 0

    # 1. Update submission artist details if state or bio extracted
    if data.get("state_code") or data.get("bio"):
        st_code = data.get("state_code")
        bio_text = data.get("bio")
        ig_url = data.get("instagram_url")

        # Find target artist record by instagram or website url
        query = select(GlassArtistORM).where(
            or_(
                GlassArtistORM.website_url == target_url,
                GlassArtistORM.instagram_url == target_url,
                GlassArtistORM.instagram_url == ig_url
            )
        )
        res = await session.execute(query)
        artist = res.scalars().first()

        if artist:
            if st_code and artist.state_code in (None, "CA", "USA"):
                artist.state_code = st_code
            if bio_text and len(bio_text) > len(artist.bio or ""):
                artist.bio = bio_text[:500]
            if ig_url and not artist.instagram_url:
                artist.instagram_url = ig_url
            await session.commit()

    # 2. Ingest Discovered Artists
    discovered_list = data.get("discovered_artists", [])
    for disc in discovered_list:
        name = disc.get("name")
        link = disc.get("link")
        disc_state = disc.get("state_code") or "CA"

        if not name or len(name) < 3:
            continue

        # Check if artist already exists
        check_q = select(GlassArtistORM).where(
            or_(
                GlassArtistORM.name.ilike(name),
                GlassArtistORM.website_url == link,
                GlassArtistORM.instagram_url == link
            )
        )
        check_res = await session.execute(check_q)
        existing = check_res.scalars().first()

        if not existing:
            ig_link = link if (link and "instagram.com" in link) else None
            web_link = link if (link and "instagram.com" not in link) else None
            new_artist = GlassArtistORM(
                name=name,
                alias=name.split()[0],
                state_code=disc_state,
                country="USA",
                tier="local",
                styles=["Niche", "Community Discovered"],
                bio=f"Discovered via scrape of {target_url}",
                instagram_url=ig_link,
                website_url=web_link,
                status="unverified"
            )
            session.add(new_artist)
            ingested_artists += 1

    # 3. Ingest Collaborations
    collabs_list = data.get("collaborations", [])
    for c in collabs_list:
        a1_name = c.get("artist_1_name")
        a2_name = c.get("artist_2_name")
        collab_name = c.get("collab_name")

        if not a1_name or not a2_name:
            continue

        # Lookup ORMs
        q1 = await session.execute(select(GlassArtistORM).where(GlassArtistORM.name.ilike(a1_name)))
        a1_orm = q1.scalars().first()
        q2 = await session.execute(select(GlassArtistORM).where(GlassArtistORM.name.ilike(a2_name)))
        a2_orm = q2.scalars().first()

        if a1_orm and a2_orm:
            # Check existing collab
            collab_q = select(GlassCollaborationORM).where(
                or_(
                    (GlassCollaborationORM.artist_1_id == a1_orm.id) & (GlassCollaborationORM.artist_2_id == a2_orm.id),
                    (GlassCollaborationORM.artist_1_id == a2_orm.id) & (GlassCollaborationORM.artist_2_id == a1_orm.id)
                )
            )
            c_res = await session.execute(collab_q)
            if not c_res.scalars().first():
                new_collab = GlassCollaborationORM(
                    artist_1_id=a1_orm.id,
                    artist_2_id=a2_orm.id,
                    collab_name=collab_name,
                    description=f"Scraped collaboration between {a1_orm.name} and {a2_orm.name}"
                )
                session.add(new_collab)
                ingested_collabs += 1

    await session.commit()

    logger.info(f"[glass_crawler] Crawl complete for {target_url}: +{ingested_artists} artists, +{ingested_collabs} collabs.")
    return {
        "success": True,
        "url": target_url,
        "ingested_artists": ingested_artists,
        "ingested_collabs": ingested_collabs
    }
