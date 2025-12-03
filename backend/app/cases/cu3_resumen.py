# backend/app/cases/cu3_resumen.py
from typing import Tuple

from ..rag_service import search_snippets
from ..llm_client import generate_llm_answer, LLM_MODEL_NAME
from ..llm_prompts import SYSTEM_PROMPT_TEACHER, build_cu3_user_prompt


def _extract_resumen_query_from_msg(msg) -> str:
    text = msg.text.strip()
    if text.startswith("/resumen"):
        rest = text[len("/resumen"):].strip()
        return rest or "tu tema"
    return text or "tu tema"


def resumen_con_llm(msg) -> Tuple[str, dict]:
    """
    CU3: /resumen para docentes.
    Devuelve:
      - reply_text: str
      - llm_meta: dict con modelo y tokens (para log_interaction).
    """
    user_query = _extract_resumen_query_from_msg(msg)

    # 1) Buscar contexto en RAG
    snippets = search_snippets(
        query=user_query,
        filters={},   # o filtra por doc_type si quieres
        k=6,
    )

    # 2) Armar texto de contexto a partir de los snippets
    context_blocks = []
    for i, sn in enumerate(snippets, start=1):
        # Ajusta estos campos a tu estructura real de snippet
        title = sn.get("title") or "sin título"
        source = sn.get("source") or "fuente interna"
        content = sn.get("content") or ""
        context_blocks.append(
            f"[Fragmento {i}] Documento: {title} ({source})\n{content}"
        )

    context_text = "\n\n".join(context_blocks)

    # 3) Prompt de usuario específico para CU3
    user_prompt = build_cu3_user_prompt(
        user_query=user_query,
        context_text=context_text,
    )

    # 4) Llamada al LLM
    answer_text, prompt_tokens, completion_tokens = generate_llm_answer(
        system_prompt=SYSTEM_PROMPT_TEACHER,
        user_prompt=user_prompt,
        max_new_tokens=500,
        temperature=0.5,
    )

    # 5) Metadatos LLM
    llm_meta = {
        "llm_model": LLM_MODEL_NAME,
        "llm_prompt_tokens": prompt_tokens,
        "llm_completion_tokens": completion_tokens,
    }

    return answer_text.strip(), llm_meta
