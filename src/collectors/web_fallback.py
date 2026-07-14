"""Web-search fallbacks for strain genetics and terpene profiles.

Used when the structured sources (SeedFinder, Kannapedia, Leafly) have nothing for a
strain: search the open web and scrape what we can out of the result snippets.

This module exists to break an import cycle. These functions used to live in main.py,
which src/enrich_strains.py imported *back* from — a cycle through the FastAPI entrypoint
that forced ~55 imports to be deferred inside function bodies. Both callers now import
from here instead.

Snippet-derived numbers are low-confidence by nature. Callers must treat anything from
this module as a hint, never as a lab measurement.
"""

import asyncio
import logging
import re

logger = logging.getLogger(__name__)

_DDG_MAX_RESULTS = 10


async def _ddg_snippets(query: str, max_results: int = _DDG_MAX_RESULTS) -> list[str]:
    """Run a DuckDuckGo text search and return the result body snippets.

    ddgs is synchronous, so it runs in a worker thread — calling it directly would
    block the event loop for the whole request.

    (This previously shelled out via asyncio.create_subprocess_exec to a hardcoded
    interpreter path inside *another service's* virtualenv —
    /home/lazycat/.../scraper-service/.venv/bin/python — which does not exist inside
    the container, so the fallback silently returned nothing in every deployment.
    ddgs is a direct dependency; just call it.)
    """
    def _search() -> list[str]:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return [r.get("body", "") for r in results if r.get("body")]

    try:
        return await asyncio.to_thread(_search)
    except Exception as e:
        logger.error(f"DuckDuckGo fallback search failed for {query!r}: {e}")
        return []


def parse_genetics_from_snippets(snippets: list[str], strain_name: str) -> list[str]:
    """Extract parent strain names from search-result snippets."""
    # Try different regex strategies across all snippets
    for snip in snippets:
        snip_clean = snip.replace('\xa0', ' ').replace('‎', '')

        # Strategy 1: Look for "StrainName »»» Parent1 x Parent2"
        match = re.search(r'»»»\s*([^·\n]+)', snip_clean)
        if match:
            cross_text = match.group(1).strip()
            if any(x in cross_text.lower() for x in [' x ', '×', ' x']):
                parts = re.split(r'\s+[xX×]\s+|\s+x\s+|_x_|_X_', cross_text)
                parents = [p.strip() for p in parts if p.strip()]
                parents = [p for p in parents if len(p) > 2 and p.lower() not in ["mostly indica", "mostly sativa", "hybrid"]]
                if len(parents) >= 2:
                    return parents

        # Strategy 2: Look for "Genetic:Parent1 x Parent2"
        match_genetic = re.search(r'Genetic\s*:\s*([^.\n]+)', snip_clean, re.IGNORECASE)
        if match_genetic:
            cross_text = match_genetic.group(1).strip()
            cross_text = re.split(r'flowering|characteristics|strong|medicinal', cross_text, flags=re.IGNORECASE)[0].strip()
            if any(x in cross_text.lower() for x in [' x ', '×', ' x']):
                parts = re.split(r'\s+[xX×]\s+|\s+x\s+|_x_|_X_', cross_text)
                parents = [p.strip() for p in parts if p.strip()]
                parents = [p for p in parents if len(p) > 2 and p.lower() not in ["mostly indica", "mostly sativa", "hybrid"]]
                if len(parents) >= 2:
                    return parents

        # Strategy 3: Capitalized names separated by 'x'.
        #
        # This is the loosest strategy and it feeds create_parent_placeholder(), so a bad
        # match becomes a real CanonicalStrain row. It is bounded hard: each side must look
        # like a strain name (1-4 capitalised tokens), and BOTH sides must pass or the whole
        # match is discarded.
        #
        # Unbounded, it used to turn "I grew this in Week 5 x Week 6 of flowering" into the
        # parent strains "I grew this in Week 5" and "Week 6 of flowering and it was great".
        for match in re.finditer(_CROSS_PATTERN, snip_clean):
            candidates = [p.strip() for p in match.groups() if p]
            if len(candidates) >= 2 and all(_is_plausible_strain_name(p) for p in candidates):
                return candidates

    return []


# A strain-name-shaped token run: 1-4 capitalised words, allowing digits, #, ' and -.
_NAME = r"(?:[A-Z][\w'\-#]*)(?:\s+[A-Z0-9#][\w'\-#]*){0,3}"
_CROSS_PATTERN = re.compile(
    rf"({_NAME})\s+[xX×*]\s+({_NAME})(?:\s+[xX×*]\s+({_NAME}))?"
)

# Words that start a sentence, not a strain name.
_NAME_STOPWORDS = {
    "i", "it", "this", "that", "the", "a", "an", "my", "we", "you", "they",
    "great", "nice", "amazing", "good", "best", "very", "week", "day", "grew",
    "grow", "growing", "smoke", "smoked", "cross", "crossed", "mostly", "hybrid",
    "indica", "sativa", "genetics", "genetic", "lineage", "flowering", "harvest",
}

_NON_NAMES = {"mostly indica", "mostly sativa", "hybrid"}


def _is_plausible_strain_name(candidate: str) -> bool:
    """Reject sentence fragments that the cross-pattern happened to straddle."""
    if not candidate:
        return False
    if not (2 < len(candidate) < 40):
        return False
    if candidate.lower() in _NON_NAMES:
        return False

    words = candidate.split()
    if not words or len(words) > 4:
        return False
    # A strain name does not begin with a sentence word.
    if words[0].lower() in _NAME_STOPWORDS:
        return False
    return True


async def fallback_search_genetics(strain_name: str) -> list[str]:
    """Search the web for a strain's parents. Returns [] if nothing parses."""
    snippets = await _ddg_snippets(f'site:seedfinder.eu "{strain_name}"')
    if not snippets:
        return []
    return parse_genetics_from_snippets(snippets, strain_name)


_TERPENE_VARIANTS = {
    "myrcene": "myrcene",
    "β-myrcene": "myrcene",
    "beta-myrcene": "myrcene",
    "limonene": "limonene",
    "d-limonene": "limonene",
    "caryophyllene": "caryophyllene",
    "β-caryophyllene": "caryophyllene",
    "beta-caryophyllene": "caryophyllene",
    "pinene": "pinene_alpha",
    "α-pinene": "pinene_alpha",
    "alpha-pinene": "pinene_alpha",
    "β-pinene": "pinene_beta",
    "beta-pinene": "pinene_beta",
    "linalool": "linalool",
    "humulene": "humulene",
    "α-humulene": "humulene",
    "alpha-humulene": "humulene",
    "terpinolene": "terpinolene",
    "ocimene": "ocimene",
}

# Longest first so "beta-caryophyllene" wins over the bare "caryophyllene".
_TERPENE_ALTERNATION = "|".join(
    re.escape(k) for k in sorted(_TERPENE_VARIANTS, key=len, reverse=True)
)

# The gap between a terpene name and its percentage may only contain connector
# characters/words — never a sentence boundary. The old pattern allowed any 10
# non-digit chars, so "Dominant terpene: myrcene. THC: 24.5%" happily bound THC's
# 24.5% to myrcene.
_CONNECTOR = r"[\s:=\-–—]{0,3}(?:(?:of|at|content|level|is|around|about)\s+)?\(?\s?"

_TERPENE_PATTERNS = [
    re.compile(rf"\b({_TERPENE_ALTERNATION})\b{_CONNECTOR}(\d+(?:\.\d+)?)\s*%"),
    re.compile(rf"(\d+(?:\.\d+)?)\s*%\s*(?:of\s+)?\b({_TERPENE_ALTERNATION})\b"),
]

# A single terpene above this is not a terpene reading — it is a cannabinoid (THC/CBD
# run 15-30%) that the snippet happened to place nearby. Real terpenes are well under 5%.
_MAX_PLAUSIBLE_TERPENE_PCT = 5.0


def parse_terpenes_from_snippets(snippets: list[str]) -> dict[str, float]:
    """Extract terpene percentages from search-result snippets.

    Returns {} when nothing plausible parses. It deliberately does NOT guess: an
    earlier version, finding no percentages, counted how often each terpene was
    *mentioned* and emitted `(count / total) * 1.5` as the value. Those invented
    numbers were written to the same columns as real lab assays and were
    indistinguishable from them.
    """
    parsed_terps: dict[str, float] = {}

    for snip in snippets:
        snip_lower = snip.lower()

        for pattern in _TERPENE_PATTERNS:
            for match in pattern.finditer(snip_lower):
                g1, g2 = match.groups()
                try:
                    if g1.replace(".", "", 1).isdigit():
                        val, name = float(g1), g2
                    else:
                        name, val = g1, float(g2)
                except ValueError:
                    continue

                if val > _MAX_PLAUSIBLE_TERPENE_PCT:
                    continue

                canonical_name = _TERPENE_VARIANTS.get(name)
                if canonical_name:
                    if canonical_name not in parsed_terps or val > parsed_terps[canonical_name]:
                        parsed_terps[canonical_name] = val

    return parsed_terps


async def fallback_search_terpenes(strain_name: str) -> dict[str, float]:
    """Search the web for a strain's terpene percentages. Returns {} if nothing parses."""
    snippets = await _ddg_snippets(f'"{strain_name}" terpene profile OR terpenes')
    if not snippets:
        return {}
    return parse_terpenes_from_snippets(snippets)
