CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS decisions (
    materialfileid UUID PRIMARY KEY,
    court TEXT,
    courtdepartment TEXT,
    eclicode TEXT,
    casenumber TEXT,
    applicationnumber TEXT,
    processtype TEXT,
    processsubtype TEXT,
    materialtype TEXT,
    registrationdate DATE,
    status TEXT,
    courtinstance TEXT,
    downloadurl TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decision_documents (
    materialfileid UUID PRIMARY KEY REFERENCES decisions(materialfileid) ON DELETE CASCADE,
    content_type TEXT,
    file_name TEXT,
    file_size BIGINT,
    sha256 TEXT,
    local_path TEXT,
    download_status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    downloaded_at TIMESTAMPTZ,
    extracted_text TEXT,
    text_length INTEGER,
    search_vector TSVECTOR
);

CREATE INDEX IF NOT EXISTS idx_decisions_registrationdate ON decisions(registrationdate);
CREATE INDEX IF NOT EXISTS idx_decisions_court ON decisions(court);
CREATE INDEX IF NOT EXISTS idx_decisions_processtype ON decisions(processtype);
CREATE INDEX IF NOT EXISTS idx_decisions_materialtype ON decisions(materialtype);
CREATE INDEX IF NOT EXISTS idx_decisions_casenumber_trgm ON decisions USING gin(casenumber gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_decision_documents_search ON decision_documents USING gin(search_vector);

CREATE OR REPLACE FUNCTION update_decision_document_search_vector()
RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('simple', coalesce(NEW.extracted_text, ''));
    NEW.text_length := length(coalesce(NEW.extracted_text, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_decision_document_search_vector ON decision_documents;
CREATE TRIGGER trg_decision_document_search_vector
BEFORE INSERT OR UPDATE OF extracted_text
ON decision_documents
FOR EACH ROW
EXECUTE FUNCTION update_decision_document_search_vector();

CREATE TABLE IF NOT EXISTS decision_embeddings (
    id BIGSERIAL PRIMARY KEY,
    materialfileid UUID NOT NULL REFERENCES decisions(materialfileid) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(1024),
    model_name TEXT NOT NULL DEFAULT 'BAAI/bge-m3',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(materialfileid, chunk_index, model_name)
);

CREATE INDEX IF NOT EXISTS idx_decision_embeddings_materialfileid ON decision_embeddings(materialfileid);
CREATE INDEX IF NOT EXISTS idx_decision_embeddings_vector ON decision_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
