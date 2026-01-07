# backend/app/utils_sources.py
import os
import re
from typing import List, Dict, Tuple, Any, Optional, Iterable

# Umbral configurable para marcar baja cobertura entre respuesta y fragmentos
MIN_COVERAGE_RATIO = float(os.getenv("LLM_MIN_COVERAGE_RATIO", "0.03"))
ELLIPSIS = "..."


def infer_subject(query: str) -> Optional[str]:
    q = (query or "").lower()
    if any(k in q for k in ["ciclo del agua", "fotosíntesis", "ecosistema", "materia", "energía", "célula"]):
        return "Ciencias Naturales"
    if any(k in q for k in ["fracción", "ecuación", "porcentaje", "geometría", "multiplicar", "dividir"]):
        return "Matemática"
    if any(k in q for k in ["comprensión lectora", "sujeto", "predicado", "texto", "cuento", "poema"]):
        return "Lenguaje y Comunicación"
    if any(k in q for k in ["revolución", "independencia", "geografía", "mapa", "estado", "democracia"]):
        return "Historia, Geografía y Ciencias Sociales"
    return None


def _single_line(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _clean_text(s: Optional[str]) -> str:
    return (s or "").strip()


def trim_to_word_boundaries(text: str) -> str:
    """
    Evita comenzar o terminar en medio de una palabra.
    Si el chunk viene partido, recorta al primer/último separador razonable.
    """
    t = _single_line(text)

    # Si empieza “a mitad de palabra” (p.ej. 'e los ele-'), recortamos hasta el primer espacio
    # cuando el primer token parece truncado (heurística suave).
    if t and not t[0].isalnum():
        t = t.lstrip(" -–—,.;:()[]{}")

    # Recorta inicio a la primera separación si el primer “token” es raro
    first_space = t.find(" ")
    if 0 < first_space < 4:  # token inicial muy corto suele ser fragmento
        t = t[first_space+1:].lstrip()

    # Asegura cierre limpio: corta al último espacio/puntuación razonable
    t = t.rstrip(" -–—,.;:()[]{}")
    return t


def build_excerpt(content: str, max_len: int = 380) -> str:
    """
    Crea un extracto de largo controlado, sin cortar palabra final.
    """
    t = trim_to_word_boundaries(content)
    if len(t) <= max_len:
        return f"<<{t}>>"

    cut = t[:max_len]
    # corta al último espacio cercano al final para no truncar palabra
    last_space = cut.rfind(" ")
    if last_space > max_len - 40:
        cut = cut[:last_space].rstrip()
    return f"<<{cut}{ELLIPSIS}>>"


def format_sources_user_facing(documents: Iterable, max_sources: int = 5) -> str:
    """
    Formato para Telegram (texto plano):
    1) Título (año opcional)
       URL
    Sin source/batch_tag/similarity.
    """
    seen = set()
    lines = []
    n = 0

    for d in documents:
        doc_id = getattr(d, "id", None) or getattr(d, "document_id", None)
        if doc_id in seen:
            continue
        seen.add(doc_id)

        title = _clean_text(getattr(d, "title", None) or getattr(d, "document_title", None))
        year = getattr(d, "year", None)
        url = _clean_text(getattr(d, "url", None))

        if not title:
            continue

        n += 1
        header = f"{n}) {title}" + (f" ({year})" if year else "")
        lines.append(header)

        if url:
            # Telegram lo vuelve clickeable automáticamente
            lines.append(url)

        if n >= max_sources:
            break

    if not lines:
        return ""

    return "Fuentes consultadas (enlaces):\n" + "\n".join(lines)


def _safe_excerpt(text: str, max_chars: int) -> Tuple[str, bool]:
    """
    Recorta a un máximo de caracteres sin cortar palabras al inicio/fin.
    Devuelve (excerpt, truncated).
    """
    if not text:
        return "", False

    # Normaliza espacios y saltos de línea para Telegram
    t = re.sub(r"\s+", " ", text).strip()

    if len(t) <= max_chars:
        return t, False

    # Tomamos un poco más para poder retroceder a un espacio
    slice_ = t[: max_chars + 1]

    # Retrocede al último espacio para no cortar palabra
    cut = slice_.rfind(" ")
    if cut == -1 or cut < int(max_chars * 0.6):
        # Si no hay espacios (o quedan demasiado pocos chars), fallback duro
        cut = max_chars

    out = slice_[:cut].rstrip(" ,;:.-")
    return out + "…", True


def _normalize_snippet_content(snippet: Dict[str, Any]) -> str:
    """
    Obtiene el contenido textual del snippet en una sola línea,
    colapsando saltos de línea y espacios extra.
    """
    raw = (
        snippet.get("content")
        or snippet.get("snippet")
        or snippet.get("text")
        or ""
    )
    text = " ".join(str(raw).split())
    return text


def _format_source_line(
    idx: int,
    snippet: Dict,
    include_scores: bool = True,
) -> str:
    """
    Formatea una línea de fuente con título, año, origen y (opcionalmente) score.
    """
    title: str = snippet.get("title") or "Documento sin título"
    year: Optional[int] = snippet.get("year")
    source: str = snippet.get("source") or "Carga Batch"

    score = snippet.get("score")
    score_str = ""
    if include_scores and isinstance(score, (int, float)):
        score_str = f" [relevancia aprox.: {score:.3f}]"

    year_str = f" ({year})" if year is not None else ""

    # Preview de contenido
    content: str = (snippet.get("content") or "").replace("\n", " ")
    if len(content) > 260:
        content_preview = content[:260] + "..."
    else:
        content_preview = content

    return (
        f"{idx}) {title}{year_str} – {source}{score_str}\n"
        f"Extracto: {content_preview}\n"
    )


def build_sources_block_from_snippets(snippets: List[Dict], title: str = "\n\nFuentes consultadas (enlaces + extractos):") -> str:
    """
    Render user-facing:
    - NO incluye source (batch_tag) ni distance (relevancia aprox.)
    - Incluye URL y extracto << >>
    """
    if not snippets:
        return ""

    lines = [title, ""]
    # dedupe por document_id conservando orden
    seen = set()
    items = []
    for sn in snippets:
        doc_id = sn.get("document_id")
        if doc_id in seen:
            continue
        seen.add(doc_id)
        items.append(sn)

    for i, sn in enumerate(items, start=1):
        doc_title = sn.get("title") or "Documento sin título"
        url = (sn.get("url") or "").strip()
        content = sn.get("content") or ""
        excerpt = build_excerpt(content)

        lines.append(f"{i}) {doc_title}")
        if url:
            lines.append(url)
        lines.append(f"Extracto: {excerpt}")
        lines.append("")

    lines.append("Nota: algunos extractos fueron recortados por extensión (marcados con «…»). Revisa el documento original si necesitas el párrafo completo.")
    return "\n".join(lines).strip()


def _tokenize_spanish(text: str) -> List[str]:
    """
    Tokenización muy simple: palabras de 3+ caracteres, en minúscula.
    Sirve solo como heurística barata.
    """
    if not text:
        return []
    return re.findall(r"[a-záéíóúüñ0-9]{3,}", text.lower())


def estimate_snippet_coverage(answer_text: str, snippets: List[Dict[str, Any]]) -> float:
    """
    Estima qué tanto vocabulario comparte la respuesta con los fragmentos usados.

    Heurística:
      - Construye un conjunto de tokens a partir del contenido de los snippets.
      - Calcula la fracción de esos tokens que también aparecen en la respuesta.
      - Si no hay snippets o no hay tokens, devuelve 0.0.

    NO es una métrica semántica “seria”, solo ayuda a marcar
    respuestas que probablemente se fueron muy lejos de las fuentes.
    """
    if not snippets:
        return 0.0

    base_text_parts = []
    for sn in snippets:
        content = sn.get("content") or ""
        base_text_parts.append(content)
    base_text = " ".join(base_text_parts)

    base_tokens = set(_tokenize_spanish(base_text))
    if not base_tokens:
        return 0.0

    answer_tokens = set(_tokenize_spanish(answer_text))
    if not answer_tokens:
        return 0.0

    inter = base_tokens.intersection(answer_tokens)
    ratio = len(inter) / len(base_tokens)
    return ratio
