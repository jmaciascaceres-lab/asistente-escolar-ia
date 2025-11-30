from typing import List, Dict, Optional
from psycopg2.extras import Json
from .db import get_db
from .embeddings import embed_texts, vector_to_str


# -------------- Nivel documento (como antes) --------------


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
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (title, doc_type, source, subject, year, Json(metadata or {})),
            )
            row = cur.fetchone()
            return int(row[0])


def search_documents(
    query: str,
    filters: Optional[dict] = None,
    limit: int = 5,
) -> List[Dict]:
    """
    Búsqueda v0 por título/metadata (no vectorial).
    Sigue siendo útil para listar fuentes relevantes.
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


# -------------- Nivel chunk (RAG “real”) --------------


def chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> List[str]:
    """
    Chunking muy simple por caracteres (se puede refinar luego por párrafos/oraciones).
    """
    text = text.replace("\r", " ")
    chunks: List[str] = []
    start = 0
    n = len(text)

    while start < n:
        end = min(start + max_chars, n)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        
        if end == n:
            break

        start = end - overlap  # solapamiento
        if start < 0:
            start = 0

    return chunks


def ingest_document_with_text(
    title: str,
    doc_type: str,
    full_text: str,
    source: Optional[str] = None,
    subject: Optional[str] = None,
    year: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> int:
    """
    Crea un registro en documents y sus correspondientes chunks en document_chunks,
    con embeddings listos para búsqueda semántica.
    """
    doc_id = ingest_document(title, doc_type, source, subject, year, metadata)

    chunks = chunk_text(full_text)
    if not chunks:
        return doc_id

    vectors = embed_texts(chunks)

    with get_db() as conn:
        with conn.cursor() as cur:
            for idx, (chunk, vec) in enumerate(zip(chunks, vectors)):
                vec_str = vector_to_str(vec)
                cur.execute(
                    """
                    INSERT INTO document_chunks (document_id, chunk_index, content, embedding)
                    VALUES (%s, %s, %s, %s::vector);
                    """,
                    (doc_id, idx, chunk, vec_str),
                )

    return doc_id


def search_snippets(
    query: str,
    filters: Optional[dict] = None,
    k: int = 5,
) -> List[Dict]:
    """
    Búsqueda semántica sobre document_chunks (RAG real).
    Devuelve contenido + datos del documento.
    """
    filters = filters or {}
    vectors = embed_texts([query])
    query_vec_str = vector_to_str(vectors[0])

    sql = """
        SELECT
            dc.id,
            dc.content,
            dc.metadata,
            d.id AS document_id,
            d.title,
            d.doc_type,
            d.source,
            d.subject,
            d.year,
            (dc.embedding <-> %s::vector) AS distance
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        WHERE TRUE
    """
    params = [query_vec_str]

    if "doc_type" in filters:
        sql += " AND d.doc_type = %s"
        params.append(filters["doc_type"])

    if "subject" in filters:
        sql += " AND d.subject = %s"
        params.append(filters["subject"])

    sql += " ORDER BY distance ASC LIMIT %s"
    params.append(k)

    results: List[Dict] = []
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for row in cur.fetchall():
                results.append(
                    {
                        "chunk_id": row[0],
                        "content": row[1],
                        "chunk_metadata": row[2] or {},
                        "document_id": row[3],
                        "title": row[4],
                        "doc_type": row[5],
                        "source": row[6],
                        "subject": row[7],
                        "year": row[8],
                        "distance": float(row[9]),
                    }
                )

    return results
