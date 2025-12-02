# backend/app/cases/cu5_adaptar.py
from typing import Tuple

from ..rag_service import search_snippets
from ..llm_client import generate_llm_answer, GRANITE_MODEL_NAME
from ..llm_prompts import SYSTEM_PROMPT_TEACHER, build_cu5_user_prompt


def _extract_adapt_query_from_msg(msg) -> str:
    text = msg.text.strip()
    if text.startswith("/adaptar"):
        rest = text[len("/adaptar"):].strip()
        return rest or "esta actividad"
    return text or "esta actividad"


def adaptar_con_llm(msg) -> Tuple[str, dict]:
    """
    CU5: /adaptar para docentes (DUA e inclusión).
    Devuelve:
      - reply_text: str
      - llm_meta: dict con modelo y tokens.
    """
    user_query = _extract_adapt_query_from_msg(msg)

    # 1) Buscar contexto en RAG orientado a inclusión/DUA
    snippets = search_snippets(
        query=user_query,
        filters={
            # ajusta estos valores a tus doc_types reales
            # por ejemplo podrías usar un filtro más amplio y filtrar luego en Python
        },
        k=6,
    )

    # 2) Construir contexto textual
    context_blocks = []
    for i, sn in enumerate(snippets, start=1):
        title = sn.get("title") or "sin título"
        source = sn.get("source") or "fuente interna"
        content = sn.get("content") or ""
        context_blocks.append(
            f"[Fragmento {i}] Documento: {title} ({source})\n{content}"
        )

    context_text = "\n\n".join(context_blocks)

    # 3) Prompt específico para CU5
    user_prompt = build_cu5_user_prompt(
        user_query=user_query,
        context_text=context_text,
    )

    # 4) Llamada al LLM
    answer_text, prompt_tokens, completion_tokens = generate_llm_answer(
        system_prompt=SYSTEM_PROMPT_TEACHER,
        user_prompt=user_prompt,
        max_new_tokens=600,
        temperature=0.6,
    )

    # 5) Metadatos
    llm_meta = {
        "llm_model": GRANITE_MODEL_NAME,
        "llm_prompt_tokens": prompt_tokens,
        "llm_completion_tokens": completion_tokens,
    }

    return answer_text.strip(), llm_meta
