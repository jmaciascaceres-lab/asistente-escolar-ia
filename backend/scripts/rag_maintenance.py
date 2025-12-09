# backend/scripts/rag_maintenance.py

import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv

# -------------------------------------------------------------------
# CONFIGURACIÓN DE ENTORNO E IMPORTACIONES
# -------------------------------------------------------------------
# Hacemos disponible app.* para poder importar app.db
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

# Cargar variables de entorno desde .env (para local development)
load_dotenv(ROOT_DIR / ".env")

# Importar lógica de conexión compartida
from app.db import init_db, close_db, get_db  # noqa: E402


# -------------------------------------------------------------------
# LISTAR DOCUMENTOS
# -------------------------------------------------------------------

def cmd_list(args):
    """
    Lista documentos con algunos filtros opcionales:
    - por doc_type
    - por batch_tag (metadata->>'batch_tag')
    - por substring del filename (metadata->>'raw_filename')
    """
    where_clauses = []
    params = []

    if args.doc_type:
        where_clauses.append("doc_type = %s")
        params.append(args.doc_type)

    if args.batch_tag:
        where_clauses.append("metadata->>'batch_tag' = %s")
        params.append(args.batch_tag)

    if args.filename_substr:
        where_clauses.append("metadata->>'raw_filename' ILIKE %s")
        params.append(f"%{args.filename_substr}%")

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    id,
                    title,
                    doc_type,
                    COALESCE(source, '') AS source,
                    year,
                    metadata->>'raw_filename' AS filename,
                    metadata->>'batch_tag' AS batch_tag
                FROM documents
                {where_sql}
                ORDER BY id
                LIMIT %s;
                """,
                (*params, args.limit),
            )

            rows = cur.fetchall()
            if not rows:
                print("No se encontraron documentos con esos filtros.")
                return

            print("id | doc_type | year | batch_tag | filename | title")
            print("-" * 80)
            for r in rows:
                doc_id, title, doc_type, source, year, filename, batch_tag = r
                print(
                    f"{doc_id} | {doc_type or '-'} | {year or '-'} | "
                    f"{batch_tag or '-'} | {filename or '-'} | {title}"
                )


# -------------------------------------------------------------------
# BORRAR POR ID
# -------------------------------------------------------------------

def cmd_delete_id(args):
    """
    Borra un documento por id, incluyendo sus chunks.
    """
    doc_id = args.id
    if not args.force:
        confirm = input(
            f"Vas a BORRAR el documento id={doc_id} y sus chunks. "
            "Escribe 'SI' para confirmar: "
        )
        if confirm.strip().upper() != "SI":
            print("Operación cancelada.")
            return

    with get_db() as conn:
        with conn.cursor() as cur:
            # Primero borramos los chunks
            cur.execute(
                "DELETE FROM document_chunks WHERE document_id = %s;",
                (doc_id,),
            )
            # Luego el documento
            cur.execute(
                "DELETE FROM documents WHERE id = %s;",
                (doc_id,),
            )
    
    print(f"Documento {doc_id} y sus chunks asociados fueron eliminados.")


# -------------------------------------------------------------------
# BORRAR POR batch_tag
# -------------------------------------------------------------------

def cmd_delete_batch(args):
    """
    Borra TODOS los documentos que tengan metadata->>'batch_tag' = batch_tag
    (y sus chunks).
    """
    batch_tag = args.batch_tag
    if not args.force:
        confirm = input(
            f"Vas a BORRAR TODOS los documentos con batch_tag='{batch_tag}'. "
            "Escribe 'SI' para confirmar: "
        )
        if confirm.strip().upper() != "SI":
            print("Operación cancelada.")
            return

    with get_db() as conn:
        with conn.cursor() as cur:
            # Borramos chunks de esos documentos
            cur.execute(
                """
                DELETE FROM document_chunks
                WHERE document_id IN (
                    SELECT id FROM documents
                    WHERE metadata->>'batch_tag' = %s
                );
                """,
                (batch_tag,),
            )

            # Borramos documentos
            cur.execute(
                "DELETE FROM documents WHERE metadata->>'batch_tag' = %s;",
                (batch_tag,),
            )

    print(f"Se eliminaron todos los documentos con batch_tag='{batch_tag}'.")


# -------------------------------------------------------------------
# BORRAR "LEGACY" (SIN batch_tag)
# -------------------------------------------------------------------

def cmd_delete_legacy(args):
    """
    Borra documentos que no tienen metadata->>'batch_tag'
    (típicamente los ingresados antes del pipeline v1.1).
    Útil para limpiar la BD y quedarte solo con los batch nuevos.
    """
    if not args.force:
        confirm = input(
            "Vas a BORRAR todos los documentos sin batch_tag en metadata "
            "(legacy). Escribe 'SI' para confirmar: "
        )
        if confirm.strip().upper() != "SI":
            print("Operación cancelada.")
            return

    with get_db() as conn:
        with conn.cursor() as cur:
            # Primero borramos los chunks de esos documentos
            cur.execute(
                """
                DELETE FROM document_chunks
                WHERE document_id IN (
                    SELECT id
                    FROM documents
                    WHERE metadata IS NULL
                    OR NOT (metadata ? 'batch_tag')
                );
                """
            )

            # Luego borramos los documentos
            cur.execute(
                """
                DELETE FROM documents
                WHERE metadata IS NULL
                OR NOT (metadata ? 'batch_tag');
                """
            )

    print("Se eliminaron todos los documentos 'legacy' sin batch_tag.")


# -------------------------------------------------------------------
# ARGPARSE
# -------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="Script de mantenimiento RAG (documents / document_chunks)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser(
        "list", help="Listar documentos con filtros básicos."
    )
    p_list.add_argument(
        "--doc-type",
        help="Filtrar por doc_type (ej. normativa_nacional, curriculo, inclusion_autismo...)",
    )
    p_list.add_argument(
        "--batch-tag",
        help="Filtrar por metadata->>'batch_tag' (ej. inclusion_batch_v1.1)",
    )
    p_list.add_argument(
        "--filename-substr",
        help="Filtrar por substring en metadata->>'raw_filename'.",
    )
    p_list.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Máximo de documentos a listar (por defecto 50).",
    )
    p_list.set_defaults(func=cmd_list)

    # delete-id
    p_del_id = subparsers.add_parser(
        "delete-id", help="Borrar un documento por id (y sus chunks)."
    )
    p_del_id.add_argument("id", type=int, help="ID del documento a borrar.")
    p_del_id.add_argument(
        "--force",
        action="store_true",
        help="No pedir confirmación interactiva.",
    )
    p_del_id.set_defaults(func=cmd_delete_id)

    # delete-batch
    p_del_batch = subparsers.add_parser(
        "delete-batch",
        help="Borrar todos los documentos con un batch_tag dado (y sus chunks).",
    )
    p_del_batch.add_argument(
        "batch_tag",
        help="Valor de metadata->>'batch_tag' a borrar.",
    )
    p_del_batch.add_argument(
        "--force",
        action="store_true",
        help="No pedir confirmación interactiva.",
    )
    p_del_batch.set_defaults(func=cmd_delete_batch)

    # delete-legacy
    p_del_legacy = subparsers.add_parser(
        "delete-legacy",
        help="Borrar documentos sin batch_tag (legacy) y sus chunks.",
    )
    p_del_legacy.add_argument(
        "--force",
        action="store_true",
        help="No pedir confirmación interactiva.",
    )
    p_del_legacy.set_defaults(func=cmd_delete_legacy)

    return parser


def main():
    print("🔌 Conectando a la BD (usando app.db)...")
    init_db()
    try:
        parser = build_parser()
        args = parser.parse_args()
        args.func(args)
    finally:
        close_db()
        print("🔌 Conexión cerrada.")


if __name__ == "__main__":
    main()
