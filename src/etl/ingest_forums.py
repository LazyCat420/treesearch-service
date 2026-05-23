"""
ingest_forums.py
----------------
ETL script to pull data from Discourse/XenForo via scraper-service
and store it into the ObservationORM in the warehouse.
"""
import asyncio
import logging
import re
from datetime import datetime

from src.scraper_client import ScraperClient
from src.db import get_session, init_db
from src.models.orm import ObservationORM, ObservationImageORM

logger = logging.getLogger(__name__)

# Fallback cannabis strains to try and match if DB is empty
KNOWN_STRAINS = [
    "Jack Herer", "Blueberry", "White Widow", "Sour Diesel", 
    "OG Kush", "Goji OG", "Northern Lights", "Haze", "Skunk", 
    "Girl Scout Cookies", "GSC", "Gelato", "Runtz", "Gorilla Glue"
]

async def load_all_known_strains(session) -> dict[str, str]:
    """Query all canonical strain names and aliases from database.
    Returns a dictionary mapping strain_name_lowercase -> canonical_strain_id.
    """
    from src.models.orm import CanonicalStrainORM, StrainAliasORM
    from sqlalchemy import select
    
    strains_map = {}
    try:
        # 1. Load canonical strains
        stmt = select(CanonicalStrainORM)
        canonical = (await session.execute(stmt)).scalars().all()
        for cs in canonical:
            strains_map[cs.name.lower()] = cs.id
            
        # 2. Load aliases
        stmt = select(StrainAliasORM)
        aliases = (await session.execute(stmt)).scalars().all()
        for alias in aliases:
            strains_map[alias.name.lower()] = alias.canonical_strain_id
    except Exception as e:
        logger.warning(f"Could not load strain names from DB (bootstrapping?): {e}")
        
    return strains_map

def match_strain(title: str, body: str, strains_map: dict[str, str]) -> tuple[str, str | None]:
    """Scan title and body for strain names or aliases using word boundaries.
    Returns a tuple of (reported_strain_name, canonical_strain_id).
    """
    text = f"{title} {body}".lower()
    
    if strains_map:
        # Sort keys by length descending to match longer multi-word names first
        sorted_names = sorted(strains_map.keys(), key=len, reverse=True)
        for name in sorted_names:
            pattern = r'\b' + re.escape(name) + r'\b'
            if re.search(pattern, text):
                return name.title(), strains_map[name]
    
    # Fallback to hardcoded list
    for strain in KNOWN_STRAINS:
        pattern = r'\b' + re.escape(strain.lower()) + r'\b'
        if re.search(pattern, text):
            return strain, None
            
    return "", None

async def ingest_discourse(client: ScraperClient, base_url: str, forum_name: str, tags: list[str]):
    """Ingest Discourse topics by tags."""
    for tag in tags:
        logger.info(f"Collecting {forum_name} tag: {tag}")
        posts = await client.collect_discourse(
            base_url=base_url,
            forum_name=forum_name,
            tag=tag,
            limit=50
        )
        await _save_posts(posts, forum_name)

async def ingest_xenforo(client: ScraperClient, base_url: str, forum_name: str, subforums: list[str]):
    """Ingest XenForo topics by subforum paths."""
    for subforum in subforums:
        logger.info(f"Collecting {forum_name} subforum: {subforum}")
        posts = await client.collect_xenforo(
            base_url=base_url,
            forum_name=forum_name,
            subforum_path=subforum,
            limit=50
        )
        await _save_posts(posts, forum_name)

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


async def _save_posts(posts: list[dict], source_name: str):
    if not posts:
        return
        
    async for session in get_session():
        # Load strain names for dynamic mapping
        strains_map = await load_all_known_strains(session)
        
        saved = 0
        for p in posts:
            # Check if exists
            from sqlalchemy import select
            stmt = select(ObservationORM).where(ObservationORM.source_id == str(p.get("id")))
            existing = (await session.execute(stmt)).scalars().first()
            if existing:
                continue

            created_at_str = p.get("created_at")
            dt = datetime.fromisoformat(created_at_str).replace(tzinfo=None) if created_at_str else datetime.utcnow()
            
            title = p.get("title", "")
            body = p.get("body", "")
            strain_name, canonical_id = match_strain(title, body, strains_map)

            # Skip if we can't associate with any strain name
            if not strain_name:
                continue

            obs = ObservationORM(
                source_name=source_name,
                source_id=str(p.get("id")),
                source_url=p.get("url"),
                author=p.get("author"),
                observed_at=dt,
                reported_strain_name=strain_name,
                canonical_strain_id=canonical_id,
                raw_text=f"Title: {title}\n\n{body}"
            )
            session.add(obs)
            
            # Save associated images
            image_urls = p.get("image_urls", [])
            for url in image_urls:
                cleaned_url = clean_forum_image_url(url)
                img_orm = ObservationImageORM(
                    observation_id=obs.id,
                    image_url=cleaned_url
                )
                session.add(img_orm)
                
            saved += 1
            
        if saved > 0:
            await session.commit()
            logger.info(f"Saved {saved} new observations from {source_name}")


async def run_forum_ingestion():
    await init_db()
    client = ScraperClient()
    try:
        # 1. Overgrow (Discourse)
        await ingest_discourse(
            client, 
            base_url="https://overgrow.com", 
            forum_name="overgrow", 
            tags=["breeding", "growroom-diaries", "indoor", "outdoor", "hydro"]
        )

        # 2. Rollitup (XenForo)
        await ingest_xenforo(
            client,
            base_url="https://www.rollitup.org",
            forum_name="rollitup",
            subforums=["f/grow-journals.54/", "f/breeders-paradise.94/"]
        )
        
        # 3. THCFarmer (XenForo)
        await ingest_xenforo(
            client,
            base_url="https://www.thcfarmer.com",
            forum_name="thcfarmer",
            subforums=["forums/grow-diaries.28/", "forums/cannabis-breeding.50/"]
        )
    finally:
        await client.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_forum_ingestion())
