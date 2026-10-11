"""Web search through lazy-agent-service's shared keyless search (Exa).

One cache and one rate limit for every project on the network
(lazy-agent-service documentation/chapters/04-shared-web-search.md).
treesearch used to search on its own: the `ddgs` library for strain fallbacks
(it scrapes DuckDuckGo and, on a blocked IP, tries about nine engines per call),
and scraper-service's DuckDuckGo collector followed by a headless-Chrome Google
search for site lookups. The free search engines bot-block this network's one
public IP, and each of those made it worse (docs/WEB_SEARCH.md).

Callers use `shared_web_search.search(...)` through the module, so tests can
patch this one function instead of every import site.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

WEB_SEARCH_URL = os.getenv("LAZY_TOOL_SERVICE_URL", "http://10.0.0.16:5591").rstrip("/") + "/execute/web_search"


async def search(query: str, limit: int = 10) -> list[dict]:
    """Results as [{title, url, snippet, published, author}].

    [] when nothing is found, when the shared search refuses (rate_limited,
    busy) and when it cannot be reached. Never raises.
    """
    try:
        # The shared search may queue a call for up to 20 s, then give Exa 15 s.
        # POST /execute needs EXECUTE_API_KEY once the server sets it (W3 audit).
        key = os.getenv("EXECUTE_API_KEY", "").strip()
        async with httpx.AsyncClient(timeout=40.0) as client:
            resp = await client.post(WEB_SEARCH_URL, json={"query": query, "limit": min(int(limit), 10)},
                                     headers={"x-api-key": key} if key else None)
            payload = resp.json()
    except Exception as e:
        logger.warning(f"Shared web search failed for {query!r}: {e}")
        return []
    if payload.get("status") != "ok":
        logger.warning(f"Shared web search answered {payload.get('status')} for {query!r}: {payload.get('error', '')}")
        return []
    return payload.get("results") or []
