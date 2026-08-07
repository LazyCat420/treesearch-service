"""
test_glass_crawler.py
----------------------
Unit tests for background crawler and entity ingestion service in treesearch-service.
"""

import pytest
from unittest.mock import AsyncMock
from src.db import get_session, init_db
from src.services.glass_service import seed_initial_glass_data
from src.services.glass_crawler import crawl_and_ingest_glass_url

@pytest.mark.asyncio
async def test_crawl_and_ingest_glass_url(monkeypatch):
    await init_db()
    async with get_session() as session:
        await seed_initial_glass_data(session)

    mock_payload = {
        "success": True,
        "url": "https://sovereigntyglass.com",
        "state_code": "CA",
        "bio": "Handcrafted scientific glass blowing from California.",
        "instagram_url": "https://instagram.com/sovereigntyglass",
        "discovered_artists": [
            {
                "name": "Niche Local Glass Artist",
                "link": "https://instagram.com/niche_glass",
                "state_code": "OR",
                "source": "https://sovereigntyglass.com"
            }
        ],
        "collaborations": [
            {
                "artist_1_name": "Banjo Glass",
                "artist_2_name": "Sovereignty Glass",
                "collab_name": "Banjo x Sovereignty Collaboration"
            }
        ]
    }

    mock_client = AsyncMock()
    mock_client.collect_glass_artist = AsyncMock(return_value=mock_payload)
    mock_client.close = AsyncMock()

    monkeypatch.setattr("src.services.glass_crawler.ScraperClient", lambda: mock_client)

    async with get_session() as session:
        res = await crawl_and_ingest_glass_url(session, "https://sovereigntyglass.com")
        assert res["success"] is True
        assert res["ingested_artists"] >= 1
