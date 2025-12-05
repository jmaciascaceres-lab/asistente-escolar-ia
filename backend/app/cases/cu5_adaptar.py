from typing import Tuple

from ..rag_service import search_snippets
from ..llm_client import generate_llm_answer, LLM_MODEL_NAME
from ..llm_prompts import SYSTEM_PROMPT_TEACHER, build_cu5_user_prompt
from ..utils_sources import build_sources_block_from_snippets, estimate_snippet_coverage, MIN_COVERAGE_RATIO


def _extract_adapt_request_from_msg(msg) -> str:
    text = (msg.text or "").strip()
    if text.startswith("/adaptar"):
        rest = text[len("/adaptar"):].strip()
        return rest or "esta actividad"
    return text or "esta actividad"


def adaptar_con_llm(msg) -> Tuple[str, dict]:
    """
    CU5: sugerencias de adaptación de actividad con enfoque DUA.
    Retorna (texto_respuesta, llm_meta).
    """
    request = _extract_adapt_request_from_msg(msg)

    # 1) Snippets preferentes de inclusión / DUA
    preferred_types = ["normativa_nacional", "inclusion_autismo", "paec"]
    snippets = []
    for t in preferred_types:
        snippets = search_snippets(request, filters={"doc_type": t}, k=4)
        if snippets:
            break

    if not snippets:
        snippets = search_snippets(request, filters={}, k=4)

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

    # 3) Prompt CU5
    user_prompt = build_cu5_user_prompt(
        request_text=request,
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
            "Nota: esta propuesta de adaptación podría no estar fuertemente apoyada en los "
            "fragmentos de documentos recuperados. Úsala solo como base de reflexión y revisa "
            "directamente la normativa y las orientaciones del establecimiento.\n\n"
        )
        answer_text = aviso + answer_text

    sources_block = build_sources_block_from_snippets(snippets)
    if sources_block:
        answer_text = (
            answer_text
            + "\n\nDebajo puedes ver textualmente qué dicen los documentos usados:\n\n"
            + sources_block
        )

    llm_meta = {
        "llm_model": LLM_MODEL_NAME,
        "llm_prompt_tokens": prompt_tokens,
        "llm_completion_tokens": completion_tokens,
    }
    return answer_text.strip(), llm_meta
