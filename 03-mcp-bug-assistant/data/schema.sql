-- Ticket database schema for the MCP Bug Assistant.
--
-- Works on Cloud SQL for PostgreSQL, AlloyDB, or plain Postgres 15+ with
-- pgvector. The `embedding` column is what makes semantic duplicate detection
-- possible: a report worded completely differently from an existing ticket
-- still matches it.
--
-- Apply with:
--   psql "$DATABASE_URL" -f data/schema.sql

-- pgvector gives us the vector type and the `<=>` cosine-distance operator.
CREATE EXTENSION IF NOT EXISTS vector;

-- google_ml_integration provides embedding() on Cloud SQL and AlloyDB. It does
-- not exist on vanilla Postgres; see data/schema_local.sql for that case.
CREATE EXTENSION IF NOT EXISTS google_ml_integration CASCADE;

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id     SERIAL PRIMARY KEY,
    title         VARCHAR(255) NOT NULL,
    description   TEXT NOT NULL,
    assignee      VARCHAR(255),
    priority      VARCHAR(50)  NOT NULL DEFAULT 'P3 - Low',
    status        VARCHAR(50)  NOT NULL DEFAULT 'Open',
    creation_time TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_time  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Constrain the enum-like columns in the database, not only in the prompt.
    -- A model that drifts to "High" instead of "P1 - High" then gets a clear
    -- error rather than writing a value no query will ever match.
    CONSTRAINT tickets_priority_check
        CHECK (priority IN ('P0 - Critical', 'P1 - High', 'P2 - Medium', 'P3 - Low')),
    CONSTRAINT tickets_status_check
        CHECK (status IN ('Open', 'In Progress', 'Blocked', 'Resolved', 'Closed'))
);

-- 768 dimensions matches text-embedding-005.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS embedding vector(768);

-- Keep updated_time honest without relying on every writer to set it.
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

-- Indexes for the access patterns the Toolbox tools actually use.
CREATE INDEX IF NOT EXISTS tickets_status_idx   ON tickets (status);
CREATE INDEX IF NOT EXISTS tickets_priority_idx ON tickets (priority);
CREATE INDEX IF NOT EXISTS tickets_assignee_idx ON tickets (assignee);
CREATE INDEX IF NOT EXISTS tickets_created_idx  ON tickets (creation_time DESC);

-- Trigram index for the ILIKE keyword fallback used when embeddings are absent.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS tickets_title_trgm_idx
    ON tickets USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS tickets_description_trgm_idx
    ON tickets USING gin (description gin_trgm_ops);

-- Populate embeddings for any row missing one. Re-run after inserting tickets
-- outside the agent; the vector search silently skips NULL embeddings.
--   UPDATE tickets
--      SET embedding = embedding('text-embedding-005', title || ' ' || description)::vector
--    WHERE embedding IS NULL;

-- An ANN index is only worth building once there is real data in the table;
-- below a few thousand rows a sequential scan is faster. When you do need it:
--   CREATE INDEX tickets_embedding_idx ON tickets
--     USING hnsw (embedding vector_cosine_ops);
