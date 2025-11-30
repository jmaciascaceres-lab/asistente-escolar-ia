import sys
from pathlib import Path

from pypdf import PdfReader

from dotenv import load_dotenv

# Hacemos disponible app.*
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

# Cargar variables de entorno desde .env
load_dotenv(ROOT_DIR / ".env")

from app.db import init_db, close_db  # noqa: E402
from app.rag_service import ingest_document_with_text  # noqa: E402



def extract_text_from_pdf(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    texts = []
    for page in reader.pages:
        t = page.extract_text() or ""
        texts.append(t)
    return "\n\n".join(texts)


# Ajusta las rutas a donde tengas guardados los PDFs en tu proyecto (ej. ../docs)
DOCS_DIR = ROOT_DIR / "docs"

ALREADY_LOADED = [
    #"2016_Orientaciones-para-la-construccion-de-comunidades-educativas-inclusivas.pdf"
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
        doc_type = "inclusion_autismo" # o normativa_nacional, según preferencia
    elif "decreto" in lower_name or "ley" in lower_name or "orientaciones" in lower_name:
        doc_type = "normativa_nacional"
        
    return title, year, doc_type

def main():
    print("🚀 Iniciando script...")
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
                
            print(f"👉 Procesando: {filename}")
            
            # Extraer texto
            try:
                full_text = extract_text_from_pdf(pdf_path)
            except Exception as e:
                print(f"   ❌ Error leyendo PDF: {e}")
                continue
            
            # Inferir metadata
            title, year, doc_type = infer_metadata(filename)
            
            # Tags básicos
            tags = []
            if year:
                tags.append(str(year))
            tags.append(doc_type)
            
            metadata = {"tags": tags, "filename": filename}

            print(f"   📥 Ingestando como: «{title}» ({doc_type}, {year})")
            
            try:
                doc_id = ingest_document_with_text(
                    title=title,
                    doc_type=doc_type,
                    full_text=full_text,
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


if __name__ == "__main__":
    main()
