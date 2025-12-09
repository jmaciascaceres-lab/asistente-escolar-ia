# backend/app/utils_sources.py
import os
import re
from typing import List, Dict, Tuple, Any, Optional

# Umbral configurable para marcar baja cobertura entre respuesta y fragmentos
MIN_COVERAGE_RATIO = float(os.getenv("LLM_MIN_COVERAGE_RATIO", "0.03"))


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
        f"   Extracto: {content_preview}\n"
    )


def build_sources_block_from_snippets(
    snippets: List[Dict],
    max_sources: int = 3,
    max_chars_per_snippet: int = 350,
) -> str:
    """
    Construye un bloque de texto plano con las fuentes más relevantes
    a partir de una lista de snippets (cada snippet es un dict).

    Incluye un extracto textual de cada fragmento usado, para que la persona
    pueda verificar qué dice realmente el documento.
    """
    if not snippets:
        return ""

    seen_docs = set()
    lines: List[str] = []
    any_truncated = False  # para avisar al final si recortamos extractos

    for sn in snippets:
        # Agrupamos por documento; si falta el id, usamos (title, source)
        doc_key: Tuple[Any, Any, Any] = (
            sn.get("document_id"),
            sn.get("title"),
            sn.get("source"),
        )
        if doc_key in seen_docs:
            continue
        seen_docs.add(doc_key)

        title = sn.get("title") or "Documento sin título"
        source = sn.get("source") or ""
        year = sn.get("year")
        distance = sn.get("distance")

        # Etiqueta de "source" un poco más amigable para docentes
        source_label = source
        if source_label == "Carga Batch":
            source_label = "Carga batch (ingesta interna)"

        # Cabecera de la fuente
        header = f"{len(seen_docs)}) {title}"
        if year:
            header += f" ({year})"
        if source_label:
            header += f" – {source_label}"
        if distance is not None:
            try:
                header += f" [relevancia aprox.: {1.0 - float(distance):.3f}]"
            except (TypeError, ValueError):
                pass

        # Extracto textual (recortado si es muy largo)
        content = _normalize_snippet_content(sn)
        truncated = False
        if len(content) > max_chars_per_snippet:
            truncated = True
            any_truncated = True
            # Dejamos espacio para el «…»
            content = content[: max_chars_per_snippet - 1].rstrip() + "…"

        lines.append(header)
        if content:
            lines.append(f"   Extracto: {content}")
        lines.append("")  # línea en blanco entre fuentes

        if len(seen_docs) >= max_sources:
            break

    block = "\n\nFuentes consultadas (con extractos textuales de los documentos):\n\n"
    block += "\n".join(lines).rstrip()

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
