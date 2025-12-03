# backend/app/cases/cu2_explicar.py
from typing import List
from ..rag_service import search_snippets
from ..llm_client import generate_llm_answer, LLM_MODEL_NAME
from ..llm_prompts import SYSTEM_PROMPT_STUDENT, build_cu2_user_prompt

def explicar_con_llm(msg):
    """
    Devuelve:
      - reply_text: str
      - llm_meta: dict con modelo y tokens
    """
    # Extraer el texto del mensaje
    user_query = msg.text
    
    # search_snippets no acepta doc_types, y k=4 es correcto
    snippets = search_snippets(query=user_query, k=4)
    
    context_text = "\n\n".join(
        f"[Fragmento {i}] ({sn.get('source', 'desconocido')})\n{sn.get('content', '')}"
        for i, sn in enumerate(snippets, start=1)
    )

    user_prompt = build_cu2_user_prompt(user_query=user_query, context_text=context_text)

    answer_text, prompt_tokens, completion_tokens = generate_llm_answer(
        system_prompt=SYSTEM_PROMPT_STUDENT,
        user_prompt=user_prompt,
        max_new_tokens=600,
        temperature=0.6,
    )

    llm_meta = {
        "llm_model": LLM_MODEL_NAME,
        "llm_prompt_tokens": prompt_tokens,
        "llm_completion_tokens": completion_tokens,
    }

    return answer_text, llm_meta
