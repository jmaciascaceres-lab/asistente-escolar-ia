from typing import Tuple

from ..rag_service import search_snippets
from ..llm_client import generate_llm_answer, LLM_MODEL_NAME
from ..llm_prompts import SYSTEM_PROMPT_TEACHER, build_cu3_user_prompt
from ..utils_sources import build_sources_block_from_snippets, estimate_snippet_coverage, MIN_COVERAGE_RATIO


def _extract_resumen_query_from_msg(msg) -> str:
    text = (msg.text or "").strip()
    if text.startswith("/resumen"):
        rest = text[len("/resumen"):].strip()
        return rest or "tu tema"
    return text or "tu tema"


def resumen_con_llm(msg) -> Tuple[str, dict]:
    """
    CU3: pre-resumen para adultos (docentes, coordinadores, apoderados).
    Retorna (texto_respuesta, llm_meta).
    """
    query = _extract_resumen_query_from_msg(msg)

    # 1) Preferimos normativa / inclusión
    preferred_types = ["normativa_nacional", "inclusion_autismo", "paec", "reglamento_interno"]
    snippets = []
    for t in preferred_types:
        snippets = search_snippets(query, filters={"doc_type": t}, k=4)
        if snippets:
            break

    if not snippets:
        snippets = search_snippets(query, filters={}, k=4)

    # 2) Contexto para el LLM
    context_blocks = []
    for i, sn in enumerate(snippets, start=1):
        title = sn.get("title") or "sin título"
        source = sn.get("source") or "fuente interna"
        content = sn.get("content") or ""
        context_blocks.append(
            f"[Fragmento {i}] Documento: {title} ({source})\n{content}"
        )
    context_text = "\n\n".join(context_blocks)

    # 3) Prompt CU3
    user_prompt = build_cu3_user_prompt(
        query=query,
        context_text=context_text,
    )

    answer_text, prompt_tokens, completion_tokens = generate_llm_answer(
        system_prompt=SYSTEM_PROMPT_TEACHER,
        user_prompt=user_prompt,
        temperature=0.4,
    )

    coverage = estimate_snippet_coverage(answer_text, snippets)
    if snippets and coverage < MIN_COVERAGE_RATIO:
        aviso = (
            "Nota: este pre-resumen podría no estar muy alineado con los fragmentos concretos "
            "que se recuperaron. Úsalo solo como guía inicial y revisa directamente los documentos "
            "antes de tomar decisiones.\n\n"
        )
        answer_text = aviso + answer_text

    sources_block = build_sources_block_from_snippets(snippets)
    if sources_block:
        answer_text = (
            answer_text
            + "\n\nDebajo puedes ver textualmente qué dicen los documentos usados (al menos en parte):\n\n"
            + sources_block
        )

    llm_meta = {
        "llm_model": LLM_MODEL_NAME,
        "llm_prompt_tokens": prompt_tokens,
        "llm_completion_tokens": completion_tokens,
    }
    return answer_text.strip(), llm_meta
