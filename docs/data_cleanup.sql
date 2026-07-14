-- =============================================================================
-- treesearch-service — one-off data cleanup
--
-- NOT RUN AUTOMATICALLY. Review, then execute yourself.
--
-- The code fixes stop NEW bad data from being written. This removes the bad data
-- already in the tables. It runs against the SHARED trading_bot database, so read
-- each statement before you run it.
--
--   psql "postgresql://trader:<PASSWORD>@10.0.0.16:5433/trading_bot" -f data_cleanup.sql
--
-- Everything here is re-derivable: the strain tables are scraped data, so the
-- worst case for any DELETE is that a re-import re-fetches it.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 0. Delete TEST-FIXTURE terpene data that was written into production.
--
-- This is the worst of the lot. The test suite ran against the live trading_bot database
-- (tests/test_db.py even asserted the host was 10.0.0.16), and
-- test_enrich_all_strains_terpenes_and_parent_placeholders calls enrich_all_strains()
-- over EVERY strain in the database with a mocked Leafly response.
--
-- The result: 160 real strains — Black_Jack, Headband_BX, JL x MC7, Afghanistan… — carry
-- the test's mock profile (caryophyllene 0.5, myrcene 0.3, limonene 0.2) as if it were
-- measured data. They are identifiable beyond doubt: their raw_payload still says
--     {"name": "Enrichment_Test_Strain", "slug": "enrichment-test-strain", ...}
--
-- That is 160 of the 321 profiles that have any terpene value at all — half the terpene
-- data in the warehouse is a unit-test fixture, and because every copy is identical, a
-- quarter of all strain pairs scored as "perfectly correlated" on it.
--
-- The tests can no longer do this: conftest.py now refuses to run against trading_bot and
-- points at a throwaway database.
-- -----------------------------------------------------------------------------

CREATE TEMP TABLE _fixture_samples AS
SELECT id FROM genomic_samples
WHERE raw_payload->>'name' = 'Enrichment_Test_Strain';

DELETE FROM chemical_profiles       WHERE sample_id         IN (SELECT id FROM _fixture_samples);
DELETE FROM source_genomics_records WHERE genomic_sample_id IN (SELECT id FROM _fixture_samples);
DELETE FROM genetic_relationships   WHERE sample_id_a       IN (SELECT id FROM _fixture_samples);
DELETE FROM genomic_samples         WHERE id                IN (SELECT id FROM _fixture_samples);

-- Also drop the strain the test itself created, if it is still there.
DELETE FROM strain_aliases    WHERE canonical_strain_id IN (
    SELECT id FROM canonical_strains WHERE primary_name = 'Enrichment_Test_Strain');
DELETE FROM canonical_strains WHERE primary_name = 'Enrichment_Test_Strain';


-- -----------------------------------------------------------------------------
-- 1. Delete fabricated terpene profiles.
--
-- When the web-search fallback found no real percentages, it counted how often each
-- terpene was *mentioned* in a search snippet and wrote `(count / total) * 1.5` as the
-- value. Those numbers are invented. They were stored in the same columns as real lab
-- assays with source='leafly_fallback', so nothing downstream could tell them apart.
--
-- The parser now returns {} instead of guessing, so no new rows like this appear.
-- Check what you are about to remove:
--     SELECT COUNT(*) FROM genomic_samples WHERE source = 'leafly_fallback';
-- -----------------------------------------------------------------------------

DELETE FROM chemical_profiles
WHERE sample_id IN (
    SELECT id FROM genomic_samples WHERE source = 'leafly_fallback'
);

DELETE FROM source_genomics_records
WHERE genomic_sample_id IN (
    SELECT id FROM genomic_samples WHERE source = 'leafly_fallback'
);

DELETE FROM genetic_relationships
WHERE sample_id_a IN (
    SELECT id FROM genomic_samples WHERE source = 'leafly_fallback'
);

DELETE FROM genomic_samples WHERE source = 'leafly_fallback';


-- -----------------------------------------------------------------------------
-- 2. Stop Leafly relative scores being treated as lab measurements.
--
-- Leafly reports RELATIVE terpene prominence, not a mass percentage, and carries no
-- cannabinoid panel — but the samples were flagged is_complete=true. /api/terpene-heatmap
-- filters on is_complete, so it was plotting Leafly's relative scores on the same axis as
-- Kannapedia's lab percentages, as if they were the same unit.
--
-- The code now writes is_complete=false for these. Backfill the existing rows.
-- -----------------------------------------------------------------------------

UPDATE genomic_samples
SET is_complete = false
WHERE source = 'leafly';


-- -----------------------------------------------------------------------------
-- 3. Purge poisoned image embeddings.
--
-- Pillow was imported but missing from requirements.txt, so in every deployed container
-- the import failed, the feature extractor fell through to get_fallback_features(), and
-- that returns a SHA-256 hash OF THE URL STRING — not of the image. Those vectors carry
-- no visual signal at all, so any clustering built on them is meaningless.
--
-- Pillow is now a declared dependency and the import failure is fatal rather than silent.
-- Null these out and re-run POST /api/ml/cluster to rebuild from real image features.
-- -----------------------------------------------------------------------------

UPDATE observation_images
SET embedding = NULL,
    cluster_id = NULL;


-- -----------------------------------------------------------------------------
-- 4. Remove garbage strains scraped out of lineage prose.
--
-- The lineage parser used to accept any capitalised words either side of an "x", and
-- SeedFinder's raw ancestry text was ingested verbatim. The result: 38 of 393 canonical
-- strains are not strains at all. They are sentence fragments and page furniture —
-- "Grundlegende_Informationen_Grape_Smash" (a German section heading),
-- "Blueberry_Jacks_Cleaner_»»»_(Pluton" (the »»» lineage separator, inlined),
-- "Medellin_Flowering_Time", "Lemon_Roze_Gallery", "Granddaddy_Purple_heritage".
--
-- They pollute search results and the graph. All 38 have zero forum observations.
--
-- The parser now rejects these, so no new ones appear. REVIEW the SELECT first, then run
-- the DELETE. Adjust the pattern if it catches a strain you want to keep.
-- -----------------------------------------------------------------------------

-- Step 4a — LOOK at what matches before deleting anything:
--
--   SELECT cs.primary_name,
--          (SELECT count(*) FROM observations o WHERE o.canonical_strain_id = cs.id) AS posts
--   FROM canonical_strains cs
--   WHERE cs.primary_name ~ '»»»|^\(|\.\.|\[|specified_above|Grundlegende|_ist_|Flowering_Time|_Lineage$|_Gallery$|_heritage$|_strains$'
--   ORDER BY 1;

-- Step 4b — delete them and everything hanging off them. Guarded on posts = 0 so a strain
-- that has real community data is never removed, whatever its name looks like.
CREATE TEMP TABLE _garbage_strains AS
SELECT cs.id
FROM canonical_strains cs
WHERE cs.primary_name ~ '»»»|^\(|\.\.|\[|specified_above|Grundlegende|_ist_|Flowering_Time|_Lineage$|_Gallery$|_heritage$|_strains$'
  AND NOT EXISTS (SELECT 1 FROM observations o WHERE o.canonical_strain_id = cs.id);

DELETE FROM chemical_profiles WHERE sample_id IN (
    SELECT gs.id FROM genomic_samples gs JOIN _garbage_strains g ON g.id = gs.canonical_strain_id);
DELETE FROM source_genomics_records WHERE genomic_sample_id IN (
    SELECT gs.id FROM genomic_samples gs JOIN _garbage_strains g ON g.id = gs.canonical_strain_id);
DELETE FROM genetic_relationships WHERE sample_id_a IN (
    SELECT gs.id FROM genomic_samples gs JOIN _garbage_strains g ON g.id = gs.canonical_strain_id);
DELETE FROM genomic_samples WHERE canonical_strain_id IN (SELECT id FROM _garbage_strains);
DELETE FROM strain_aliases   WHERE canonical_strain_id IN (SELECT id FROM _garbage_strains);
DELETE FROM canonical_strains WHERE id IN (SELECT id FROM _garbage_strains);


-- -----------------------------------------------------------------------------
-- 4c. Duplicate genomic samples.
--
-- Some strains hold the same Kannapedia RSP more than once (Blue_Dream has RSP11342,
-- RSP11227 and RSP11033 twice each). Each copy becomes its own edge, so the graph shows
-- the same neighbour several times at slightly different distances.
--
-- Keep the oldest sample per (canonical_strain_id, rsp_number).
-- -----------------------------------------------------------------------------

CREATE TEMP TABLE _dupe_samples AS
SELECT id FROM (
    SELECT gs.id,
           row_number() OVER (
               PARTITION BY gs.canonical_strain_id, gs.rsp_number
               ORDER BY gs.created_at NULLS LAST, gs.id
           ) AS rn
    FROM genomic_samples gs
    WHERE gs.canonical_strain_id IS NOT NULL AND gs.rsp_number <> ''
) x WHERE rn > 1;

DELETE FROM chemical_profiles        WHERE sample_id         IN (SELECT id FROM _dupe_samples);
DELETE FROM source_genomics_records  WHERE genomic_sample_id IN (SELECT id FROM _dupe_samples);
DELETE FROM genetic_relationships    WHERE sample_id_a       IN (SELECT id FROM _dupe_samples);
DELETE FROM genomic_samples          WHERE id                IN (SELECT id FROM _dupe_samples);


-- -----------------------------------------------------------------------------
-- 5. Drop the dead source_strain_records table.
--
-- Nothing has ever inserted into it. merge_duplicates.py ran an UPDATE against it that
-- always affected zero rows. The ORM model has been removed.
-- -----------------------------------------------------------------------------

DROP TABLE IF EXISTS source_strain_records;


-- -----------------------------------------------------------------------------
-- 6. Deduplicate forum posts, then enforce uniqueness per forum.
--
-- Post ids are only unique WITHIN a forum, but the dedupe check matched on source_id
-- alone — so an Overgrow topic id of 4211 permanently blocked Rollitup's post 4211 from
-- ever being saved, while re-imports of the same forum left true duplicates behind
-- (196 of them).
--
-- Keep the oldest row of each group and re-parent its siblings' images onto it, so no
-- photo is ever lost to deduplication.
--
-- init_db() performs exactly this on startup, so a restart alone is enough; it is
-- repeated here so the script is self-contained.
-- -----------------------------------------------------------------------------

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

CREATE UNIQUE INDEX IF NOT EXISTS uq_observations_source
    ON observations (source_name, source_id);


COMMIT;

-- After committing:
--   1. Restart treesearch-service (it will pick up the new index and the Pillow dependency).
--   2. POST /api/ml/cluster   to rebuild image clusters from real embeddings.
--   3. POST /api/strains/enrich  to re-fetch terpene data for the strains cleared in step 1.
