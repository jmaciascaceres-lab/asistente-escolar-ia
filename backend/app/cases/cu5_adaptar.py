from typing import Tuple
import os

from ..rag_service import search_snippets
from ..llm_client import generate_llm_answer
from ..llm_prompts import SYSTEM_PROMPT_TEACHER, build_cu5_user_prompt
from ..utils_sources import build_sources_block_from_snippets, estimate_snippet_coverage, MIN_COVERAGE_RATIO

RAG_ENABLED = os.getenv("RAG_ENABLED", "true").lower() in ("1", "true", "yes")


def _extract_adapt_request_from_msg(msg) -> str:
    text = (msg.text or "").strip()
    if text.startswith("/adaptar"):
        rest = text[len("/adaptar") :].strip()
        return rest or "esta actividad"
    return text or "esta actividad"


def adaptar_con_llm(msg) -> Tuple[str, dict]:
    """
    CU5: sugerencias de adaptación de actividad con enfoque DUA.
    Retorna (texto_respuesta, llm_meta).
    """
    request = _extract_adapt_request_from_msg(msg)

    # 1) Recuperación (RAG) - solo si está habilitado
    snippets = []
    if RAG_ENABLED:
        preferred_types = ["normativa_nacional", "inclusion_autismo", "paec"]
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
        content = sn.get("content") or ""
        context_blocks.append(f"[Fragmento {i}] Documento: {title}\n{content}")
    context_text = "\n\n".join(context_blocks)

    # 3) Prompt CU5
    user_prompt = build_cu5_user_prompt(
        request_text=request,
        context_text=context_text,
    )

    answer_text, prompt_tokens, completion_tokens, model_label = generate_llm_answer(
        system_prompt=SYSTEM_PROMPT_TEACHER,
        user_prompt=user_prompt,
        temperature=0.5,
    )

    # 4) Cobertura solo si hubo snippets
    if snippets:
        coverage = estimate_snippet_coverage(answer_text, snippets)
        if coverage < MIN_COVERAGE_RATIO:
            aviso = (
                "Nota: esta propuesta de adaptación podría no estar fuertemente apoyada en los "
                "fragmentos de documentos recuperados. Úsala solo como base de reflexión y revisa "
                "directamente la normativa y las orientaciones del establecimiento.\n\n"
            )
            answer_text = aviso + answer_text

    # 5) Fuentes (solo si hay snippets)
    sources_block = build_sources_block_from_snippets(snippets)
    if sources_block:
        answer_text = answer_text + sources_block

    llm_meta = {
        "llm_model": model_label,
        "llm_prompt_tokens": prompt_tokens,
        "llm_completion_tokens": completion_tokens,
        "used_rag": bool(snippets),
        "snippets_count": len(snippets),
    }
    return answer_text.strip(), llm_meta
