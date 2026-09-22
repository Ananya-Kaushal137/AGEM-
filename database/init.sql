-- Base schema if needed outside Alembic migrations (Architecture §25).
-- The nine entities in Architecture §14.1 are created by `alembic upgrade head`;
-- this file only holds what must exist before migrations run.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
