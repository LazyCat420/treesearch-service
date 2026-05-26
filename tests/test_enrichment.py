import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.db import init_db, get_session
from src.models.orm import CanonicalStrainORM, GenomicSampleORM, ChemicalProfileORM, BreederORM, StrainAliasORM
from src.enrich_strains import enrich_all_strains

pytestmark = pytest.mark.asyncio

async def test_enrich_all_strains_terpenes_and_parent_placeholders():
    await init_db()

    # Create a unique test breeder and clean up old data
    async for session in get_session():
        from sqlalchemy import delete
        
        # Clean up any leftover test strains and parents first
        stmt_old = select(CanonicalStrainORM).where(
            CanonicalStrainORM.primary_name.in_([
                "Enrichment_Test_Strain",
                "Enrichment_Test_Parent_1",
                "Enrichment_Test_Parent_2"
            ])
        )
        old_strains = (await session.execute(stmt_old)).scalars().all()
        if old_strains:
            old_ids = [s.id for s in old_strains]
            await session.execute(delete(StrainAliasORM).where(StrainAliasORM.canonical_strain_id.in_(old_ids)))
            
            stmt_old_samples = select(GenomicSampleORM.id).where(GenomicSampleORM.canonical_strain_id.in_(old_ids))
            old_sample_ids = (await session.execute(stmt_old_samples)).scalars().all()
            if old_sample_ids:
                await session.execute(delete(ChemicalProfileORM).where(ChemicalProfileORM.sample_id.in_(old_sample_ids)))
                
            await session.execute(delete(GenomicSampleORM).where(GenomicSampleORM.canonical_strain_id.in_(old_ids)))
            for s in old_strains:
                await session.delete(s)
            await session.commit()

        # Check/create breeder
        stmt_br = select(BreederORM).where(BreederORM.name == "Enrichment Test Breeder")
        breeder = (await session.execute(stmt_br)).scalars().first()
        if not breeder:
            breeder = BreederORM(name="Enrichment Test Breeder")
            session.add(breeder)
            await session.flush()

        # Create a test strain with lineage referring to a non-existent parent
        strain = CanonicalStrainORM(
            primary_name="Enrichment_Test_Strain",
            breeder_id=breeder.id,
            strain_type="hybrid",
            lineage=[{"name": "Enrichment Test Parent 1"}, {"name": "Enrichment Test Parent 2"}]
        )
        session.add(strain)
        await session.commit()
        strain_id = strain.id
        breeder_id = breeder.id
        break

    # Mock ScraperClient and test enrichment
    mock_leafly_result = {
        "name": "Enrichment_Test_Strain",
        "slug": "enrichment-test-strain",
        "terpenes": {
            "caryophyllene": 0.5,
            "myrcene": 0.3,
            "limonene": 0.2
        }
    }

    async def mock_collect_leafly(strain_name):
        if strain_name == "Enrichment_Test_Strain":
            return mock_leafly_result
        return None

    # Use patch to mock ScraperClient collect_leafly and disable network requests for lineage lookup
    with patch("src.enrich_strains.ScraperClient.collect_leafly", new_callable=AsyncMock) as mock_leafly, \
         patch("src.collectors.seedfinder_collector.search_seedfinder", new_callable=AsyncMock, return_value=[]), \
         patch("main.fallback_search_genetics", new_callable=AsyncMock, return_value=[]):
        mock_leafly.side_effect = mock_collect_leafly
        async for session in get_session():
            await enrich_all_strains(session)
            await session.commit()
            break

    # Verify that:
    # 1. The strain's terpenes were enriched
    # 2. Lineage parent placeholders were auto-created
    async for session in get_session():
        # Check child strain
        stmt = select(CanonicalStrainORM).where(CanonicalStrainORM.id == strain_id).options(
            selectinload(CanonicalStrainORM.genomic_samples).selectinload(GenomicSampleORM.chemical_profile)
        )
        res = await session.execute(stmt)
        enriched_strain = res.scalar_one()

        assert enriched_strain.dominant_terpenes == ["caryophyllene", "myrcene", "limonene"]
        assert len(enriched_strain.genomic_samples) == 1
        assert enriched_strain.genomic_samples[0].source == "leafly"
        assert enriched_strain.genomic_samples[0].chemical_profile is not None
        assert enriched_strain.genomic_samples[0].chemical_profile.caryophyllene == 0.5

        # Check that parent placeholders were created
        stmt_p1 = select(CanonicalStrainORM).where(CanonicalStrainORM.primary_name == "Enrichment_Test_Parent_1").options(
            selectinload(CanonicalStrainORM.genomic_samples)
        )
        p1 = (await session.execute(stmt_p1)).scalars().first()
        assert p1 is not None
        assert p1.description == "Auto-generated lineage placeholder for Enrichment Test Parent 1."
        assert len(p1.genomic_samples) == 1
        assert p1.genomic_samples[0].rsp_number == "PLACEHOLDER-Enrichment_Test_Parent_1"
        assert p1.genomic_samples[0].source == "seedfinder"
        assert p1.genomic_samples[0].is_complete is False

        stmt_p2 = select(CanonicalStrainORM).where(CanonicalStrainORM.primary_name == "Enrichment_Test_Parent_2")
        p2 = (await session.execute(stmt_p2)).scalars().first()
        assert p2 is not None

        # Clean up
        from sqlalchemy import delete
        
        # Delete aliases
        await session.execute(delete(StrainAliasORM).where(StrainAliasORM.canonical_strain_id.in_([p1.id, p2.id, strain_id])))
        
        # Find sample IDs to delete chemical profiles
        stmt_samples = select(GenomicSampleORM.id).where(GenomicSampleORM.canonical_strain_id.in_([p1.id, p2.id, strain_id]))
        sample_ids = (await session.execute(stmt_samples)).scalars().all()
        if sample_ids:
            await session.execute(delete(ChemicalProfileORM).where(ChemicalProfileORM.sample_id.in_(sample_ids)))
            
        # Delete genomic samples
        await session.execute(delete(GenomicSampleORM).where(GenomicSampleORM.canonical_strain_id.in_([p1.id, p2.id, strain_id])))
        
        # Delete strains and breeder
        await session.delete(p1)
        await session.delete(p2)
        await session.delete(enriched_strain)
        await session.delete(breeder)
        await session.commit()
        break


async def test_dynamic_terpene_propagation_and_self_loops():
    from main import load_state_from_db_internal
    from src.db import get_session
    from sqlalchemy import delete
    
    # 1. Clean up potential old data
    async for session in get_session():
        stmt_old = select(CanonicalStrainORM).where(
            CanonicalStrainORM.primary_name.in_([
                "Test_Child_Strain",
                "Test_Parent_Strain",
                "Test_Self_Loop_Strain"
            ])
        )
        old_strains = (await session.execute(stmt_old)).scalars().all()
        if old_strains:
            old_ids = [s.id for s in old_strains]
            await session.execute(delete(StrainAliasORM).where(StrainAliasORM.canonical_strain_id.in_(old_ids)))
            stmt_old_samples = select(GenomicSampleORM.id).where(GenomicSampleORM.canonical_strain_id.in_(old_ids))
            old_sample_ids = (await session.execute(stmt_old_samples)).scalars().all()
            if old_sample_ids:
                await session.execute(delete(ChemicalProfileORM).where(ChemicalProfileORM.sample_id.in_(old_sample_ids)))
            await session.execute(delete(GenomicSampleORM).where(GenomicSampleORM.canonical_strain_id.in_(old_ids)))
            for s in old_strains:
                await session.delete(s)
            await session.commit()
            
    # 2. Insert test data:
    # - "Test_Parent_Strain" with a leafly sample containing terpenes.
    # - "Test_Child_Strain" with lineage pointing to "Test_Parent_Strain", but NO samples/terpenes.
    # - "Test_Self_Loop_Strain" with lineage pointing to "Test_Self_Loop_Strain" (casing variation), to verify self-loop prevention.
    async for session in get_session():
        parent = CanonicalStrainORM(
            primary_name="Test_Parent_Strain",
            lineage={}
        )
        session.add(parent)
        await session.flush()
        
        # Add a sample and chemical profile for parent
        parent_sample = GenomicSampleORM(
            canonical_strain_id=parent.id,
            rsp_number="LEAFLY-TEST_PARENT",
            strain_name="Test_Parent_Strain",
            source="leafly",
            is_complete=True
        )
        session.add(parent_sample)
        await session.flush()
        
        cp = ChemicalProfileORM(sample_id=parent_sample.id, myrcene=0.6, limonene=0.4)
        session.add(cp)
        
        child = CanonicalStrainORM(
            primary_name="Test_Child_Strain",
            lineage=[{"name": "Test Parent Strain"}]
        )
        session.add(child)
        
        # Self loop strain pointing to itself as parent
        self_loop = CanonicalStrainORM(
            primary_name="Test_Self_Loop_Strain",
            lineage=[{"name": "test_self_loop_strain"}]
        )
        session.add(self_loop)
        
        await session.commit()
        break
        
    # 3. Call load_state_from_db_internal and assert behavior
    async for session in get_session():
        state = await load_state_from_db_internal(session)
        
        strains_data = state["strains_data"]
        lineage_rels = state["lineage_relationships"]
        
        # Verify Test_Child_Strain has inherited terpenes from Test_Parent_Strain
        assert "Test_Child_Strain" in strains_data
        assert strains_data["Test_Child_Strain"].get("terpenes") is not None
        assert strains_data["Test_Child_Strain"]["terpenes"]["myrcene"] == 0.6
        assert strains_data["Test_Child_Strain"]["terpenes_inherited_from"] == "Test_Parent_Strain"
        
        # Verify lineage relationship: Test_Parent_Strain -> Test_Child_Strain
        parent_child_rels = [r for r in lineage_rels if r["from"] == "Test_Parent_Strain" and r["to"] == "Test_Child_Strain"]
        assert len(parent_child_rels) == 1
        
        # Verify self-loop prevention: Test_Self_Loop_Strain -> Test_Self_Loop_Strain is NOT created
        self_loop_rels = [r for r in lineage_rels if r["from"] == "Test_Self_Loop_Strain" or r["to"] == "Test_Self_Loop_Strain"]
        assert len(self_loop_rels) == 0
        
        # Clean up
        stmt_del = select(CanonicalStrainORM).where(
            CanonicalStrainORM.primary_name.in_([
                "Test_Child_Strain",
                "Test_Parent_Strain",
                "Test_Self_Loop_Strain"
            ])
        )
        strains_to_del = (await session.execute(stmt_del)).scalars().all()
        del_ids = [s.id for s in strains_to_del]
        await session.execute(delete(StrainAliasORM).where(StrainAliasORM.canonical_strain_id.in_(del_ids)))
        stmt_del_samples = select(GenomicSampleORM.id).where(GenomicSampleORM.canonical_strain_id.in_(del_ids))
        del_sample_ids = (await session.execute(stmt_del_samples)).scalars().all()
        if del_sample_ids:
            await session.execute(delete(ChemicalProfileORM).where(ChemicalProfileORM.sample_id.in_(del_sample_ids)))
        await session.execute(delete(GenomicSampleORM).where(GenomicSampleORM.canonical_strain_id.in_(del_ids)))
        for s in strains_to_del:
            await session.delete(s)
        await session.commit()
        break

