"""treesearch searches only through lazy-agent-service's shared keyless search.

It used to search on its own: the `ddgs` library for strain fallbacks (DuckDuckGo,
and about nine engines per call on a blocked IP), and scraper-service's DuckDuckGo
collector followed by a headless-Chrome Google search for site lookups. The free
search engines bot-block this network's one public IP (docs/WEB_SEARCH.md).
"""

import ast
import pathlib
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from src import shared_web_search
from src.collectors import search_helper, web_fallback

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Async tests throughout, as in the rest of the suite. A sync test that runs its own
# event loop closes the loop this pytest-asyncio version expects the next async test
# to find.
pytestmark = pytest.mark.asyncio


class _Client:
    """Stands in for httpx.AsyncClient; records each POST."""
    calls, reply, error = [], {}, None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, **k):
        _Client.calls.append((url, json))
        if _Client.error:
            raise _Client.error

        class _Resp:
            def json(self_inner):
                return _Client.reply
        return _Resp()


def _use(monkeypatch, reply=None, error=None):
    _Client.calls, _Client.reply, _Client.error = [], reply or {}, error
    monkeypatch.setattr(shared_web_search.httpx, "AsyncClient", _Client)


async def test_search_asks_the_shared_web_search(monkeypatch):
    results = [{"title": "OG Kush", "url": "https://www.seedfinder.eu/og-kush", "snippet": "Chemdawg x Hindu Kush"}]
    _use(monkeypatch, {"status": "ok", "results": results})
    assert await shared_web_search.search("og kush genetics", 25) == results
    assert _Client.calls == [(shared_web_search.WEB_SEARCH_URL, {"query": "og kush genetics", "limit": 10})]
    assert shared_web_search.WEB_SEARCH_URL.endswith("/execute/web_search")


@pytest.mark.parametrize("reply,error", [
    ({"status": "rate_limited", "error": "searches resume in 40 s"}, None),
    ({"status": "busy"}, None),
    (None, OSError("lazy-agent-service down")),
])
async def test_search_returns_nothing_when_refused_or_unreachable(monkeypatch, reply, error):
    _use(monkeypatch, reply, error)
    assert await shared_web_search.search("q") == []


async def test_site_search_uses_the_site_operator_and_keeps_only_that_domain():
    hits = [
        {"url": "https://www.reddit.com/r/microgrowery/comments/a"},
        {"url": "https://www.reddit.com/r/microgrowery/comments/a"},
        {"url": "https://example.com/elsewhere"},
        {"url": "https://www.reddit.com/r/microgrowery/comments/b"},
    ]
    with patch.object(shared_web_search, "search", AsyncMock(return_value=hits)) as search:
        urls = await search_helper.search_search_engine_for_site("blue dream", "reddit.com/r/microgrowery", limit=5)
    search.assert_awaited_once_with("site:reddit.com/r/microgrowery blue dream", 5)
    assert urls == ["https://www.reddit.com/r/microgrowery/comments/a",
                    "https://www.reddit.com/r/microgrowery/comments/b"]


async def test_fallback_snippets_come_from_the_shared_search():
    hits = [{"snippet": "Blue Dream »»» Blueberry x Haze"}, {"snippet": ""}]
    with patch.object(shared_web_search, "search", AsyncMock(return_value=hits)) as search:
        parents = await web_fallback.fallback_search_genetics("Blue Dream")
    assert search.await_args.args[0] == 'site:seedfinder.eu "Blue Dream"'
    assert parents == ["Blueberry", "Haze"]


async def test_no_module_imports_ddgs():
    for path in [ROOT / "main.py", *sorted((ROOT / "src").rglob("*.py"))]:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any("ddgs" in n or "duckduckgo" in n for n in names), f"{path}: {names}"


async def test_the_boot_enrichment_never_searches_the_web():
    """A restart re-enriches every strain. With the web fallback on, that was a
    search for every strain SeedFinder and Leafly lack, on every boot."""
    from src.db import get_session, init_db
    from src.enrich_strains import enrich_all_strains
    from src.models.orm import BreederORM, CanonicalStrainORM

    await init_db()
    async with get_session() as session:
        breeder = (await session.execute(select(BreederORM).where(BreederORM.name == "Boot Search Breeder"))).scalars().first()
        if not breeder:
            breeder = BreederORM(name="Boot Search Breeder")
            session.add(breeder)
            await session.flush()
        if not (await session.execute(select(CanonicalStrainORM).where(
                CanonicalStrainORM.primary_name == "Boot_Search_Strain"))).scalars().first():
            session.add(CanonicalStrainORM(primary_name="Boot_Search_Strain", breeder_id=breeder.id, strain_type="hybrid"))
        await session.commit()

    async def run(web_fallback_on: bool):
        with patch("src.enrich_strains.ScraperClient.collect_leafly", new_callable=AsyncMock, return_value=None), \
             patch("src.collectors.seedfinder_collector.search_seedfinder", new_callable=AsyncMock, return_value=[]), \
             patch("src.enrich_strains.fallback_search_genetics", new_callable=AsyncMock, return_value=[]) as genetics, \
             patch("src.enrich_strains.fallback_search_terpenes", new_callable=AsyncMock, return_value={}) as terpenes:
            async with get_session() as session:
                await enrich_all_strains(session, web_fallback=web_fallback_on)
                await session.commit()
        return genetics.await_count + terpenes.await_count

    assert await run(False) == 0, "the boot run searched the web"
    # Positive control: the same strain does reach the fallback when it is on
    # (POST /api/strains/enrich), so the check above can fail.
    assert await run(True) > 0
