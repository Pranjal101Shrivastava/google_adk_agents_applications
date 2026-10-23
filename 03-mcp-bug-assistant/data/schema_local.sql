-- Local-development variant of the ticket schema.
--
-- Plain Postgres has pgvector (via the pgvector/pgvector image) but not
-- google_ml_integration, so there is no in-database embedding() function. This
-- file creates the same table without it, and the Toolbox `search-tickets`
-- tool falls back to keyword search. Everything else behaves identically.
--
--   psql "postgresql://postgres:admin@127.0.0.1:5432/ticketsdb" -f data/schema_local.sql

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id     SERIAL PRIMARY KEY,
    title         VARCHAR(255) NOT NULL,
    description   TEXT NOT NULL,
    assignee      VARCHAR(255),
    priority      VARCHAR(50)  NOT NULL DEFAULT 'P3 - Low',
    status        VARCHAR(50)  NOT NULL DEFAULT 'Open',
    creation_time TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_time  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    embedding     vector(768),

    CONSTRAINT tickets_priority_check
        CHECK (priority IN ('P0 - Critical', 'P1 - High', 'P2 - Medium', 'P3 - Low')),
    CONSTRAINT tickets_status_check
        CHECK (status IN ('Open', 'In Progress', 'Blocked', 'Resolved', 'Closed'))
);

CREATE OR REPLACE FUNCTION tickets_touch_updated_time()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_time = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tickets_set_updated_time ON tickets;
CREATE TRIGGER tickets_set_updated_time
    BEFORE UPDATE ON tickets
    FOR EACH ROW
    EXECUTE FUNCTION tickets_touch_updated_time();

CREATE INDEX IF NOT EXISTS tickets_status_idx   ON tickets (status);
CREATE INDEX IF NOT EXISTS tickets_priority_idx ON tickets (priority);
CREATE INDEX IF NOT EXISTS tickets_assignee_idx ON tickets (assignee);
CREATE INDEX IF NOT EXISTS tickets_created_idx  ON tickets (creation_time DESC);
CREATE INDEX IF NOT EXISTS tickets_title_trgm_idx
    ON tickets USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS tickets_description_trgm_idx
    ON tickets USING gin (description gin_trgm_ops);
