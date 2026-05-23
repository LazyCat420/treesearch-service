"""
Seedfinder.eu collector — scrapes strain data including lineage trees,
breeder info, flowering time, phenotypes, and awards.

Uses JSON-LD structured data from index pages for reliable parsing.
Strain detail pages are server-rendered HTML.
"""

import json
import re
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://seedfinder.eu/en"
HEADERS = {
    "User-Agent": "CannabisResearcher/1.0 (academic research)",
    "Accept-Language": "en-US,en;q=0.9",
}


async def search_seedfinder(query: str, limit: int = 20) -> list[dict]:
    """
    Search Seedfinder's alphabetical index for strains matching query.
    Uses JSON-LD structured data embedded in index pages.
    """
    query_lower = query.strip().lower()
    if len(query_lower) < 2:
        return []

    # Determine which letter pages to search
    first_char = query_lower[0].upper()
    if first_char.isalpha():
        letter = first_char
    else:
        letter = "1234567890"

    url = f"{BASE_URL}/database/strains/alphabetical/{letter}"

    html = None
    async with httpx.AsyncClient(
        timeout=15, headers=HEADERS, follow_redirects=True
    ) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logger.warning("Failed to fetch Seedfinder page %s: %s — trying scraper-service fallback", letter, e)
            try:
                from src.scraper_client import ScraperClient
                scraper = ScraperClient()
                try:
                    res = await scraper.scrape(url, engine="playwright", options={"raw_html": True})
                    if res.get("success") and res.get("content"):
                        html = res["content"]
                finally:
                    await scraper.close()
            except Exception as se:
                logger.error("Scraper-service fallback failed for Seedfinder page %s: %s", letter, se)

    if not html:
        return []

    # ── Strategy 1: Parse HTML links (primary — has all strains) ──
    all_matches = []
    for m in re.finditer(
        r'href="(?:https://seedfinder\.eu)?/en/strain-info/([^"]+?)/([^"]+?)"[^>]*>([^<]+)</a>',
        html,
        re.IGNORECASE,
    ):
        strain_slug = m.group(1)
        breeder_slug = m.group(2)
        name = m.group(3).strip()

        if query_lower in name.lower():
            all_matches.append({
                "name": name,
                "breeder": breeder_slug.replace("-", " ").title(),
                "strain_slug": strain_slug,
                "breeder_slug": breeder_slug,
                "url": f"{BASE_URL}/strain-info/{strain_slug}/{breeder_slug}/",
                "source": "seedfinder",
            })

        if len(all_matches) >= 200:
            break

    # Sort: exact matches first, then starts-with, then contains
    def sort_key(r):
        rname = r["name"].lower()
        if rname == query_lower:
            return (0, rname)
        if rname.startswith(query_lower):
            return (1, rname)
        return (2, rname)

    all_matches.sort(key=sort_key)
    results = all_matches

    # ── Strategy 2: Supplement with JSON-LD if available ──
    if not results:
        json_ld_matches = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )

        for ld_text in json_ld_matches:
            try:
                ld_data = json.loads(ld_text)
            except (json.JSONDecodeError, ValueError):
                continue

            if isinstance(ld_data, list):
                continue

            graphs = ld_data.get("@graph", [ld_data])
            for item in graphs:
                if item.get("@type") != "ItemList":
                    continue
                for list_item in item.get("itemListElement", []):
                    strain_item = list_item.get("item", {})
                    name = strain_item.get("name", "")
                    strain_url = strain_item.get("url", "")
                    brand = strain_item.get("brand", {})
                    breeder = brand.get("name", "") if isinstance(brand, dict) else ""

                    if not name or query_lower not in name.lower():
                        continue

                    slug_match = re.search(
                        r"/strain-info/([^/]+)/([^/]+)/?$", strain_url
                    )
                    if not slug_match:
                        continue

                    results.append({
                        "name": name,
                        "breeder": breeder,
                        "strain_slug": slug_match.group(1),
                        "breeder_slug": slug_match.group(2),
                        "url": strain_url,
                        "source": "seedfinder",
                    })

                    if len(results) >= limit:
                        break

    # Deduplicate by name+breeder
    seen = set()
    deduped = []
    for r in results:
        key = f"{r['name'].lower()}|{r['breeder_slug']}"
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped[:limit]


async def scrape_seedfinder_strain(
    strain_slug: str, breeder_slug: str
) -> Optional[dict]:
    """
    Scrape a single strain detail page from Seedfinder.
    Returns structured data including lineage, type, flowering time, etc.
    """
    url = f"{BASE_URL}/strain-info/{strain_slug}/{breeder_slug}/"

    html = None
    async with httpx.AsyncClient(
        timeout=30, headers=HEADERS, follow_redirects=True
    ) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logger.warning(
                "Failed to fetch Seedfinder strain %s/%s: %s — trying scraper-service fallback",
                strain_slug, breeder_slug, e,
            )
            try:
                from src.scraper_client import ScraperClient
                scraper = ScraperClient()
                try:
                    res = await scraper.scrape(url, engine="playwright", options={"raw_html": True})
                    if res.get("success") and res.get("content"):
                        html = res["content"]
                finally:
                    await scraper.close()
            except Exception as se:
                logger.error("Scraper-service fallback failed for strain %s/%s: %s", strain_slug, breeder_slug, se)

    if not html:
        return None

    # ── Detect error/404 pages ──
    # SeedFinder returns a styled 404 page instead of a proper HTTP 404
    if any(marker in html.lower() for marker in [
        "page_not_found", "page not found", "404 not found",
        "the page you requested could not be found",
    ]):
        logger.warning("SeedFinder returned 404/error page for %s/%s", strain_slug, breeder_slug)
        return None

    data = {
        "name": "",
        "breeder": breeder_slug.replace("-", " ").title(),
        "breeder_slug": breeder_slug,
        "strain_slug": strain_slug,
        "type": None,
        "flowering_time_days": None,
        "description": None,
        "lineage": [],
        "hybrids": [],
        "awards": [],
        "phenotypes": [],
        "url": url,
        "source": "seedfinder",
    }

    # ── Name from <title> ──
    title_match = re.search(r"<title>([^(]+)\(", html)
    if title_match:
        raw_name = title_match.group(1).strip()
        # Validate: name shouldn't contain HTML artifacts
        if "<" not in raw_name and len(raw_name) < 200:
            data["name"] = raw_name
    if not data["name"]:
        # Try h1
        h1_match = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
        if h1_match:
            data["name"] = h1_match.group(1).strip()
    if not data["name"]:
        data["name"] = strain_slug.replace("-", " ").title()

    # ── Type (indica/sativa) ──
    type_match = re.search(
        r"is\s+(?:an?\s+)?(mostly\s+)?(indica|sativa|ruderalis)\s*/\s*(indica|sativa|ruderalis)",
        html, re.IGNORECASE,
    )
    if type_match:
        prefix = (type_match.group(1) or "").strip()
        t1 = type_match.group(2).lower()
        t2 = type_match.group(3).lower()
        data["type"] = f"{prefix} {t1}/{t2}".strip()
    else:
        for pattern, label in [
            (r"indica\s*/\s*sativa", "indica/sativa"),
            (r"sativa\s*/\s*indica", "sativa/indica"),
            (r"mostly indica", "mostly indica"),
            (r"mostly sativa", "mostly sativa"),
            (r"pure indica", "pure indica"),
            (r"pure sativa", "pure sativa"),
            (r"ruderalis", "ruderalis"),
        ]:
            if re.search(pattern, html, re.IGNORECASE):
                data["type"] = label
                break

    # ── Flowering time ──
    flower_match = re.search(
        r"flowering\s+time\s+of\s+[±~]?\s*(\d+)\s*(?:[-–]\s*(\d+)\s+)?days",
        html, re.IGNORECASE,
    )
    if flower_match:
        low = int(flower_match.group(1))
        high = int(flower_match.group(2)) if flower_match.group(2) else low
        data["flowering_time_days"] = (low + high) // 2

    # ── Description ──
    desc_match = re.search(
        r"Description</h\d>.*?<p[^>]*>(.*?)</p>",
        html, re.DOTALL | re.IGNORECASE,
    )
    if desc_match:
        desc_text = re.sub(r"<[^>]+>", "", desc_match.group(1)).strip()
        if len(desc_text) > 20:
            data["description"] = desc_text[:2000]

    # ── Lineage (parent strains) ──
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    lineage_div = soup.find(id="lineage")
    if lineage_div:
        seen_lineage = set()
        for a in lineage_div.find_all("a", href=True):
            href = a["href"]
            if any(term in href for term in ["/genealogy", "/family-tree", "/hybrid-map"]):
                continue
            m = re.search(r'/strain-info/([^/]+)/([^/]+)/?$', href)
            if m:
                parent_slug = m.group(1)
                parent_breeder = m.group(2)
                parent_name = a.get_text().strip()
                key = f"{parent_name.lower()}|{parent_breeder}"
                if key not in seen_lineage and parent_slug != strain_slug:
                    seen_lineage.add(key)
                    data["lineage"].append({
                        "name": parent_name,
                        "breeder": parent_breeder.replace("-", " ").title(),
                        "strain_slug": parent_slug,
                        "breeder_slug": parent_breeder,
                    })

    # ── Hybrids/Crossbreeds ──
    hybrids_div = soup.find(id="hybrids")
    if hybrids_div:
        seen_hybrids = set()
        for a in hybrids_div.find_all("a", href=True):
            href = a["href"]
            if any(term in href for term in ["/genealogy", "/family-tree", "/hybrid-map"]):
                continue
            m = re.search(r'/strain-info/([^/]+)/([^/]+)/?$', href)
            if m:
                h_slug = m.group(1)
                h_breeder = m.group(2)
                h_name = a.get_text().strip()
                if "(" in h_name:
                    h_name = h_name.split("(")[0].strip()
                key = f"{h_name.lower()}|{h_breeder}"
                if key not in seen_hybrids and h_slug != strain_slug:
                    seen_hybrids.add(key)
                    data["hybrids"].append({
                        "name": h_name,
                        "breeder": h_breeder.replace("-", " ").title(),
                    })
        data["hybrids"] = data["hybrids"][:50]

    # ── Awards ──
    for award_match in re.finditer(
        r"(\d+(?:st|nd|rd|th)\s+Place).*?at\s+the\s+([^<\n]+?)(?:\s*</|\n)",
        html, re.IGNORECASE,
    ):
        award = f"{award_match.group(1)} at {award_match.group(2).strip()}"
        data["awards"].append(award)

    # ── Phenotypes ──
    for pheno_match in re.finditer(
        r"<li>\s*((?:long|short|compact|stretched|slowly|fast)[^<]+)</li>",
        html, re.IGNORECASE,
    ):
        data["phenotypes"].append(pheno_match.group(1).strip())

    return data
