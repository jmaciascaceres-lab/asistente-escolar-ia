# backend/app/utils_sources.py
import os
import re
from typing import List, Dict, Tuple, Any

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


def build_sources_block_from_snippets(
    snippets: List[Dict],
    max_sources: int = 3,
    max_chars_per_snippet: int = 350,
) -> str:
    """
    Construye un bloque de texto plano con las fuentes más relevantes
    a partir de una lista de snippets (cada snippet es un dict).

    Incluye también un EXTRACTO textual de cada fragmento usado, para
    que la persona pueda verificar qué dice realmente el documento.
    """
    if not snippets:
        return ""

    seen_docs = set()
    lines: List[str] = []

    for sn in snippets:
        # Usamos document_id para agrupar; si no existe, caemos a (title, source)
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
        score = sn.get("distance")

        # Cabecera de la fuente
        header = f"{len(seen_docs)}) {title}"
        if year:
            header += f" ({year})"
        if source:
            header += f" – {source}"
        if score is not None:
            try:
                header += f" [relevancia aprox.: {1.0 - float(score):.3f}]"
            except (TypeError, ValueError):
                pass

        # Extracto textual
        content = _normalize_snippet_content(sn)
        if len(content) > max_chars_per_snippet:
            content = content[: max_chars_per_snippet - 3].rstrip() + "..."

        lines.append(header)
        if content:
            lines.append(f"   Extracto: {content}")
        lines.append("")  # línea en blanco entre fuentes

        if len(seen_docs) >= max_sources:
            break

    block = (
        "Fuentes consultadas (con extractos textuales de los documentos):\n\n"
        "Debajo puedes ver textualmente qué dicen los documentos usados (al menos en parte):\n\n"
    )
    block += "\n".join(lines).rstrip()
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
