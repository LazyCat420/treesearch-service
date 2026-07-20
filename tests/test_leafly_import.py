import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select

from src.models.orm import CanonicalStrainORM, GenomicSampleORM, ChemicalProfileORM

MOCK_LEAFLY_PAGE_HTML = """
<!DOCTYPE html>
<html>
<head>
  <script id="__NEXT_DATA__" type="application/json">
  {
    "props": {
      "pageProps": {
        "strain": {
          "name": "Headband",
          "topTerpName": "caryophyllene",
          "terps": {
            "caryophyllene": {
              "name": "Caryophyllene",
              "score": 0.485
            },
            "limonene": {
              "name": "Limonene",
              "score": 0.419
            },
            "myrcene": {
              "name": "Myrcene",
              "score": 0.338
            },
            "pinene": {
              "name": "Pinene",
              "score": 0.106
            }
          }
        }
      }
    }
  }
  </script>
</head>
<body>
</body>
</html>
"""

# ── Test 1: LeaflyCollector Scraping and Fallback Slugs ──

@pytest.mark.asyncio
async def test_leafly_collector_parses_next_data():
    import sys
    sys.path.append("/home/lazycat/github/projects/sun/scraper-service")
    from app.collectors.leafly_collector import LeaflyCollector

    collector = LeaflyCollector()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = MOCK_LEAFLY_PAGE_HTML
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_response) as mock_get:
        result = await collector.get_strain("Headband")
        assert result is not None
        assert result["name"] == "Headband"
        assert result["slug"] == "headband"
        assert "caryophyllene" in result["terpenes"]
        assert result["terpenes"]["caryophyllene"] == 0.485
        assert result["terpenes"]["pinene"] == 0.106
        mock_get.assert_called_once_with("https://www.leafly.com/strains/headband", headers=collector.headers)


@pytest.mark.asyncio
async def test_leafly_collector_fallback_slug():
    import sys
    sys.path.append("/home/lazycat/github/projects/sun/scraper-service")
    from app.collectors.leafly_collector import LeaflyCollector

    collector = LeaflyCollector()

    # First request for "head-band" returns 404, second request for "headband" returns 200
    mock_response_404 = MagicMock()
    mock_response_404.status_code = 404

    mock_response_200 = MagicMock()
    mock_response_200.status_code = 200
    mock_response_200.text = MOCK_LEAFLY_PAGE_HTML
    mock_response_200.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [mock_response_404, mock_response_200]

        result = await collector.get_strain("Head Band")
        assert result is not None
        assert result["name"] == "Headband"
        assert result["slug"] == "headband"
        assert mock_get.call_count == 2


# ── Test 2: ScraperClient collect_leafly Formatting ──

@pytest.mark.asyncio
async def test_scraper_client_collect_leafly():
    from src.scraper_client import ScraperClient

    client = ScraperClient(base_url="http://localhost:3031")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "source": "leafly",
        "count": 1,
        "items": [{
            "name": "Headband",
            "slug": "headband",
            "terpenes": {"caryophyllene": 0.485}
        }]
    }

    with patch.object(client.client, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        result = await client.collect_leafly("Headband")
        assert result is not None
        assert result["name"] == "Headband"
        assert result["slug"] == "headband"
        assert result["terpenes"]["caryophyllene"] == 0.485

        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        assert payload["source"] == "leafly"
        assert payload["query"] == "Headband"

    await client.close()
