# backend/app/cases/cu4_quiz.py
from typing import Tuple

from ..rag_service import search_snippets
from ..llm_client import generate_llm_answer, GRANITE_MODEL_NAME
from ..llm_prompts import SYSTEM_PROMPT_TEACHER, build_cu4_user_prompt


def _extract_quiz_query_from_msg(msg) -> str:
    text = msg.text.strip()
    if text.startswith("/quiz"):
        rest = text[len("/quiz"):].strip()
        return rest or "este contenido"
    return text or "este contenido"


def quiz_con_llm(msg) -> Tuple[str, dict]:
    """
    CU4: /quiz para docentes (evaluación formativa).
    Devuelve:
      - reply_text: str con las preguntas
      - llm_meta: dict con modelo y tokens.
    """
    user_query = _extract_quiz_query_from_msg(msg)

    # 1) Recuperar snippets relacionados (idealmente currículo)
    snippets = search_snippets(
        query=user_query,
        filters={"doc_type": "curriculo"},  # ajusta si tienes otro naming
        k=4,
    )
    if not snippets:
        snippets = search_snippets(query=user_query, filters={}, k=4)

    # 2) Construir contexto para el LLM
    context_blocks = []
    for i, sn in enumerate(snippets, start=1):
        title = sn.get("title") or "sin título"
        source = sn.get("source") or "fuente interna"
        content = sn.get("content") or ""
        context_blocks.append(
            f"[Fragmento {i}] Documento: {title} ({source})\n{content}"
        )

    context_text = "\n\n".join(context_blocks)

    # 3) Prompt específico CU4
    user_prompt = build_cu4_user_prompt(
        user_query=user_query,
        context_text=context_text,
    )

    # 4) Llamada a Granite
    answer_text, prompt_tokens, completion_tokens = generate_llm_answer(
        system_prompt=SYSTEM_PROMPT_TEACHER,
        user_prompt=user_prompt,
        max_new_tokens=400,
        temperature=0.7,
    )

    # 5) Metadatos
    llm_meta = {
        "llm_model": GRANITE_MODEL_NAME,
        "llm_prompt_tokens": prompt_tokens,
        "llm_completion_tokens": completion_tokens,
    }

    return answer_text.strip(), llm_meta
