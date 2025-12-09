import sys
import re
import hashlib
from pathlib import Path
from typing import Tuple

from pypdf import PdfReader
from dotenv import load_dotenv

# Hacemos disponible app.*
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

# Cargar variables de entorno desde .env
load_dotenv(ROOT_DIR / ".env")

from app.db import init_db, close_db  # noqa: E402
from app.rag_service import ingest_document_with_text, chunk_text  # noqa: E402


def extract_text_from_pdf(pdf_path: Path) -> str:
    """
    Paso 1: Ingesta y normalización básica de formato desde PDF.
    """
    reader = PdfReader(str(pdf_path))
    texts = []
    for page in reader.pages:
        t = page.extract_text() or ""
        texts.append(t)
    return "\n\n".join(texts)


def clean_text_basic(text: str) -> str:
    """
    Paso 2: Limpieza básica de texto.
    - Normaliza saltos de línea y espacios.
    - Evita que se pierdan los párrafos (mantenemos doble \n\n).
    """
    if not text:
        return ""

    # Normalizar retornos de carro
    text = text.replace("\r", "\n")

    # Quitar espacios al inicio/fin de cada línea
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)

    # Colapsar múltiples líneas en blanco en máximo 2 (para marcar párrafos)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Colapsar espacios múltiples dentro de líneas
    text = re.sub(r"[ \t]+", " ", text)

    return text.strip()


def deduplicate_paragraphs(text: str, min_len: int = 50) -> Tuple[str, int, int]:
    """
    Paso 3: Deduplicación muy simple de párrafos largos.

    Estrategia:
    - Dividimos el texto en párrafos por doble salto de línea.
    - Párrafos de longitud < min_len se dejan tal cual (suelen ser títulos, etc.).
    - Para párrafos más largos, si ya vimos el mismo texto exactamente, lo eliminamos.
    """
    if not text:
        return "", 0, 0

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    seen = set()
    kept = []
    removed = 0

    for p in paragraphs:
        if len(p) < min_len:
            kept.append(p)
            continue

        key = hashlib.md5(p.encode("utf-8")).hexdigest()
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        kept.append(p)

    cleaned = "\n\n".join(kept)
    return cleaned, len(paragraphs), removed


# Ajusta las rutas a donde tengas guardados los PDFs en tu proyecto (ej. ../docs)
DOCS_DIR = ROOT_DIR / "docs"

ALREADY_LOADED = [
    # "2016_Orientaciones-para-la-construccion-de-comunidades-educativas-inclusivas.pdf"
]


def infer_metadata(filename: str):
    """
    Infiere título, año y tipo de documento a partir del nombre del archivo.
    """
    # 1. Año: primeros 4 dígitos si existen
    year = None
    if filename[:4].isdigit():
        try:
            year = int(filename[:4])
        except ValueError:
            pass

    # 2. Título: nombre del archivo sin extensión, reemplazando _ y - por espacios
    # Si empieza con año, lo quitamos del título para que quede más limpio
    stem = Path(filename).stem
    if year and stem.startswith(str(year)):
        # quita "2016_" o "2016-"
        clean_stem = stem[4:].lstrip("_- ")
    else:
        clean_stem = stem

    title = clean_stem.replace("_", " ").replace("-", " ").strip()

    # 3. Doc Type: heurística simple
    lower_name = filename.lower()
    doc_type = "documento_general"

    if "paec" in lower_name:
        doc_type = "paec"
    elif "inclusion" in lower_name or "inclusión" in lower_name:
        # puedes cambiar a 'normativa_nacional' si prefieres
        doc_type = "inclusion_autismo"
    elif "decreto" in lower_name or "ley" in lower_name or "orientaciones" in lower_name:
        doc_type = "normativa_nacional"

    return title, year, doc_type


def main():
    print("🚀 Iniciando script de ingesta MVP v1.1 (con limpieza previa)...")
    print(f"📂 DOCS_DIR: {DOCS_DIR}")
    print("🔌 Conectando a la BD...")
    init_db()
    print("✅ BD conectada.")
    try:
        if not DOCS_DIR.exists():
            print(f"❌ No existe el directorio: {DOCS_DIR}")
            return

        # Listar todos los PDFs
        pdf_files = list(DOCS_DIR.glob("*.pdf"))
        print(f"📂 Se encontraron {len(pdf_files)} archivos PDF en {DOCS_DIR}")

        for pdf_path in pdf_files:
            filename = pdf_path.name

            if filename in ALREADY_LOADED:
                print(f"⏭️ Saltando archivo ya cargado: {filename}")
                continue

            print(f"\n👉 Procesando: {filename}")

            # Paso 1: Extraer texto "crudo"
            try:
                raw_text = extract_text_from_pdf(pdf_path)
            except Exception as e:
                print(f"   ❌ Error leyendo PDF: {e}")
                continue

            raw_len = len(raw_text)
            if raw_len == 0:
                print("   ⚠️ PDF sin texto extraído (¿escaneado sin OCR?). Se omite.")
                continue

            # Paso 2: Limpieza básica
            cleaned_text = clean_text_basic(raw_text)
            clean_len = len(cleaned_text)

            print(
                f"   🧹 Limpieza básica: {raw_len} → {clean_len} caracteres "
                f"({(clean_len / raw_len * 100):.1f}% del tamaño original)"
            )

            # Paso 3: Deduplicación de párrafos
            dedup_text, total_pars, removed_pars = deduplicate_paragraphs(cleaned_text)
            dedup_len = len(dedup_text)

            print(
                f"   🔁 Deduplicación de párrafos: total={total_pars}, "
                f"eliminados={removed_pars}, chars={clean_len} → {dedup_len}"
            )

            if dedup_len < 1000:
                print(
                    "   ⚠️ Documento quedó muy corto tras limpieza/deduplicación. "
                    "Revisa si la extracción del PDF es adecuada."
                )

            # Paso 4+7: estimación rápida de chunking antes de grabar
            preview_chunks = chunk_text(dedup_text)
            num_chunks = len(preview_chunks)
            if num_chunks == 0:
                print("   ⚠️ No se generaron chunks en validación previa. Se omite este PDF.")
                continue

            print(
                f"   🔍 Validación rápida de chunking: {num_chunks} chunks estimados "
                f"(ejemplo inicio primer chunk: {preview_chunks[0][:120]!r})"
            )

            # Paso 5: Metadata enriquecida
            title, year, doc_type = infer_metadata(filename)

            tags = []
            if year:
                tags.append(str(year))
            tags.append(doc_type)

            metadata = {
                "tags": tags,
                "filename": filename,
                "pipeline": {
                    "raw_chars": raw_len,
                    "clean_chars": clean_len,
                    "dedup_chars": dedup_len,
                    "paragraphs_total": total_pars,
                    "paragraphs_dedup_removed": removed_pars,
                    "estimated_chunks": num_chunks,
                },
                "snippet": dedup_text[:600],
                "docs_dir": str(DOCS_DIR),
            }

            print(f"   📥 Ingestando como: «{title}» ({doc_type}, {year})")

            # Paso 6: Ingesta + embeddings (dentro de ingest_document_with_text)
            try:
                doc_id = ingest_document_with_text(
                    title=title,
                    doc_type=doc_type,
                    full_text=dedup_text,
                    source="Carga Batch",
                    subject=None,
                    year=year,
                    metadata=metadata,
                )
                print(f"   ✅ OK. ID={doc_id}")
            except Exception as e:
                print(f"   ❌ Error ingestando en BD: {e}")

    finally:
        close_db()
        print("🔌 Conexión a BD cerrada.")


if __name__ == "__main__":
    main()
