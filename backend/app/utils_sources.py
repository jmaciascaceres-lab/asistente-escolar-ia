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
    include_scores: bool = True,
) -> str:
    """
    Construye el bloque de texto con las fuentes consultadas.

    IMPORTANTE:
    - NO incluye la frase "Debajo puedes ver textualmente qué dicen los documentos usados".
      Esa frase la estás agregando en cada CU antes de llamar a esta función, para evitar
      duplicados.
    """
    if not snippets:
        return ""

    lines: List[str] = []
    for i, sn in enumerate(snippets, start=1):
        lines.append(_format_source_line(i, sn, include_scores=include_scores))

    header = "Fuentes consultadas (con extractos textuales de los documentos):\n\n"
    return header + "".join(lines)


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
