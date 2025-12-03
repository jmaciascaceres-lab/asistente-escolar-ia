# backend/app/utils_sources.py
from typing import List, Dict

def build_sources_block_from_snippets(snippets: List[Dict], max_sources: int = 3) -> str:
    """
    Construye un bloque de texto plano con las fuentes más relevantes
    a partir de una lista de snippets (cada snippet es un dict).
    """
    seen = set()
    ordered = []

    for sn in snippets:
        title = sn.get("title") or "Documento sin título"
        source = sn.get("source") or ""
        year = sn.get("year")
        key = (title, source, year)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
        if len(ordered) >= max_sources:
            break

    if not ordered:
        return ""

    lines = ["", "Fuentes consultadas (no exhaustivas):"]
    for i, (title, source, year) in enumerate(ordered, start=1):
        detalle = title
        if year:
            detalle += f" ({year})"
        if source:
            detalle += f" – {source}"
        lines.append(f"{i}) {detalle}")

    return "\n".join(lines)
