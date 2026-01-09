-- db/init.sql
-- Init schema for asistente-escolar-ia (pgvector + RAG + users + logs)
-- Target embedding dim: 384 (intfloat/multilingual-e5-small)

BEGIN;

-- Ensure public schema exists
CREATE SCHEMA IF NOT EXISTS public;

-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;

-- Enum type (roles)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
        CREATE TYPE user_role AS ENUM ('student','teacher','coordinator','caregiver');
    END IF;
END$$;

-- updated_at helper
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- -------------------------
-- Core tables
-- -------------------------

-- Documents (must include "source" as your ingest uses it)
CREATE TABLE IF NOT EXISTS documents (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    doc_type    TEXT,
    source      TEXT,
    subject     TEXT,
    summary     TEXT,
    year        INTEGER,
    grade_min   INTEGER,
    grade_max   INTEGER,
    url         TEXT,
    batch_tag   TEXT,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Document chunks (embedding dim = 384)
CREATE TABLE IF NOT EXISTS document_chunks (
    id          SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(384),
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_doc_chunk UNIQUE (document_id, chunk_index)
);

-- Users (Telegram identity + role)
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    telegram_id TEXT NOT NULL UNIQUE,
    role        user_role NOT NULL DEFAULT 'student',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Interaction logs (telemetry)
CREATE TABLE IF NOT EXISTS interaction_logs (
    id              SERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
    telegram_id     TEXT,
    role            user_role,
    command         TEXT,
    case_id         TEXT,
    prompt          TEXT,
    response        TEXT,
    llm_provider    TEXT,
    llm_model       TEXT,
    latency_ms      INTEGER,
    error_msg       TEXT,
    extra           JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- -------------------------
-- Triggers
-- -------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_documents_updated_at') THEN
        CREATE TRIGGER trg_documents_updated_at
        BEFORE UPDATE ON documents
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_document_chunks_updated_at') THEN
        CREATE TRIGGER trg_document_chunks_updated_at
        BEFORE UPDATE ON document_chunks
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_users_updated_at') THEN
        CREATE TRIGGER trg_users_updated_at
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
END$$;

-- -------------------------
-- Indexes
-- -------------------------

CREATE INDEX IF NOT EXISTS idx_documents_doc_type       ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_subject        ON documents(subject);
CREATE INDEX IF NOT EXISTS idx_chunks_document_id       ON document_chunks(document_id);

-- Vector index:
-- Prefer HNSW if available. If it fails for any reason, fall back to IVFFLAT.
DO $$
BEGIN
    BEGIN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
                 ON document_chunks USING hnsw (embedding vector_cosine_ops)';
    EXCEPTION WHEN others THEN
        RAISE NOTICE 'HNSW index not created (will try IVFFLAT). Error: %', SQLERRM;
        BEGIN
            EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_embedding_ivfflat
                     ON document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)';
        EXCEPTION WHEN others THEN
            RAISE NOTICE 'IVFFLAT index not created either. Error: %', SQLERRM;
        END;
    END;
END$$;

COMMIT;
