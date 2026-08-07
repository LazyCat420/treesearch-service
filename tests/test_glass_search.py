"""
test_glass_search.py
--------------------
Unit and integration tests for the Glass Search feature in treesearch-service.
"""

import pytest
from src.db import get_session, init_db
from src.services.glass_service import (
    seed_initial_glass_data,
    get_glass_artists,
    get_glass_network,
    get_glass_map_summary,
    rate_artist,
    submit_artist,
    toggle_favorite
)


@pytest.mark.asyncio
async def test_glass_search_workflow():
    await init_db()
    async with get_session() as session:
        # 1. Seed glass data
        await seed_initial_glass_data(session)

    async with get_session() as session:
        # 2. Query all artists
        artists = await get_glass_artists(session)
        assert len(artists) >= 9, f"Expected at least 9 initial artists, got {len(artists)}"
        
        # Check iconic artist exists
        names = [a["name"] for a in artists]
        assert "Banjo Glass" in names
        assert "Sovereignty Glass" in names
        assert "Bob Snodgrass" in names

    async with get_session() as session:
        # 3. Filter by state (CA)
        ca_artists = await get_glass_artists(session, state_code="CA")
        assert len(ca_artists) >= 2
        for a in ca_artists:
            assert a["state_code"] == "CA"

        # 4. Filter by tier (legend)
        legends = await get_glass_artists(session, tier="legend")
        assert len(legends) >= 5
        for l in legends:
            assert l["tier"] == "legend"

        # 5. Search query ("snodgrass")
        search_res = await get_glass_artists(session, search="snodgrass")
        assert len(search_res) == 1
        assert search_res[0]["name"] == "Bob Snodgrass"

    async with get_session() as session:
        # 6. Test network graph generation
        net = await get_glass_network(session)
        assert "nodes" in net and "edges" in net
        assert len(net["nodes"]) >= 9
        assert len(net["edges"]) >= 3

    async with get_session() as session:
        # 7. Test map summary
        summary = await get_glass_map_summary(session)
        assert "states" in summary and "countries" in summary
        assert "CA" in summary["states"]
        assert "OR" in summary["states"]

    async with get_session() as session:
        # 8. Test rating an artist
        target_artist = artists[0]
        res_rate = await rate_artist(
            session,
            artist_id=target_artist["id"],
            user_identifier="test_user",
            craftsmanship=5.0,
            function=5.0,
            aesthetic=5.0,
            comment="Masterpiece glasswork!"
        )
        assert res_rate["success"] is True

    async with get_session() as session:
        # 9. Test submit new artist
        res_sub = await submit_artist(
            session,
            name="Test Local Glass Blower",
            location_state="NC",
            instagram_or_site="https://instagram.com/test_glass",
            source_urls=["https://testglass.com"]
        )
        assert res_sub["success"] is True

        # Verify new artist appears under NC
        nc_artists = await get_glass_artists(session, state_code="NC")
        assert len(nc_artists) == 1
        assert nc_artists[0]["name"] == "Test Local Glass Blower"

    async with get_session() as session:
        # 10. Test favorite toggle
        artist_id = artists[0]["id"]
        fav_on = await toggle_favorite(session, artist_id, "user_123")
        assert fav_on["favorited"] is True

        fav_off = await toggle_favorite(session, artist_id, "user_123")
        assert fav_off["favorited"] is False
