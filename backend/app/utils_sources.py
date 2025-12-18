# backend/app/utils_sources.py
import os
import re
from typing import List, Dict, Tuple, Any, Optional, Iterable

# Umbral configurable para marcar baja cobertura entre respuesta y fragmentos
MIN_COVERAGE_RATIO = float(os.getenv("LLM_MIN_COVERAGE_RATIO", "0.03"))


def _clean_text(s: Optional[str]) -> str:
    return (s or "").strip()

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


def build_sources_block_from_snippets(
    snippets: List[Dict],
    max_sources: int = 3,
    max_chars_per_snippet: int = 350,
) -> str:
    """
    Construye un bloque de texto plano con fuentes y enlaces, sin exponer
    metadatos internos (batch_tag/source) ni puntajes de similitud.

    Incluye un extracto textual acotado de cada fragmento usado, para que
    la persona pueda verificar qué dice realmente el documento.
    """
    if not snippets:
        return ""

    seen_docs = set()
    lines: List[str] = []
    any_truncated = False

    for sn in snippets:
        doc_key = (
            sn.get("document_id"),
            sn.get("title"),
            sn.get("url"),
        )
        if doc_key in seen_docs:
            continue
        seen_docs.add(doc_key)

        title = sn.get("title") or "Documento sin título"
        year = sn.get("year")
        url = (sn.get("url") or "").strip()

        header = f"{len(seen_docs)}) {title}"
        if year:
            header += f" ({year})"
        lines.append(header)

        # URL clickeable en Telegram (texto plano)
        if url:
            lines.append(url)

        content = _normalize_snippet_content(sn)
        if content:
            excerpt, truncated = _safe_excerpt(content, max_chars_per_snippet)
            any_truncated = any_truncated or truncated

            if excerpt:
                lines.append(f"Extracto: «{excerpt}»")

        lines.append("")  # separación visual

        if len(seen_docs) >= max_sources:
            break

    while lines and lines[-1] == "":
        lines.pop()

    if not lines:
        return ""

    block = ".\n\nFuentes consultadas (enlaces + extractos):\n\n" + "\n".join(lines).rstrip()

    if any_truncated:
        block += (
            "\n\nNota: algunos extractos fueron recortados por extensión "
            "(marcados con «…»). Revisa el documento original si necesitas el párrafo completo."
        )

    return block


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
