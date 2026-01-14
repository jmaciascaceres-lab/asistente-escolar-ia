import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env")

from app.db import init_db, get_db

def fix_schema():
    init_db()
    sql = """
    CREATE EXTENSION IF NOT EXISTS "vector";

    CREATE TABLE IF NOT EXISTS document_chunks (
      id          BIGSERIAL PRIMARY KEY,
      document_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
      chunk_index INT NOT NULL,
      content     TEXT NOT NULL,
      embedding   vector(384),
      metadata    JSONB DEFAULT '{}'::jsonb,
      created_at  TIMESTAMPTZ DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id
      ON document_chunks (document_id);
    """
    print("🔧 Creando tabla document_chunks...")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    print("-- Tabla creada exitosamente.")

if __name__ == "__main__":
    fix_schema()
