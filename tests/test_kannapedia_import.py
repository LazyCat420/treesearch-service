"""
test_kannapedia_import.py
---------------------------
Mock tests for the Kannapedia → cannabis-researcher import pipeline.

Validates:
  1. Kannapedia strain name → RSP number lookup (index search)
  2. Kannapedia raw payload → ingest_kannapedia_record() ETL
  3. ScraperClient.collect_kannapedia() request formatting
  4. Full import pipeline creates genomic data (not just observations)
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ── Fixtures ──

MOCK_KANNAPEDIA_INDEX_HTML = """
<html>
<body>
  <li class="-js SearchResults--item">
    <h2 class="SearchResults--strain" data-name="Headband">
      <a href="/strains/rsp10154">Headband</a>
    </h2>
    <p class="SearchResults--source" data-source="The List Exchange">
      <a class="SearchLink" href="/strains?source=The+List+Exchange">The List Exchange</a>
    </p>
  </li>
  <li class="-js SearchResults--item">
    <h2 class="SearchResults--strain" data-name="Headband BX">
      <a href="/strains/rsp10155">Headband BX</a>
    </h2>
  </li>
  <li class="-js SearchResults--item">
    <h2 class="SearchResults--strain" data-name="Blue Dream">
      <a href="/strains/rsp10200">Blue Dream</a>
    </h2>
  </li>
</body>
</html>
"""

MOCK_KANNAPEDIA_SCRAPED_PAYLOAD = {
    "rsp_number": "RSP10154",
    "name": "Headband",
    "general_info": {
        "Sample Name": "Headband",
        "REF NUMBER": "RSP10154",
        "Grower": "The List Exchange",
        "Accession Date": "2019-03-15",
        "Reported Sex": "Female",
        "Report Type": "Standard",
        "Rarity": "Common",
        "Plant Type": "Hybrid",
        "Reported Heterozygosity": "1.5%",
    },
    "chemical_content": {
        "cannabinoids": {
            "THC": "22.5%",
            "CBD": "0.1%",
            "THCA": "24.8%",
            "CBG": "0.5%",
        },
        "terpenoids": {
            "β-Myrcene": "0.45%",
            "d-Limonene": "0.32%",
            "β-Caryophyllene": "0.28%",
            "α-Pinene": "0.15%",
            "Linalool": "0.12%",
        },
    },
    "genetic_relationships": {
        "all_samples": [
            {"distance": 0.15, "strain": "OG Kush", "rsp": "rsp10100"},
            {"distance": 0.18, "strain": "Sour Diesel", "rsp": "rsp10101"},
        ],
        "base_tree": [
            {"distance": 0.12, "strain": "Master Kush", "rsp": "rsp10102"},
        ],
        "most_distant": [
            {"distance": 0.95, "strain": "Durban Poison", "rsp": "rsp10103"},
        ],
    },
    "blockchain": {
        "txid": "abc123def456",
        "shasum": "sha256:deadbeef",
    },
    "source_url": "https://www.kannapedia.net/strains/rsp10154",
    "scraped_at": "2026-05-21T12:00:00",
}


# ── Test 1: ETL ingestion ──

class TestKannapediaETL:
    """Test the kannapedia_etl.py transforms raw payloads correctly."""

    def test_ingest_creates_genomic_sample(self):
        from src.etl.kannapedia_etl import ingest_kannapedia_record

        result = ingest_kannapedia_record(MOCK_KANNAPEDIA_SCRAPED_PAYLOAD)

        assert "sample" in result
        assert "strain" in result
        assert "alias" in result
        assert "source_record" in result

        sample = result["sample"]
        assert sample.rsp_number == "RSP10154"
        assert sample.strain_name == "Headband"
        assert sample.is_complete is True

    def test_ingest_creates_chemical_profile(self):
        from src.etl.kannapedia_etl import ingest_kannapedia_record

        result = ingest_kannapedia_record(MOCK_KANNAPEDIA_SCRAPED_PAYLOAD)
        sample = result["sample"]

        assert sample.chemical_profile is not None
        cp = sample.chemical_profile
        assert cp.thc == pytest.approx(22.5)
        assert cp.cbd == pytest.approx(0.1)
        assert cp.thca == pytest.approx(24.8)
        assert cp.cbg == pytest.approx(0.5)
        assert cp.myrcene == pytest.approx(0.45)
        assert cp.limonene == pytest.approx(0.32)
        assert cp.caryophyllene == pytest.approx(0.28)

    def test_ingest_creates_genetic_relationships(self):
        from src.etl.kannapedia_etl import ingest_kannapedia_record

        result = ingest_kannapedia_record(MOCK_KANNAPEDIA_SCRAPED_PAYLOAD)
        sample = result["sample"]

        assert len(sample.genetic_relationships) == 4  # 2 all_samples + 1 base_tree + 1 most_distant

        all_samples = [r for r in sample.genetic_relationships if r.relationship_type == "all_samples"]
        assert len(all_samples) == 2
        assert all_samples[0].strain_name_b == "OG Kush"
        assert all_samples[0].rsp_b == "RSP10100"
        assert all_samples[0].distance == pytest.approx(0.15)

    def test_ingest_creates_source_record(self):
        from src.etl.kannapedia_etl import ingest_kannapedia_record

        result = ingest_kannapedia_record(MOCK_KANNAPEDIA_SCRAPED_PAYLOAD)
        src = result["source_record"]

        assert src.source_id == "RSP10154"
        assert "kannapedia.net" in src.source_url
        assert src.payload == MOCK_KANNAPEDIA_SCRAPED_PAYLOAD

    def test_ingest_resolves_existing_strain(self):
        from src.etl.kannapedia_etl import ingest_kannapedia_record
        from src.models.strain import CanonicalStrain

        existing = {
            "Headband": CanonicalStrain(primary_name="Headband")
        }
        result = ingest_kannapedia_record(MOCK_KANNAPEDIA_SCRAPED_PAYLOAD, existing)

        # Should reuse the existing strain, not create a new one
        assert result["strain"].id == existing["Headband"].id

    def test_ingest_resolves_normalized_strain(self):
        from src.etl.kannapedia_etl import ingest_kannapedia_record
        from src.models.strain import CanonicalStrain

        # Try to resolve "Headband" (scraped name) to "Head_Band" (existing canonical name)
        existing = {
            "Head_Band": CanonicalStrain(primary_name="Head_Band")
        }
        result = ingest_kannapedia_record(MOCK_KANNAPEDIA_SCRAPED_PAYLOAD, existing)

        # Should reuse the existing strain despite difference in underscores/spaces
        assert result["strain"].id == existing["Head_Band"].id

    def test_ingest_creates_new_strain_if_not_found(self):
        from src.etl.kannapedia_etl import ingest_kannapedia_record

        result = ingest_kannapedia_record(MOCK_KANNAPEDIA_SCRAPED_PAYLOAD, {})

        assert result["strain"].primary_name == "Headband"
        assert result["strain"].id is not None


# ── Test 2: ScraperClient.collect_kannapedia() request formatting ──

class TestScraperClientKannapedia:
    """Test the ScraperClient formats Kannapedia requests correctly."""

    @pytest.mark.asyncio
    async def test_collect_kannapedia_sends_correct_payload(self):
        from src.scraper_client import ScraperClient

        client = ScraperClient(base_url="http://localhost:8001")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "source": "kannapedia",
            "count": 1,
            "items": [MOCK_KANNAPEDIA_SCRAPED_PAYLOAD],
        }

        with patch.object(client.client, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            results = await client.collect_kannapedia("Headband", limit=3)

            # Verify correct endpoint and payload
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == "http://localhost:8001/collect"
            payload = call_args[1]["json"]
            assert payload["source"] == "kannapedia"
            assert payload["query"] == "Headband"
            assert payload["limit"] == 3

            # Verify results
            assert len(results) == 1
            assert results[0]["name"] == "Headband"

        await client.close()

    @pytest.mark.asyncio
    async def test_collect_kannapedia_by_rsp(self):
        from src.scraper_client import ScraperClient

        client = ScraperClient(base_url="http://localhost:8001")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "source": "kannapedia",
            "count": 1,
            "items": [MOCK_KANNAPEDIA_SCRAPED_PAYLOAD],
        }

        with patch.object(client.client, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            results = await client.collect_kannapedia_by_rsp(["rsp10154"])

            payload = mock_post.call_args[1]["json"]
            assert payload["source"] == "kannapedia"
            assert payload["rsp_numbers"] == ["rsp10154"]

        await client.close()

    @pytest.mark.asyncio
    async def test_collect_kannapedia_handles_errors(self):
        from src.scraper_client import ScraperClient

        client = ScraperClient(base_url="http://localhost:8001")

        with patch.object(client.client, "post", new_callable=AsyncMock, side_effect=Exception("Connection refused")):
            results = await client.collect_kannapedia("NonExistent")
            assert results == []

        await client.close()


# ── Test 3: Kannapedia index search regex ──

class TestKannapediaIndexSearch:
    """Test the regex that extracts strain names and RSP numbers from Kannapedia's index."""

    def test_regex_finds_matching_strains(self):
        import re

        query_lower = "headband"
        rsp_numbers = []

        for m in re.finditer(
            r'data-name="([^"]+)"[^>]*>\s*<a\s+href="/strains/(rsp\d+)"',
            MOCK_KANNAPEDIA_INDEX_HTML,
            re.IGNORECASE,
        ):
            strain_name = m.group(1).strip()
            rsp = m.group(2).strip()
            if query_lower in strain_name.lower():
                rsp_numbers.append(rsp)

        assert "rsp10154" in rsp_numbers
        assert "rsp10155" in rsp_numbers
        assert "rsp10200" not in rsp_numbers  # Blue Dream doesn't match "headband"

    def test_regex_is_case_insensitive(self):
        import re

        for query in ["Headband", "HEADBAND", "headband"]:
            rsp_numbers = []
            for m in re.finditer(
                r'data-name="([^"]+)"[^>]*>\s*<a\s+href="/strains/(rsp\d+)"',
                MOCK_KANNAPEDIA_INDEX_HTML,
                re.IGNORECASE,
            ):
                strain_name = m.group(1).strip()
                rsp = m.group(2).strip()
                if query.lower() in strain_name.lower():
                    rsp_numbers.append(rsp)
            assert len(rsp_numbers) >= 1, f"Failed for query: {query}"


# ── Test 4: End-to-end import pipeline mock ──

class TestImportPipelineIntegration:
    """Verify the import flow produces both observations AND genomic data."""

    def test_kannapedia_payload_has_required_fields(self):
        """Ensure the mock payload matches what KannapediaCollector._serialize_strain() produces."""
        required_keys = ["name", "general_info", "chemical_content", "genetic_relationships"]
        for key in required_keys:
            assert key in MOCK_KANNAPEDIA_SCRAPED_PAYLOAD, f"Missing key: {key}"

        # Verify chemical_content structure
        cc = MOCK_KANNAPEDIA_SCRAPED_PAYLOAD["chemical_content"]
        assert "cannabinoids" in cc
        assert "terpenoids" in cc
        assert len(cc["cannabinoids"]) > 0
        assert len(cc["terpenoids"]) > 0

    def test_etl_then_detail_has_genomic_data(self):
        """After ETL ingestion, a strain should have cannabinoids and terpenes."""
        from src.etl.kannapedia_etl import ingest_kannapedia_record

        result = ingest_kannapedia_record(MOCK_KANNAPEDIA_SCRAPED_PAYLOAD)

        sample = result["sample"]
        strain = result["strain"]

        # Genomic sample should be linked to the strain
        assert sample.canonical_strain_id == strain.id
        assert sample.rsp_number == "RSP10154"

        # Chemical profile must exist
        cp = sample.chemical_profile
        assert cp is not None, "Chemical profile is None — no genomic data was created!"
        assert cp.thc is not None and cp.thc > 0
        assert cp.myrcene is not None and cp.myrcene > 0

        # Genetic relationships must exist
        assert len(sample.genetic_relationships) > 0

        # Source record must exist
        src = result["source_record"]
        assert src.source_id == "RSP10154"

    def test_empty_kannapedia_result_doesnt_crash(self):
        """If Kannapedia returns no results, the import should continue gracefully."""
        from src.etl.kannapedia_etl import ingest_kannapedia_record

        # Empty payload should still work (with minimal data)
        minimal_payload = {
            "name": "Unknown Strain",
            "general_info": {},
            "chemical_content": {},
            "genetic_relationships": {},
            "blockchain": {},
        }

        result = ingest_kannapedia_record(minimal_payload)
        assert result["sample"] is not None
        assert result["strain"].primary_name == "Unknown Strain"
        assert result["sample"].chemical_profile is not None
        # Chemical profile should exist but with null values
        assert result["sample"].chemical_profile.thc is None
