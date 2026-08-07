"""
glass_service.py
----------------
Data access, seeding, and business logic for the Glass Search feature.
"""

import uuid
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import (
    GlassArtistORM,
    GlassCollaborationORM,
    GlassRatingORM,
    GlassFavoriteORM,
    GlassSubmissionORM
)

logger = logging.getLogger(__name__)

INITIAL_ARTISTS = [
    {
        "name": "Banjo Glass",
        "alias": "Banjo",
        "state_code": "CA",
        "country": "USA",
        "tier": "legend",
        "styles": ["Sculptural", "Biomechanical", "Goddess"],
        "bio": "Pioneer of intricate biomechanical and transformational sculptural functional glass art.",
        "instagram_url": "https://instagram.com/banjoglass",
        "website_url": "https://banjoglass.com",
        "status": "verified",
        "image_url": "https://images.unsplash.com/photo-1579783902614-a3fb3927b675?w=500&q=80"
    },
    {
        "name": "Sovereignty Glass",
        "alias": "Steve",
        "state_code": "CA",
        "country": "USA",
        "tier": "legend",
        "styles": ["Scientific", "Peyote Pillar", "Grid Perc"],
        "bio": "Benchmark of American scientific functional glass blowing with revolutionary diffusion designs.",
        "instagram_url": "https://instagram.com/sovereigntyglass",
        "website_url": "https://sovereigntyglass.com",
        "status": "verified",
        "image_url": "https://images.unsplash.com/photo-1541701494587-cb58502866ab?w=500&q=80"
    },
    {
        "name": "Bob Snodgrass",
        "alias": "Godfather of Fuming",
        "state_code": "OR",
        "country": "USA",
        "tier": "legend",
        "styles": ["Fuming", "Skull", "Grateful Dead Heady"],
        "bio": "Widely regarded as the Godfather of modern glass blowing, introduced silver & gold fuming.",
        "instagram_url": "https://instagram.com/bob_snodgrass_glass",
        "website_url": "https://snodgrassglass.com",
        "status": "verified",
        "image_url": "https://images.unsplash.com/photo-1513364776144-60967b0f800f?w=500&q=80"
    },
    {
        "name": "Mothership Glass",
        "alias": "Mothership",
        "state_code": "WA",
        "country": "USA",
        "tier": "legend",
        "styles": ["Fab Egg", "Torus", "Bucket Perc"],
        "bio": "High-end functional art studio known for unmatched precision, fab eggs, and torus designs.",
        "instagram_url": "https://instagram.com/mothershipglass",
        "website_url": "https://mothershipglass.com",
        "status": "verified",
        "image_url": "https://images.unsplash.com/photo-1563089145-599997674d42?w=500&q=80"
    },
    {
        "name": "Elbo Glass",
        "alias": "Elbo",
        "state_code": "PA",
        "country": "USA",
        "tier": "master",
        "styles": ["Dinosaur", "Sculptural", "Faceted"],
        "bio": "Renowned for iconic prehistoric dinosaur sculptures and pop-culture collaborations.",
        "instagram_url": "https://instagram.com/elboglass",
        "website_url": "https://elboglass.com",
        "status": "verified",
        "image_url": "https://images.unsplash.com/photo-1579783900882-c0d3dad7b119?w=500&q=80"
    },
    {
        "name": "Bluegrass Glass",
        "alias": "Corey",
        "state_code": "KY",
        "country": "USA",
        "tier": "master",
        "styles": ["Lamp Post", "Streetlight", "Sculptural"],
        "bio": "Famous for signature lamp post, fire hydrant, and urban landscape functional glass sculptures.",
        "instagram_url": "https://instagram.com/bluegrassglass",
        "website_url": "https://bluegrassglass.com",
        "status": "verified",
        "image_url": "https://images.unsplash.com/photo-1578926375605-eaf7559b1458?w=500&q=80"
    },
    {
        "name": "Buck Glass",
        "alias": "Buck",
        "state_code": "OR",
        "country": "USA",
        "tier": "master",
        "styles": ["Skeleton", "Animals", "Sculptural"],
        "bio": "Master of anatomically precise skeleton animals and fluid organic borosilicate art.",
        "instagram_url": "https://instagram.com/buckglass",
        "website_url": "https://buckglass.com",
        "status": "verified",
        "image_url": "https://images.unsplash.com/photo-1561214115-f2f134cc4912?w=500&q=80"
    },
    {
        "name": "Salt Glass",
        "alias": "Salt",
        "state_code": "TX",
        "country": "USA",
        "tier": "master",
        "styles": ["Monster", "Teeth & Eyes", "Organic Texture"],
        "bio": "Austin-based master creating grotesque yet stunning monster face and eyeball textured pipes.",
        "instagram_url": "https://instagram.com/saltglass",
        "website_url": "https://saltglass.com",
        "status": "verified",
        "image_url": "https://images.unsplash.com/photo-1579546929518-9e396f3cc809?w=500&q=80"
    },
    {
        "name": "Eusheen Jackson",
        "alias": "Eusheen",
        "state_code": "OR",
        "country": "USA",
        "tier": "legend",
        "styles": ["Fillacello", "Patterned Line Work", "Precision Geometry"],
        "bio": "Global master of fillacello technique and mesmerizing geometric line work.",
        "instagram_url": "https://instagram.com/eusheen",
        "website_url": "https://eusheenglass.com",
        "status": "verified",
        "image_url": "https://images.unsplash.com/photo-1550684848-fac1c5b4e853?w=500&q=80"
    }
]

async def seed_initial_glass_data(session: AsyncSession):
    """Seed initial glass blower artists and collabs if table is empty."""
    stmt = select(func.count(GlassArtistORM.id))
    count = (await session.execute(stmt)).scalar()
    if count and count > 0:
        return

    logger.info("Seeding initial Heady Glass Artists...")
    artist_map = {}

    for data in INITIAL_ARTISTS:
        artist = GlassArtistORM(
            id=str(uuid.uuid4()),
            name=data["name"],
            alias=data["alias"],
            state_code=data["state_code"],
            country=data["country"],
            tier=data["tier"],
            styles=data["styles"],
            bio=data["bio"],
            instagram_url=data["instagram_url"],
            website_url=data["website_url"],
            status=data["status"],
            image_url=data["image_url"]
        )
        session.add(artist)
        artist_map[data["name"]] = artist
        if data["alias"]:
            artist_map[data["alias"]] = artist

    await session.flush()

    # Seed sample collabs
    if "Banjo Glass" in artist_map and "Elbo Glass" in artist_map:
        session.add(GlassCollaborationORM(
            id=str(uuid.uuid4()),
            artist_1_id=artist_map["Banjo Glass"].id,
            artist_2_id=artist_map["Elbo Glass"].id,
            collab_name="Bio-Dino Headpiece",
            description="Biomechanical dinosaur headpiece collaboration.",
            year=2021
        ))

    if "Bob Snodgrass" in artist_map and "Eusheen Jackson" in artist_map:
        session.add(GlassCollaborationORM(
            id=str(uuid.uuid4()),
            artist_1_id=artist_map["Bob Snodgrass"].id,
            artist_2_id=artist_map["Eusheen Jackson"].id,
            collab_name="Fume-Cello Rig",
            description="Classic silver fuming mixed with intricate fillacello lines.",
            year=2019
        ))

    if "Sovereignty Glass" in artist_map and "Buck Glass" in artist_map:
        session.add(GlassCollaborationORM(
            id=str(uuid.uuid4()),
            artist_1_id=artist_map["Sovereignty Glass"].id,
            artist_2_id=artist_map["Buck Glass"].id,
            collab_name="Peyote Pillar Skeleton Animal",
            description="Sovereignty grid perc with Buck signature skeleton figure.",
            year=2020
        ))

    # Seed initial ratings
    for alias, artist in artist_map.items():
        session.add(GlassRatingORM(
            id=str(uuid.uuid4()),
            artist_id=artist.id,
            user_identifier="system_seed",
            craftsmanship_score=4.9 if artist.tier == "legend" else 4.7,
            function_score=4.8,
            aesthetic_score=4.9,
            comment="Initial baseline community seed rating."
        ))

    await session.commit()
    logger.info("Successfully seeded glass artists, collabs, and ratings.")


async def get_glass_artists(
    session: AsyncSession,
    state_code: Optional[str] = None,
    tier: Optional[str] = None,
    search: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieve glass artists with optional filters."""
    query = select(GlassArtistORM)

    if state_code:
        query = query.where(func.upper(GlassArtistORM.state_code) == state_code.upper())
    if tier:
        query = query.where(GlassArtistORM.tier == tier)
    if search:
        s = f"%{search.lower()}%"
        query = query.where(
            or_(
                func.lower(GlassArtistORM.name).like(s),
                func.lower(GlassArtistORM.alias).like(s),
                func.lower(GlassArtistORM.bio).like(s)
            )
        )

    results = (await session.execute(query)).scalars().all()
    
    artists = []
    for row in results:
        # Fetch rating stats
        rating_stmt = select(
            func.avg(GlassRatingORM.craftsmanship_score),
            func.avg(GlassRatingORM.function_score),
            func.avg(GlassRatingORM.aesthetic_score),
            func.count(GlassRatingORM.id)
        ).where(GlassRatingORM.artist_id == row.id)

        avg_c, avg_f, avg_a, rating_count = (await session.execute(rating_stmt)).first()

        overall_score = round(
            float((avg_c or 5.0) + (avg_f or 5.0) + (avg_a or 5.0)) / 3.0, 1
        ) if rating_count else 5.0

        artists.append({
            "id": row.id,
            "name": row.name,
            "alias": row.alias,
            "state_code": row.state_code,
            "country": row.country,
            "bio": row.bio,
            "instagram_url": row.instagram_url,
            "website_url": row.website_url,
            "tier": row.tier,
            "styles": row.styles or [],
            "status": row.status,
            "image_url": row.image_url,
            "rating": {
                "overall": overall_score,
                "craftsmanship": round(float(avg_c or 5.0), 1),
                "function": round(float(avg_f or 5.0), 1),
                "aesthetic": round(float(avg_a or 5.0), 1),
                "count": rating_count or 0
            }
        })

    return artists


async def get_glass_network(session: AsyncSession) -> Dict[str, Any]:
    """Build nodes and edges for Vis.js graph network."""
    artists = await get_glass_artists(session)
    collab_stmt = select(GlassCollaborationORM)
    collabs = (await session.execute(collab_stmt)).scalars().all()

    nodes = []
    for a in artists:
        color = "#e040fb" if a["tier"] == "legend" else ("#00d2ff" if a["tier"] == "master" else "#00c853")
        nodes.append({
            "id": a["id"],
            "label": a["name"],
            "title": f"<b>{a['name']}</b> ({a['state_code'] or 'USA'})<br>Tier: {a['tier'].capitalize()}<br>Rating: ⭐ {a['rating']['overall']}",
            "group": a["tier"],
            "color": {
                "background": color,
                "border": "#ffffff"
            },
            "shape": "dot",
            "size": 25 if a["tier"] == "legend" else (20 if a["tier"] == "master" else 15),
            "data": a
        })

    edges = []
    for c in collabs:
        edges.append({
            "from": c.artist_1_id,
            "to": c.artist_2_id,
            "label": c.collab_name or "Collab",
            "title": f"Collab: {c.collab_name or 'Glass Piece'} ({c.year or 'N/A'})",
            "color": {"color": "#ffb300", "highlight": "#ff8f00"},
            "width": 2
        })

    return {"nodes": nodes, "edges": edges}


async def get_glass_map_summary(session: AsyncSession) -> Dict[str, Any]:
    """Aggregates artist count by US state code and by country."""
    state_stmt = select(
        GlassArtistORM.state_code, func.count(GlassArtistORM.id)
    ).where(GlassArtistORM.state_code.isnot(None)).group_by(GlassArtistORM.state_code)

    state_results = (await session.execute(state_stmt)).all()
    states_dict = {st: count for st, count in state_results if st}

    country_stmt = select(
        GlassArtistORM.country, func.count(GlassArtistORM.id)
    ).group_by(GlassArtistORM.country)
    country_results = (await session.execute(country_stmt)).all()
    countries_dict = {co: count for co, count in country_results if co}

    return {
        "states": states_dict,
        "countries": countries_dict
    }


async def rate_artist(
    session: AsyncSession,
    artist_id: str,
    user_identifier: str,
    craftsmanship: float,
    function: float,
    aesthetic: float,
    comment: Optional[str] = None
) -> Dict[str, Any]:
    """Submit a rating for an artist."""
    rating = GlassRatingORM(
        id=str(uuid.uuid4()),
        artist_id=artist_id,
        user_identifier=user_identifier,
        craftsmanship_score=max(1.0, min(5.0, craftsmanship)),
        function_score=max(1.0, min(5.0, function)),
        aesthetic_score=max(1.0, min(5.0, aesthetic)),
        comment=comment
    )
    session.add(rating)
    await session.commit()

    return {"success": True, "rating_id": rating.id}


async def submit_artist(
    session: AsyncSession,
    name: str,
    location_state: Optional[str] = None,
    instagram_or_site: Optional[str] = None,
    source_urls: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Submit a new glass blower artist for moderation / ingestion."""
    sub = GlassSubmissionORM(
        id=str(uuid.uuid4()),
        name=name,
        location_state=location_state,
        instagram_or_site=instagram_or_site,
        source_urls=source_urls or [],
        status="pending"
    )
    session.add(sub)
    
    # Also add directly as unverified artist so it shows immediately in the map/list
    artist = GlassArtistORM(
        id=str(uuid.uuid4()),
        name=name,
        state_code=location_state,
        country="USA",
        tier="local",
        styles=["Niche", "Community Submitted"],
        bio=f"User submitted artist. Web/IG: {instagram_or_site or 'N/A'}",
        instagram_url=instagram_or_site if "instagram.com" in (instagram_or_site or "") else None,
        website_url=instagram_or_site if "http" in (instagram_or_site or "") and "instagram" not in (instagram_or_site or "") else None,
        status="unverified"
    )
    session.add(artist)

    await session.commit()
    return {"success": True, "submission_id": sub.id, "artist_id": artist.id}


async def toggle_favorite(
    session: AsyncSession,
    artist_id: str,
    user_identifier: str
) -> Dict[str, Any]:
    """Toggle a favorite bookmark for an artist."""
    stmt = select(GlassFavoriteORM).where(
        GlassFavoriteORM.artist_id == artist_id,
        GlassFavoriteORM.user_identifier == user_identifier
    )
    existing = (await session.execute(stmt)).scalars().first()

    if existing:
        await session.delete(existing)
        await session.commit()
        return {"favorited": False}
    else:
        fav = GlassFavoriteORM(
            id=str(uuid.uuid4()),
            artist_id=artist_id,
            user_identifier=user_identifier
        )
        session.add(fav)
        await session.commit()
        return {"favorited": True}
