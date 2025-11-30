# backend/scripts/ingest_pdf.py

import os
import sys
import argparse
from pathlib import Path

from dotenv import load_dotenv
from pypdf import PdfReader

load_dotenv()  # Carga variables de entorno desde .env

# Añadir ../ al path para poder importar app.*
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

from app.db import init_db, close_db  # noqa: E402
from app.rag_service import ingest_document_with_text  # noqa: E402


def extract_text_from_pdf(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    texts = []
    for page in reader.pages:
        t = page.extract_text() or ""
        texts.append(t)
    return "\n\n".join(texts)


def main():
    parser = argparse.ArgumentParser(
        description="Ingesta de un PDF al RAG (documents + document_chunks)"
    )
    parser.add_argument("--path", required=True, help="Ruta al PDF")
    parser.add_argument("--title", required=True, help="Título a guardar en la BD")
    parser.add_argument("--doc-type", required=True, help="Tipo de documento (ej: normativa_nacional, inclusion_autismo, curriculo, paec, reglamento_interno)")
    parser.add_argument("--source", default=None, help="Fuente (MINEDUC, UNESCO, Colegio X, etc.)")
    parser.add_argument("--subject", default=None, help="Asignatura (Lenguaje, Historia, etc.)")
    parser.add_argument("--year", type=int, default=None, help="Año del documento")
    parser.add_argument("--tag", action="append", help="Tag opcional (se puede poner varias veces)")

    args = parser.parse_args()

    pdf_path = Path(args.path)
    if not pdf_path.exists():
        print(f"❌ PDF no encontrado: {pdf_path}")
        sys.exit(1)

    print(f"📄 Extrayendo texto de {pdf_path}...")
    full_text = extract_text_from_pdf(pdf_path)

    metadata = {"tags": args.tag or []}

    print("🧬 Inicializando conexión a BD...")
    init_db()
    try:
        print("📥 Ingestando documento y chunks...")
        doc_id = ingest_document_with_text(
            title=args.title,
            doc_type=args.doc_type,
            full_text=full_text,
            source=args.source,
            subject=args.subject,
            year=args.year,
            metadata=metadata,
        )
        print(f"✅ Documento ingerido con id={doc_id}")
    finally:
        close_db()


if __name__ == "__main__":
    main()