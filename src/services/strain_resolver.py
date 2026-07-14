"""Canonical strain-name resolution.

Strain names arrive from forums, SeedFinder, Kannapedia and users in every possible
spelling: "Jack's Cleaner", "jacks_cleaner", "JACK'S CLEANER (Sannie's)". This module
resolves any of them to the one canonical primary_name in the database.

This used to exist twice — main.get_canonical_strain_name and
enrich_strains.resolve_strain_name — with the same four-stage algorithm but two
*separate* module-level caches. Only main's was ever cleared, so the background
enricher could keep resolving names against a cache that no import ever invalidated.
One implementation, one cache, one invalidation hook.
"""

import logging
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from src.genomics.normalization import normalize_strain_name, normalize_for_grouping
from src.models.orm import CanonicalStrainORM, StrainAliasORM

logger = logging.getLogger(__name__)

# name.lower().strip() -> canonical primary_name
_resolved_name_cache: dict[str, str] = {}
# normalize_for_grouping(name) -> canonical primary_name
_group_normalized_cache: dict[str, str] = {}


def invalidate_caches() -> None:
    """Drop the memoized name lookups. Call after anything that writes strains or aliases."""
    _resolved_name_cache.clear()
    _group_normalized_cache.clear()


async def resolve_canonical_name(session, name: str) -> Optional[str]:
    """Resolve any case/punctuation/alias variation of a strain name to its canonical name.

    Returns None if the strain is not in the database. Negative results are deliberately
    NOT cached — a name that misses today is often imported a moment later.
    """
    if not name:
        return None

    name_key = name.lower().strip()
    if name_key in _resolved_name_cache:
        return _resolved_name_cache[name_key]

    norm = normalize_strain_name(name)

    # 1. Case-insensitive exact match
    stmt = select(CanonicalStrainORM.primary_name).where(CanonicalStrainORM.primary_name.ilike(name))
    res = (await session.execute(stmt)).scalar()
    if res:
        _resolved_name_cache[name_key] = res
        return res

    # 2. Case- and punctuation-insensitive match
    stmt2 = select(CanonicalStrainORM.primary_name).where(
        func.regexp_replace(func.lower(CanonicalStrainORM.primary_name), '[^a-z0-9]', '', 'g') == norm
    )
    res = (await session.execute(stmt2)).scalar()
    if res:
        _resolved_name_cache[name_key] = res
        return res

    # 3. Check aliases
    stmt_alias = select(CanonicalStrainORM.primary_name).join(
        StrainAliasORM, CanonicalStrainORM.id == StrainAliasORM.canonical_strain_id
    ).where(
        or_(
            StrainAliasORM.name.ilike(name),
            func.regexp_replace(func.lower(StrainAliasORM.name), '[^a-z0-9]', '', 'g') == norm
        )
    )
    res = (await session.execute(stmt_alias)).scalar()
    if res:
        _resolved_name_cache[name_key] = res
        return res

    # 4. Group normalization — strips breeder suffixes, phenotype numbers etc.
    #    Populating the cache costs a full scan of strains + aliases, so it runs last.
    norm_group = normalize_for_grouping(name)
    if norm_group:
        if norm_group in _group_normalized_cache:
            res = _group_normalized_cache[norm_group]
            _resolved_name_cache[name_key] = res
            return res

        stmt_all = select(CanonicalStrainORM).options(selectinload(CanonicalStrainORM.aliases))
        all_strains = (await session.execute(stmt_all)).scalars().all()
        for s in all_strains:
            s_norm = normalize_for_grouping(s.primary_name)
            if s_norm:
                _group_normalized_cache[s_norm] = s.primary_name
            for a in s.aliases:
                a_norm = normalize_for_grouping(a.name)
                if a_norm:
                    _group_normalized_cache[a_norm] = s.primary_name

        if norm_group in _group_normalized_cache:
            res = _group_normalized_cache[norm_group]
            _resolved_name_cache[name_key] = res
            return res

    return None
