CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS pages (
  id SERIAL PRIMARY KEY,
  content TEXT NOT NULL,
  scope_id TEXT,
  memory_type TEXT NOT NULL DEFAULT 'conversation',
  importance REAL NOT NULL DEFAULT 0.5,
  confidence REAL NOT NULL DEFAULT 0.8,
  frequency INT NOT NULL DEFAULT 1,
  sentiment REAL NOT NULL DEFAULT 0.0,
  source TEXT NOT NULL DEFAULT 'capture',
  ttl_days INT,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW(),
  last_used TIMESTAMP DEFAULT NOW(),
  last_retrieved TIMESTAMP,
  is_archived BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS pages_archive (
  archive_id SERIAL PRIMARY KEY,
  archive_batch_id TEXT,
  page_id INT,
  content TEXT NOT NULL,
  scope_id TEXT,
  memory_type TEXT,
  importance REAL,
  confidence REAL,
  frequency INT,
  sentiment REAL,
  source TEXT,
  ttl_days INT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  last_used TIMESTAMP,
  last_retrieved TIMESTAMP,
  archived_at TIMESTAMP DEFAULT NOW(),
  archive_reason TEXT DEFAULT 'decay'
);

CREATE TABLE IF NOT EXISTS embeddings (
  id SERIAL PRIMARY KEY,
  page_id INT REFERENCES pages(id) ON DELETE CASCADE,
  embedding vector(768),
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tags (
  id SERIAL PRIMARY KEY,
  page_id INT REFERENCES pages(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pages_memory_type ON pages(memory_type);
CREATE INDEX IF NOT EXISTS idx_pages_scope_id ON pages(scope_id);
CREATE INDEX IF NOT EXISTS idx_pages_last_used ON pages(last_used DESC);
CREATE INDEX IF NOT EXISTS idx_pages_archived ON pages(is_archived);
CREATE INDEX IF NOT EXISTS idx_pages_fts_content ON pages USING GIN (to_tsvector('english', content));
CREATE INDEX IF NOT EXISTS idx_pages_archive_batch ON pages_archive(archive_batch_id);
