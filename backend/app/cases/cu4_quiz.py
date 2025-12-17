from typing import Tuple

from ..rag_service import search_snippets
from ..llm_client import generate_llm_answer, LLM_MODEL_NAME
from ..llm_prompts import SYSTEM_PROMPT_TEACHER, build_cu4_user_prompt
from ..utils_sources import build_sources_block_from_snippets, estimate_snippet_coverage, MIN_COVERAGE_RATIO


def _extract_quiz_query_from_msg(msg) -> str:
    """
    Extrae el tema del quiz desde msg.text.
    """
    text = (msg.text or "").strip()
    if text.startswith("/quiz"):
        rest = text[len("/quiz"):].strip()
        return rest or "este contenido"
    return text or "este contenido"


def quiz_con_llm(msg) -> Tuple[str, dict]:
    """
    CU4: generar preguntas de evaluación formativa con LLM + RAG.
    Retorna (texto_respuesta, llm_meta).
    """
    user_query = _extract_quiz_query_from_msg(msg)

    # 1) Recuperar snippets curriculares primero, luego fallback a todo
    snippets = search_snippets(user_query, filters={"doc_type": "curriculo"}, k=4)
    if not snippets:
        snippets = search_snippets(user_query, filters={}, k=4)

    # 2) Construir contexto para el LLM
    context_blocks = []
    for i, sn in enumerate(snippets, start=1):
        title = sn.get("title") or "sin título"
        content = sn.get("content") or ""
        context_blocks.append(
            f"[Fragmento {i}] Documento: {title}\n{content}"
        )
    context_text = "\n\n".join(context_blocks)

    # 3) Prompt de usuario específico para CU4
    user_prompt = build_cu4_user_prompt(
        user_query=user_query,
        context_text=context_text,
    )

    answer_text, prompt_tokens, completion_tokens = generate_llm_answer(
        system_prompt=SYSTEM_PROMPT_TEACHER,
        user_prompt=user_prompt,
        temperature=0.5,
    )

    coverage = estimate_snippet_coverage(answer_text, snippets)
    if snippets and coverage < MIN_COVERAGE_RATIO:
        aviso = (
            "Nota: estas preguntas podrían no reflejar con precisión los fragmentos curriculares "
            "que se usaron como base. Revísalas y ajústalas antes de aplicarlas en clase.\n\n"
        )
        answer_text = aviso + answer_text

    sources_block = build_sources_block_from_snippets(snippets)
    if sources_block:
        answer_text = (
            answer_text
            #+ "\n\nDebajo puedes ver textualmente qué dicen los documentos usados (al menos en parte):\n\n"
            + sources_block
        )

    llm_meta = {
        "llm_model": LLM_MODEL_NAME,
        "llm_prompt_tokens": prompt_tokens,
        "llm_completion_tokens": completion_tokens,
    }
    return answer_text.strip(), llm_meta
