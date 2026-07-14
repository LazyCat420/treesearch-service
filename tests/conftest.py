import asyncio
import inspect
import os
import pytest
from unittest.mock import AsyncMock, patch

# ---------------------------------------------------------------------------
# Test database isolation.
#
# This MUST run before anything imports src.db — that module builds its engine
# at import time from DATABASE_URL, and it now refuses to start without one.
# Without this, the suite would read and WRITE the shared production database
# that the trading system also lives in.
# ---------------------------------------------------------------------------
_TEST_DB = os.getenv("TEST_DATABASE_URL")
if not _TEST_DB:
    raise RuntimeError(
        "TEST_DATABASE_URL is not set. The tests create, truncate and drop tables — "
        "point it at a throwaway database, never at trading_bot. See .env.example.\n"
        "  createdb treesearch_test  # on 10.0.0.16:5433\n"
        "  export TEST_DATABASE_URL=postgresql+asyncpg://trader:<pass>@10.0.0.16:5433/treesearch_test"
    )
if "/trading_bot" in _TEST_DB:
    raise RuntimeError(
        f"TEST_DATABASE_URL points at the production trading_bot database ({_TEST_DB}). "
        "Refusing to run — the tests would destroy live data."
    )
os.environ["DATABASE_URL"] = _TEST_DB


# Monkeypatch inspect.findsource to handle virtualenv absolute path mismatches pointing to rods-project
_orig_findsource = inspect.findsource
def _patched_findsource(obj):
    try:
        return _orig_findsource(obj)
    except OSError as e:
        try:
            file = inspect.getfile(obj)
            if "/rods-project/" in file:
                new_file = file.replace("/rods-project/", "/projects/")
                if os.path.exists(new_file):
                    _orig_getfile = inspect.getfile
                    _orig_getsourcefile = inspect.getsourcefile
                    inspect.getfile = lambda o: new_file
                    inspect.getsourcefile = lambda o: new_file
                    try:
                        return _orig_findsource(obj)
                    finally:
                        inspect.getfile = _orig_getfile
                        inspect.getsourcefile = _orig_getsourcefile
        except Exception:
            pass
        raise e

inspect.findsource = _patched_findsource


@pytest.fixture(scope="session")
def event_loop():
    """Create a single session-scoped event loop to prevent event loop mismatch errors in async tests."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def _reset_schema():
    """Wipe the test schema once at session start so runs cannot accumulate rows.

    Individual tests call init_db() themselves to recreate what they need.

    Two deliberate choices here:

    * It drops the whole schema rather than calling Base.metadata.drop_all(), because
      drop_all only knows about tables the ORM still declares. A table that was removed
      from the models but still exists in the database blocks the drop with a dependent
      foreign-key error and can never be cleaned up.
    * It is synchronous and opens its own short-lived asyncpg connection rather than
      reusing src.db.engine. A session-scoped async fixture runs on a different event
      loop than the function-scoped tests, which binds the engine's pool to the wrong
      loop and fails every subsequent test with "attached to a different loop".
    """
    import asyncpg

    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")

    async def _wipe():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        finally:
            await conn.close()

    asyncio.run(_wipe())
    yield


@pytest.fixture
def no_network():
    """Stub out every outbound network call the import pipeline makes.

    The import route fans out to five forum collectors, SeedFinder, Kannapedia,
    Leafly, a DuckDuckGo fallback and a vision model. Unmocked, a single test
    blocks on live HTTP until it is killed — which is exactly what
    test_concurrency_import_no_duplicates used to do.
    """
    targets = {
        "src.collectors.discourse_collector.DiscourseCollector.search": [],
        "src.collectors.xenforo_collector.XenForoCollector.search": [],
        "src.collectors.reddit_collector.RedditCollector.search": [],
        "src.collectors.seedfinder_collector.search_seedfinder": [],
        "src.collectors.seedfinder_collector.scrape_seedfinder_strain": None,
        "src.scraper_client.ScraperClient.collect_kannapedia": [],
        "src.scraper_client.ScraperClient.collect_kannapedia_by_rsp": [],
        "src.scraper_client.ScraperClient.collect_leafly": None,
        "src.scraper_client.ScraperClient.collect_duckduckgo": [],
        "src.scraper_client.ScraperClient.collect": {"items": [], "count": 0},
        "src.ml.clustering.classify_images_batch": {},
        "main.get_translation_cached": {"translated_text": "", "detected_language": "en"},
        # The web fallbacks are imported by name into both main and enrich_strains, so
        # the bindings must be patched at each call site — patching only the defining
        # module would leave both callers pointing at the real, network-hitting function.
        "src.collectors.web_fallback._ddg_snippets": [],
        "src.collectors.web_fallback.fallback_search_genetics": [],
        "src.collectors.web_fallback.fallback_search_terpenes": {},
        "main.fallback_search_genetics": [],
        "main.fallback_search_terpenes": {},
        "src.enrich_strains.fallback_search_genetics": [],
        "src.enrich_strains.fallback_search_terpenes": {},
    }

    patchers = [
        patch(target, new_callable=AsyncMock, return_value=value)
        for target, value in targets.items()
    ]
    for p in patchers:
        p.start()
    try:
        yield
    finally:
        for p in patchers:
            p.stop()
