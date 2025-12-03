# backend/app/llm_client.py
import os
from typing import Tuple

from google import genai
from google.genai import types

# 1) Configurar cliente Gemini
#    Usa GOOGLE_API_KEY o GEMINI_API_KEY (la librería los detecta).
_client = None

LLM_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash")


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        # Si usas Gemini Developer API (no Vertex):
        _client = genai.Client(
            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        )
    return _client


def generate_llm_answer(
    system_prompt: str,
    user_prompt: str,
    max_new_tokens: int = 8192,
    temperature: float = 0.7,
) -> Tuple[str, int, int]:
    """
    Llama a Gemini y devuelve:
      - answer_text: str
      - prompt_tokens: int (aprox)
      - completion_tokens: int (aprox)
    """
    client = _get_client()

    # Armamos un contenido tipo "chat" simple:
    full_prompt = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}"

    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_new_tokens,
    )

    response = client.models.generate_content(
        model=LLM_MODEL_NAME,
        contents=full_prompt,
        config=config,
    )

    print(f"DEBUG: LLM Model: {LLM_MODEL_NAME}")
    print(f"DEBUG: Response object: {response}")

    # Texto generado: Intentamos obtener .text, si falla, buscamos en parts
    answer_text = ""
    if response.text:
        answer_text = (response.text or "").strip()
        answer_text = answer_text.replace("**", "")
    else:
        # Fallback para cuando .text es None (ej: finish_reason=MAX_TOKENS o safety)
        try:
            if response.candidates and response.candidates[0].content.parts:
                parts_text = []
                for part in response.candidates[0].content.parts:
                    if part.text:
                        parts_text.append(part.text)
                answer_text = "".join(parts_text).strip()
                answer_text = answer_text.replace("**", "")
                print(f"DEBUG: Extracted text from parts: {answer_text[:100]}...")
        except Exception as e:
             print(f"DEBUG: Error extracting from parts: {e}")

    if not answer_text:
         print("DEBUG: WARNING - Resulting answer_text is empty.")

    # Tokens: usamos usage_metadata si está disponible
    prompt_tokens = None
    completion_tokens = None
    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        # Los nombres pueden variar según versión; probamos ambos estilos
        prompt_tokens = getattr(usage, "prompt_token_count", None) or getattr(
            usage, "promptTokenCount", None
        )
        completion_tokens = getattr(usage, "candidates_token_count", None) or getattr(
            usage, "candidatesTokenCount", None
        )

    return answer_text, prompt_tokens or 0, completion_tokens or 0
