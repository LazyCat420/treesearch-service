"""
search_helper.py
----------------
Helper functions to perform web search using scraper-service.
Bypasses rate-limiting by utilizing Playwright+Stealth to scrape Google/DuckDuckGo.
"""

import re
import urllib.parse
import logging
from bs4 import BeautifulSoup
from src.scraper_client import ScraperClient

logger = logging.getLogger(__name__)

async def search_search_engine_for_site(query: str, domain: str, limit: int = 10) -> list[str]:
    """
    Search DuckDuckGo or Google for matching pages in a specific domain.
    Returns a list of absolute URLs.
    """
    search_query = f"site:{domain} {query}"
    urls = []
    
    # --- Strategy 1: DuckDuckGo Native Collection ---
    logger.info(f"Querying DuckDuckGo native collection for: {search_query}")
    
    scraper = ScraperClient()
    try:
        results = await scraper.collect_duckduckgo(query=search_query, limit=limit)
        for r in results:
            href = r.get("url")
            if href and domain in href and href not in urls:
                urls.append(href)
                if len(urls) >= limit:
                    break
    except Exception as e:
        logger.warning(f"DuckDuckGo native collection failed: {e}")
    finally:
        await scraper.close()
        
    if urls:
        logger.info(f"DuckDuckGo search found {len(urls)} URLs for {domain}")
        return urls

    # --- Strategy 2: Google Search fallback ---
    safe_query = urllib.parse.quote_plus(search_query)
    google_url = f"https://www.google.com/search?q={safe_query}"
    logger.info(f"Querying Google Search fallback: {google_url}")
    
    scraper = ScraperClient()
    try:
        res = await scraper.scrape(
            google_url, 
            engine="playwright", 
            options={"raw_html": True, "wait_for": "#search"}
        )
        html = res.get("content")
        if html:
            soup = BeautifulSoup(html, "html.parser")
            # Google links typically reside in div.g a
            for a in soup.select("div.g a, div.r a, a[href^='http']"):
                href = a.get("href")
                if href and domain in href:
                    # Filter out Google cache/amp links
                    if "webcache.googleusercontent.com" in href or "google.com/search" in href:
                        continue
                    if href not in urls:
                        urls.append(href)
                        if len(urls) >= limit:
                            break
    except Exception as e:
        logger.error(f"Google Search fallback failed: {e}")
    finally:
        await scraper.close()

    logger.info(f"Google search found {len(urls)} URLs for {domain}")
    return urls
