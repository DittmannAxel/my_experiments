-- Bootstrap the rag database, role, and pgvector schema.

CREATE EXTENSION IF NOT EXISTS vector;

DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rag') THEN
        CREATE ROLE rag LOGIN PASSWORD 'rag';
    END IF;
END $$;

CREATE DATABASE rag OWNER rag;

\c rag

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS spec_chunks (
    id        BIGSERIAL PRIMARY KEY,
    part      TEXT      NOT NULL,
    chunk_id  TEXT      NOT NULL,
    title     TEXT,
    content   TEXT      NOT NULL,
    embedding vector(1024),
    UNIQUE (part, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_spec_chunks_embedding
    ON spec_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_spec_chunks_part
    ON spec_chunks (part);

GRANT ALL ON SCHEMA public TO rag;
GRANT ALL ON ALL TABLES IN SCHEMA public TO rag;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO rag;
