# Web search in treesearch-service

**Date:** 2026-10-06
**Status:** Every search goes through lazy-agent-service's shared keyless search, and the boot run never searches (verified).

## Why it changed

Every machine in the house leaves through one public IP, 71.198.70.27. DuckDuckGo, Startpage, Ecosia, Google and Mojeek bot-block it; DuckDuckGo first refused the NAS on 2026-07-27. A read-only audit on 2026-10-06 found treesearch searching on its own in three ways:

- **Strain fallbacks** (`src/collectors/web_fallback.py`): lineage and terpenes came from the `ddgs` library. `ddgs` scrapes DuckDuckGo and, on a blocked IP, tries about nine engines for every call: Wikipedia, Grokipedia, Brave, DuckDuckGo, Google, Mojeek, Yahoo, Yandex and Startpage.
- **Every boot** (`main.py` lifespan): background enrichment re-enriched every strain. Each strain that SeedFinder or Leafly lacked got one of those searches, on every restart. `DISABLE_STARTUP_ENRICHMENT` was not set.
- **Strain imports** (`src/collectors/search_helper.py`, used by the Reddit, Discourse and XenForo collectors): site lookups went to scraper-service's DuckDuckGo collector first. That collector is switched off on the NAS (`DISABLE_DDG_SEARCH`) and answers with no results, so every lookup fell through to a headless-Chrome Google search: up to 8 per import.

## What it does now

- `src/shared_web_search.py` is the one place treesearch searches. Its `search()` posts to lazy-agent-service's `POST /execute/web_search`, which is Exa's free keyless index with one cache and one rate limit for every project on the network (lazy-agent-service `documentation/chapters/04-shared-web-search.md`). It returns `[]` when nothing is found, when the shared search refuses (`rate_limited`, `busy`) and when it cannot be reached. It never raises. The address comes from `LAZY_TOOL_SERVICE_URL`, default `http://10.0.0.16:5591`.
- The strain fallbacks use its result snippets (`_web_snippets`). Site lookups use `site:<domain> <query>` and keep only links on that domain. The DuckDuckGo collector call and the headless Google fallback are deleted, as is `ScraperClient.collect_duckduckgo`.
- `enrich_all_strains(..., web_fallback=True)` has a switch. The boot run passes `web_fallback=False`, so a restart searches nothing and only uses SeedFinder and Leafly. `POST /api/strains/enrich` and imports still use the fallback; a person asked for those.

## How it was verified

- `tests/test_shared_web_search.py` (new) covers:
  - the request `search()` sends, and its empty answer for a refusal or an unreachable service;
  - the site search's `site:` query, domain filter and de-duplication;
  - the genetics fallback reading snippets from the shared search;
  - a check on the imports of `main.py` and every module under `src/` that none imports `ddgs`;
  - a database test that the boot run (`web_fallback=False`) never calls either web fallback, with the `web_fallback=True` run as a positive control so the check can fail.
- `tests/conftest.py` `no_network` now patches `src.shared_web_search.search` in place of the removed `_ddg_snippets` and `collect_duckduckgo`.
- The suite ran on a scratch SQLite database (`TEST_DATABASE_URL=sqlite+aiosqlite:///…`), because the Postgres test database's `trader` password was rotated that evening. 3 tests fail, and the same 3 fail on unchanged `master`, compared failure by failure:
  - `test_db.py::test_database_connection_and_crud`
  - `test_leafly_import.py::test_leafly_collector_parses_next_data`
  - `test_leafly_import.py::test_leafly_collector_fallback_slug`

  They are not caused by this change. They are still open.

## Follow-up (2026-10-06): the last DuckDuckGo traces

- `ddgs` is gone from `requirements.txt`. Nothing had imported it since the change above, but the image still installed it.
- The strain import's progress lines said "Querying DuckDuckGo fallback for … lineage…". They now say "Searching the web for … lineage…", and the log line reads "Web fallback parsed parents".
- `.env.example` no longer lists DuckDuckGo among scraper-service's sources.
- `tests/test_shared_web_search.py::test_ddgs_is_not_installed_and_no_message_names_duckduckgo` checks both the dependency and `main.py`'s messages. The same 3 tests as above still fail on SQLite, and also fail on unchanged `master`.
