-- Runs once, on first Postgres container start, before the application.
--
-- The nine entities of Architecture §14.1 are NOT created here. As of the first
-- migration, the schema is owned by Alembic:
--
--     alembic upgrade head
--
-- Architecture §14.2 and FR-DAT-006 are explicit that every schema change from
-- that point goes through a migration — so nothing is added to this file.
-- It exists only for what must be true before migrations can run.

-- Primary keys are application-generated UUIDs (FR-DAT-001), so no UUID
-- extension is required. This is here for gen_random_uuid() in ad-hoc queries
-- and manual demo fixups only.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
