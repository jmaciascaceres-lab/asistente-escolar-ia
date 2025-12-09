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

    max_retries = 3
    base_delay = 20.0  # Segundos

    for attempt in range(max_retries + 1):
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
            error_msg = str(e)
            # Chequeo "naive" de errores de cuota (429 / Resource Exhausted)
            if "RESOURCE_EXHAUSTED" in error_msg or "429" in error_msg:
                if attempt < max_retries:
                    # Exponential backoff: 4s, 8s, 16s... + jitter
                    import time
                    import random

                    sleep_time = base_delay * (2**attempt) + random.uniform(0, 1)
                    print(
                        f"[LLM] Cuota excedida. Reintentando en {sleep_time:.2f}s (Intento {attempt+1}/{max_retries})"
                    )
                    time.sleep(sleep_time)
                    continue
                else:
                    print(f"[LLM] Se agotaron los reintentos. Error final: {error_msg}")
            else:
                # Si es otro error, no reintentamos (o podrías decidir hacerlo)
                print("[LLM] Error no recuperable o distinto a cuota:", error_msg)
                break

    fallback = (
        "En este momento no puedo generar una respuesta completa debido a alta demanda. "
        "Intenta de nuevo en unos minutos, o consulta a tu profesora o profesor."
    )
    return fallback, 0, 0
