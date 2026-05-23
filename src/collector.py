import asyncio
from src.db import init_db, get_session
from src.models.orm import BreederORM, CanonicalStrainORM

async def save_strain_data(strain_data: dict):
    """
    Saves the dictionary structure returned by Kannapedia scraper to the Postgres database.
    """
    async for session in get_session():
        # First check if the grower/breeder exists or create one
        grower_name = strain_data.get('general_info', {}).get('Grower', 'Unknown Breeder')
        
        breeder = BreederORM(
            name=grower_name,
        )
        session.add(breeder)
        await session.flush()
        
        # Parse cannabinoids and terpenoids
        cannabinoids = strain_data.get('chemical_content', {}).get('cannabinoids', {})
        terpenoids = strain_data.get('chemical_content', {}).get('terpenoids', {})
        
        avg_thc_pct = None
        if 'THC' in cannabinoids:
            try:
                avg_thc_pct = float(cannabinoids['THC'].replace('%', '').strip())
            except ValueError:
                pass
                
        avg_cbd_pct = None
        if 'CBD' in cannabinoids:
            try:
                avg_cbd_pct = float(cannabinoids['CBD'].replace('%', '').strip())
            except ValueError:
                pass

        dominant_terps = list(terpenoids.keys())

        # Save the strain
        strain = CanonicalStrainORM(
            primary_name=strain_data.get('name', 'Unknown Strain'),
            breeder_id=breeder.id,
            avg_thc_pct=avg_thc_pct,
            avg_cbd_pct=avg_cbd_pct,
            dominant_terpenes=dominant_terps,
            description=f"Kannapedia RSP data for {strain_data.get('name')}"
        )
        session.add(strain)
        await session.commit()
        return strain.id
