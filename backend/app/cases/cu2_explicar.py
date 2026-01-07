# backend/app/cases/cu2_explicar.py
from typing import Tuple

from ..rag_service import search_snippets
from ..llm_client import generate_llm_answer, LLM_MODEL_NAME
from ..llm_prompts import SYSTEM_PROMPT_STUDENT, build_cu2_user_prompt
from ..utils_sources import build_sources_block_from_snippets, estimate_snippet_coverage, MIN_COVERAGE_RATIO, infer_subject 


def _extract_explanation_topic_from_msg(msg) -> str:
    text = (msg.text or "").strip()
    if text.startswith("/explicar"):
        rest = text[len("/explicar"):].strip()
        return rest or "este contenido"
    return text or "este contenido"


def explicar_con_llm(msg) -> Tuple[str, dict]:
    """
    CU2: explicación de contenido para estudiantes con LLM + RAG.
    Retorna (texto_respuesta, llm_meta).
    """
    user_query = _extract_explanation_topic_from_msg(msg)
    mode = msg.settings.get("modo", "alto")

    # 1) Recuperar snippets de currículo primero
    subject = infer_subject(user_query)

    snippets = search_snippets(user_query, filters={"doc_type": "curriculo"}, k=4)

    if not snippets and subject:
        snippets = search_snippets(user_query, filters={"doc_type": "curriculo", "subject": subject}, k=4)

    if not snippets and subject:
        snippets = search_snippets(user_query, filters={"subject": subject}, k=4)

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

    # 3) Prompt específico para CU2
    user_prompt = build_cu2_user_prompt(
        user_query=user_query,
        context_text=context_text,
        mode=mode,
    )

    # 3) LLM
    answer_text, prompt_tokens, completion_tokens = generate_llm_answer(
        system_prompt=SYSTEM_PROMPT_STUDENT,
        user_prompt=user_prompt,
        temperature=0.6,
    )

    # 3.b) Chequeo ligero de cobertura con los fragmentos
    coverage = estimate_snippet_coverage(answer_text, snippets)
    if snippets and coverage < MIN_COVERAGE_RATIO:
        aviso = (
            "Nota: la siguiente explicación podría no estar fuertemente alineada con los "
            "fragmentos de documentos que se usaron como base. Tómala solo como apoyo inicial "
            "y revisa directamente el material de clases o los documentos del establecimiento.\n\n"
        )
        answer_text = aviso + answer_text

    # 4) Fuentes consultadas
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
