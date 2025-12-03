# backend/app/llm_client.py
import os
from typing import Tuple

from google import genai
from google.genai import types

_client = None
LLM_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash")


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        )
    return _client


def generate_llm_answer(
    system_prompt: str,
    user_prompt: str,
    max_new_tokens: int = 512,
    temperature: float = 0.6,
) -> Tuple[str, int, int]:
    """
    Llama a Gemini y devuelve:
      - answer_text: str
      - prompt_tokens: int
      - completion_tokens: int

    En caso de error, devuelve un mensaje seguro y tokens = 0.
    """
    client = _get_client()

    full_prompt = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}"

    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_new_tokens,
    )

    try:
        response = client.models.generate_content(
            model=LLM_MODEL_NAME,
            contents=full_prompt,
            config=config,
        )

        answer_text = (response.text or "").strip()
        # Limpieza básica de formato
        answer_text = answer_text.replace("**", "")

        prompt_tokens = 0
        completion_tokens = 0
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            prompt_tokens = (
                getattr(usage, "prompt_token_count", None)
                or getattr(usage, "promptTokenCount", 0)
            )
            completion_tokens = (
                getattr(usage, "candidates_token_count", None)
                or getattr(usage, "candidatesTokenCount", 0)
            )

        return answer_text, int(prompt_tokens or 0), int(completion_tokens or 0)

    except Exception as e:
        # Log a consola, pero no rompas la experiencia del usuario
        print("[LLM] Error llamando a Gemini:", repr(e))
        fallback = (
            "En este momento no puedo generar una respuesta completa. "
            "Intenta de nuevo en unos minutos, o consulta a tu profesora o profesor."
        )
        return fallback, 0, 0
