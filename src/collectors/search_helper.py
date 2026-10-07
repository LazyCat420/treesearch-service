"""
search_helper.py
----------------
Find pages on one site through lazy-agent-service's shared keyless web search:
`site:<domain> <query>`, keeping only links on that domain.

This used to ask scraper-service's DuckDuckGo collector and then fall back to a
headless-Chrome Google search. The collector is switched off on the NAS
(DISABLE_DDG_SEARCH), so every strain import ran up to 8 headless Google
searches from an IP the search engines bot-block (docs/WEB_SEARCH.md).
"""

import logging

from src import shared_web_search

logger = logging.getLogger(__name__)


async def search_search_engine_for_site(query: str, domain: str, limit: int = 10) -> list[str]:
    """Absolute URLs on `domain` matching `query`; [] when the shared search finds
    nothing, refuses or cannot be reached."""
    results = await shared_web_search.search(f"site:{domain} {query}", limit)
    urls = []
    for r in results:
        href = r.get("url") or ""
        if href.startswith("http") and domain in href and href not in urls:
            urls.append(href)
            if len(urls) >= limit:
                break
    logger.info(f"Shared web search found {len(urls)} URLs for {domain}")
    return urls
