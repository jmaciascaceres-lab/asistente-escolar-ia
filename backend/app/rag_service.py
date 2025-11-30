from typing import List, Dict, Optional
import json

from .db import get_db


def ingest_document(
    title: str,
    doc_type: str,
    source: Optional[str] = None,
    subject: Optional[str] = None,
    year: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> int:
    """
    Inserta un documento en la tabla documents y devuelve su id.
    metadata puede incluir por ejemplo: {"snippet": "...", "tags": ["DUA", "Autismo"]}
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (title, doc_type, source, subject, year, metadata)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id;
                """,
                (title, doc_type, source, subject, year, json.dumps(metadata or {})),
            )
            row = cur.fetchone()
            return int(row[0])


def search_documents(
    query: str,
    filters: Optional[dict] = None,
    limit: int = 5,
) -> List[Dict]:
    """
    Búsqueda muy simple tipo RAG v0:
    - Busca query en title y en metadata::text (donde puedes guardar resúmenes, palabras clave, etc.)
    - Aplica filtros por doc_type y subject si se entregan.
    """
    filters = filters or {}
    sql = """
        SELECT id, title, doc_type, source, subject, year, metadata
        FROM documents
        WHERE (title ILIKE %s OR metadata::text ILIKE %s)
    """
    params = [f"%{query}%", f"%{query}%"]

    if "doc_type" in filters:
        sql += " AND doc_type = %s"
        params.append(filters["doc_type"])

    if "subject" in filters:
        sql += " AND subject = %s"
        params.append(filters["subject"])

    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)

    results: List[Dict] = []
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for row in cur.fetchall():
                results.append(
                    {
                        "id": row[0],
                        "title": row[1],
                        "doc_type": row[2],
                        "source": row[3],
                        "subject": row[4],
                        "year": row[5],
                        "metadata": row[6] or {},
                    }
                )
    return results
