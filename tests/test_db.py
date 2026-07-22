import pytest
import os
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from src.db import init_db, get_session, engine
from src.models.orm import BreederORM, CanonicalStrainORM, GenomicSampleORM
from src.etl.kannapedia_etl import ingest_kannapedia_record

pytestmark = pytest.mark.asyncio

async def test_database_connection_and_crud():
    # Verify the database URL is pointing to the correct instance
    db_url = str(engine.url)
    assert "10.0.0.16" in db_url or "localhost" in db_url, "Database is not configured to expected host"

    # Initialize tables
    await init_db()

    async with get_session() as session:
        # Create a mock breeder
        breeder = BreederORM(
            name="Test Breeder",
            website="https://example.com"
        )
        session.add(breeder)
        await session.flush()  # to get the ID

        # Create a mock strain
        strain = CanonicalStrainORM(
            primary_name="Test Strain 420",
            breeder_id=breeder.id,
            strain_type="hybrid"
        )
        session.add(strain)
        await session.commit()

        # Fetch it back
        stmt = select(CanonicalStrainORM).where(CanonicalStrainORM.primary_name == "Test Strain 420")
        result = await session.execute(stmt)
        fetched_strain = result.scalar_one_or_none()
        
        assert fetched_strain is not None
        assert fetched_strain.primary_name == "Test Strain 420"
        
        # Cleanup test data
        await session.delete(fetched_strain)
        
        breeder_stmt = select(BreederORM).where(BreederORM.id == breeder.id)
        result_breeder = await session.execute(breeder_stmt)
        fetched_breeder = result_breeder.scalar_one_or_none()
        if fetched_breeder:
            await session.delete(fetched_breeder)
            
        await session.commit()

    # Now exercise the real Kannapedia ingest path used by POST /api/ingest/kannapedia:
    # transform the raw payload into domain models, then persist them.
    mock_scraped_data = {
        "name": "White Fire",
        "general_info": {
            "Grower": "OG Seed Bank"
        },
        "chemical_content": {
            "cannabinoids": {
                "THC": "24.5%",
                "CBD": "0.1%"
            },
            "terpenoids": {
                "Myrcene": "1.2%",
                "Limonene": "0.8%"
            }
        }
    }

    from main import save_domain_models_to_db

    result = ingest_kannapedia_record(mock_scraped_data)
    async with get_session() as session:
        await save_domain_models_to_db(session, result)
        await session.commit()

    # Verify it was saved correctly. Note the Kannapedia path stores chemistry on the
    # sample's ChemicalProfile, not as strain-level averages — the strain row carries
    # identity, the sample carries the assay.
    async with get_session() as session:
        stmt = (
            select(CanonicalStrainORM)
            .where(CanonicalStrainORM.primary_name == "White Fire")
            .options(
                selectinload(CanonicalStrainORM.genomic_samples).selectinload(
                    GenomicSampleORM.chemical_profile
                )
            )
        )
        result_row = await session.execute(stmt)
        strain = result_row.scalar_one_or_none()

        assert strain is not None
        assert strain.primary_name == "White Fire"

        assert len(strain.genomic_samples) == 1
        profile = strain.genomic_samples[0].chemical_profile
        assert profile is not None
        assert profile.total_thc == 24.5
        assert profile.total_cbd == 0.1
        assert profile.myrcene == 1.2
        assert profile.limonene == 0.8

        # Cleanup. Aliases must go first — strain_aliases.canonical_strain_id is NOT NULL,
        # so letting SQLAlchemy null it out on delete raises an IntegrityError.
        from sqlalchemy import delete
        from src.models.orm import StrainAliasORM, SourceGenomicsRecordORM

        await session.execute(
            delete(StrainAliasORM).where(StrainAliasORM.canonical_strain_id == strain.id)
        )
        sample_ids = [s.id for s in strain.genomic_samples]
        if sample_ids:
            await session.execute(
                delete(SourceGenomicsRecordORM).where(
                    SourceGenomicsRecordORM.genomic_sample_id.in_(sample_ids)
                )
            )
        for sample in strain.genomic_samples:
            if sample.chemical_profile:
                await session.delete(sample.chemical_profile)
            await session.delete(sample)
        await session.delete(strain)

        breeder_stmt = select(BreederORM).where(BreederORM.id == strain.breeder_id)
        result_breeder = await session.execute(breeder_stmt)
        breeder = result_breeder.scalar_one_or_none()
        if breeder:
            await session.delete(breeder)

        await session.commit()


async def test_get_canonical_strain_name():
    from main import get_canonical_strain_name
    from src.models.orm import StrainAliasORM

    await init_db()

    async with get_session() as session:
        # Create a mock breeder
        breeder = BreederORM(
            name="Alias Test Breeder",
            website="https://example.com"
        )
        session.add(breeder)
        await session.flush()

        # Create a mock strain with name "Test_GetCanonical_Head_Band"
        strain = CanonicalStrainORM(
            primary_name="Test_GetCanonical_Head_Band",
            breeder_id=breeder.id,
            strain_type="hybrid"
        )
        session.add(strain)
        await session.flush()

        # Create a mock alias
        alias = StrainAliasORM(
            canonical_strain_id=strain.id,
            name="TestGetCanonicalHeadband Alias",
            source_name="seedfinder",
            source_id="testgetcanonicalheadband:breeder"
        )
        session.add(alias)
        await session.commit()

        try:
            # Test various name lookups
            # 1. Exact match
            assert await get_canonical_strain_name(session, "Test_GetCanonical_Head_Band") == "Test_GetCanonical_Head_Band"
            # 2. Case-insensitive match
            assert await get_canonical_strain_name(session, "test_getcanonical_head_band") == "Test_GetCanonical_Head_Band"
            # 3. Punctuation/spacing normalized match
            assert await get_canonical_strain_name(session, "TestGetCanonicalHeadBand") == "Test_GetCanonical_Head_Band"
            assert await get_canonical_strain_name(session, "test getcanonical head band") == "Test_GetCanonical_Head_Band"
            # 4. Alias match
            assert await get_canonical_strain_name(session, "TestGetCanonicalHeadband Alias") == "Test_GetCanonical_Head_Band"
            assert await get_canonical_strain_name(session, "testgetcanonicalheadbandalias") == "Test_GetCanonical_Head_Band"
            # 5. Non-existent strain
            assert await get_canonical_strain_name(session, "Non Existent Strain") is None
        finally:
            # Cleanup
            await session.delete(alias)
            await session.delete(strain)
            await session.delete(breeder)
            await session.commit()


async def test_load_state_from_db_with_placeholders():
    from main import load_state_from_db
    
    await init_db()
    
    async with get_session() as session:
        # Create a mock breeder
        breeder = BreederORM(
            name="Placeholder Test Breeder",
            website="https://example.com"
        )
        session.add(breeder)
        await session.flush()
        
        # Create a mock strain with no genomic samples
        strain = CanonicalStrainORM(
            primary_name="Test_Placeholder_NoSample_Strain",
            breeder_id=breeder.id,
            strain_type="hybrid"
        )
        session.add(strain)
        await session.commit()
        
        try:
            # Load state and verify it contains our mock strain
            state = await load_state_from_db(session)
            strains_data = state["strains_data"]
            
            assert "Test_Placeholder_NoSample_Strain" in strains_data
            data = strains_data["Test_Placeholder_NoSample_Strain"]
            assert data["complete"] is False
            assert data["rsp"] == "PLACEHOLDER-Test_Placeholder_NoSample_Strain"
            assert data["source"] == "forum"  # default when no aliases present
        finally:
            # Cleanup
            await session.delete(strain)
            await session.delete(breeder)
            await session.commit()

