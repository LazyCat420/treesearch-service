import pytest
import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from main import app
from src.db import init_db, get_session
from src.models.orm import (
    CanonicalStrainORM,
    GenomicSampleORM,
    ObservationORM,
    ObservationImageORM,
    StrainAliasORM,
    SourceGenomicsRecordORM,
)
from src.ml.clustering import run_image_clustering

pytestmark = pytest.mark.asyncio

async def test_ingest_clustering_and_detail_api():
    # 1. Initialize tables
    await init_db()
    
    # 2. Mock a Kannapedia scraper payload and post to the ingest endpoint
    kannapedia_payload = {
        "name": "Jack Herer",
        "general_info": {
            "Grower": "Sensi Seeds",
            "Accession Date": "2026-05-20",
            "Plant Type": "Sativa-dominant",
            "Ref Number": "RSP420"
        },
        "chemical_content": {
            "cannabinoids": {
                "THC": "21.0%",
                "CBD": "0.5%"
            },
            "terpenoids": {
                "Terpinolene": "0.9%",
                "Caryophyllene": "0.4%",
                "Myrcene": "0.3%"
            }
        },
        "genetic_relationships": {
            "all_samples": [
                {"strain": "Northern Lights", "rsp": "RSP100", "distance": 0.15},
                {"strain": "Shiva Skunk", "rsp": "RSP200", "distance": 0.22}
            ]
        }
    }
    
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/ingest/kannapedia", json=kannapedia_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["strain_name"] == "Jack Herer"
        assert data["rsp"] == "RSP420"
        
        # 3. Add mock forum observation with images linked to Jack Herer
        async for session in get_session():
            # Find canonical strain
            stmt = select(CanonicalStrainORM).where(CanonicalStrainORM.id == data["strain_id"])
            strain = (await session.execute(stmt)).scalars().first()
            assert strain is not None
            
            # Add observation
            obs = ObservationORM(
                canonical_strain_id=strain.id,
                reported_strain_name="Jack Herer",
                source_name="overgrow",
                source_url="https://overgrow.com/t/jack-herer/123",
                author="lazycat420",
                raw_text="Jack Herer has an amazing spicy pine smell and strong sativa effects.",
            )
            session.add(obs)
            await session.flush()
            
            # Add images
            img1 = ObservationImageORM(
                observation_id=obs.id,
                image_url="https://example.com/jack1.jpg",
                local_path="jack1.jpg",
            )
            img2 = ObservationImageORM(
                observation_id=obs.id,
                image_url="https://example.com/jack2.jpg",
                local_path="jack2.jpg",
            )
            session.add(img1)
            session.add(img2)
            await session.commit()
            break
            
        # 4. Trigger ML clustering
        async for session in get_session():
            clustered_count = await run_image_clustering(session)
            assert clustered_count >= 2, "Both images should be clustered"
            
            # Verify cluster_id is populated
            stmt_img = select(ObservationImageORM).where(ObservationImageORM.image_url == "https://example.com/jack1.jpg")
            img = (await session.execute(stmt_img)).scalars().first()
            assert img.cluster_id is not None
            break
            
        # 5. Fetch strain details via API
        resp_detail = await client.get("/api/strains/Jack_Herer/detail")
        assert resp_detail.status_code == 200
        detail = resp_detail.json()
        assert detail["name"] in ("Jack Herer", "Jack_Herer")
        if detail.get("rsp"):
            assert detail["rsp"] == "RSP420"
            assert detail["total_thc"] is not None
        
        # Assert observations and clustered images are returned
        assert len(detail["observations"]) >= 1
        obs_data = next((o for o in detail["observations"] if o["source_url"] == "https://overgrow.com/t/jack-herer/123"), None)
        assert obs_data is not None
        assert obs_data["author"] == "lazycat420"
        assert obs_data["source_name"] == "overgrow"
        assert len(obs_data["images"]) == 2
        assert obs_data["images"][0]["cluster_id"] is not None
        
        # Cleanup test data
        async for session in get_session():
            stmt_sample = select(GenomicSampleORM).where(GenomicSampleORM.rsp_number == "RSP420").options(
                selectinload(GenomicSampleORM.chemical_profile),
                selectinload(GenomicSampleORM.genetic_relationships)
            )
            sample = (await session.execute(stmt_sample)).scalars().first()
            if sample:
                if sample.chemical_profile:
                    await session.delete(sample.chemical_profile)
                for rel in sample.genetic_relationships:
                    await session.delete(rel)
                
                # Delete source genomics records linked to the sample
                stmt_src = select(SourceGenomicsRecordORM).where(SourceGenomicsRecordORM.genomic_sample_id == sample.id)
                src_records = (await session.execute(stmt_src)).scalars().all()
                for src in src_records:
                    await session.delete(src)
                    
                stmt_obs = select(ObservationORM).where(ObservationORM.reported_strain_name == "Jack Herer")
                observations = (await session.execute(stmt_obs)).scalars().all()
                for o in observations:
                    stmt_imgs = select(ObservationImageORM).where(ObservationImageORM.observation_id == o.id)
                    imgs = (await session.execute(stmt_imgs)).scalars().all()
                    for img in imgs:
                        await session.delete(img)
                    await session.delete(o)
                    
                await session.delete(sample)
                
            stmt_strain = select(CanonicalStrainORM).where(CanonicalStrainORM.id == data["strain_id"]).options(
                selectinload(CanonicalStrainORM.aliases)
            )
            strain = (await session.execute(stmt_strain)).scalars().first()
            if strain:
                for alias in list(strain.aliases):
                    if alias.source_id == "RSP420":
                        await session.delete(alias)
                if strain.primary_name == "Jack Herer":
                    await session.delete(strain)
                
            await session.commit()
            break
