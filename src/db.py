import os
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

from sqlalchemy import text

async def init_db():
    """Initialize the database, creating all tables if they don't exist."""
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

async def get_session() -> AsyncSession:
    """Get an async database session."""
    async with AsyncSessionLocal() as session:
        yield session
