import os
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. This service has no default connection string — "
        "a credential must never be committed to this repo. "
        "Set it in deploy-kit/.env.deploy (staged into the container as .env), "
        "or export it locally. See .env.example for the expected format."
    )

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Collapse forum posts that were saved more than once for the same (forum, post id).
# The old dedupe check matched on source_id alone, so re-imports and cross-forum id
# collisions left true duplicates behind (196 of them in the live database).
#
# Keep the oldest row of each group and re-parent its siblings' images onto it, so
# deduplicating can never drop a photo. Idempotent: a no-op once the data is clean.
_DEDUPE_OBSERVATIONS = """
WITH ranked AS (
    SELECT id,
           first_value(id) OVER (
               PARTITION BY source_name, source_id
               ORDER BY created_at NULLS LAST, id
           ) AS keeper_id
    FROM observations
),
dupes AS (
    SELECT id, keeper_id FROM ranked WHERE id <> keeper_id
),
moved AS (
    UPDATE observation_images oi
    SET observation_id = d.keeper_id
    FROM dupes d
    WHERE oi.observation_id = d.id
    RETURNING 1
)
DELETE FROM observations o USING dupes d WHERE o.id = d.id;
"""


async def init_db():
    """Initialize the database, creating all tables if they don't exist."""
    # Importing the models registers them on Base.metadata. Without this, a caller that
    # has not already imported src.models.orm gets an empty metadata and create_all()
    # silently creates nothing.
    from src.models import orm  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrate unique indexes
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_breeders_name ON breeders (name);"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_strains_normalized_name "
            "ON canonical_strains (regexp_replace(lower(primary_name), '[^a-z0-9]', '', 'g'));"
        ))

    # A forum post id is only unique within its own forum. Declared as raw DDL rather than
    # a UniqueConstraint in orm.py because create_all() never ALTERs an existing table —
    # it would silently do nothing.
    #
    # The existing rows must be deduplicated first or the index cannot be built. This runs
    # in its OWN transaction: a failure here would abort the enclosing one, and a missing
    # index must never stop the service from booting.
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(_DEDUPE_OBSERVATIONS))
            if result.rowcount:
                logger.warning(
                    "Removed %s duplicate observation(s) before indexing "
                    "(their images were re-parented, not deleted).",
                    result.rowcount,
                )
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_observations_source "
                "ON observations (source_name, source_id);"
            ))
    except Exception as e:
        logger.error("Could not create uq_observations_source: %s", e)

@asynccontextmanager
async def get_session():
    """Get an async database session (async context manager).

    Was a bare async generator driven by `async for session in get_session():`.
    A `return` from inside that loop suspends the generator at the yield — its
    `async with AsyncSessionLocal()` cleanup only ran at GC, so the asyncpg
    connection sat idle-in-transaction until finalized and the pool
    (5 + overflow 10) drained under load. As a context manager, cleanup runs
    deterministically at block exit. Use `async with get_session() as session:`.
    """
    async with AsyncSessionLocal() as session:
        yield session
