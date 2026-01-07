# backend/app/llm_client.py
import os
from typing import Tuple

from google import genai
from google.genai import types

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

_client = None
LLM_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        )
    return _client


def _call_gemini(client, full_prompt, config):
    return client.models.generate_content(
        model=LLM_MODEL_NAME,
        contents=full_prompt,
        config=config,
    )


def generate_llm_answer(system_prompt: str, user_prompt: str, max_new_tokens: int = 512, temperature: float = 0.6,
                        request_timeout_s: int = 60) -> Tuple[str, int, int]:
    client = _get_client()
    full_prompt = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}"

    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_new_tokens,
    )
    """
    Llama a Gemini y devuelve:
      - answer_text: str
      - prompt_tokens: int
      - completion_tokens: int

    En caso de error, devuelve un mensaje seguro y tokens = 0.
    """
    # Reintentos más bajos para no exceder timeout del bot
    max_retries = 1
    base_delay = 5.0

    for attempt in range(max_retries + 1):
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_call_gemini, client, full_prompt, config)
                response = fut.result(timeout=request_timeout_s)

            answer_text = (response.text or "").strip().replace("**", "")

            prompt_tokens = 0
            completion_tokens = 0
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_token_count", None) or getattr(usage, "promptTokenCount", 0)
                completion_tokens = getattr(usage, "candidates_token_count", None) or getattr(usage, "candidatesTokenCount", 0)

            return answer_text, int(prompt_tokens or 0), int(completion_tokens or 0)

        except FutTimeout:
            # Timeout duro: devolvemos fallback rápido
            return (
                "Estoy teniendo demoras para generar la respuesta. "
                "¿Puedes intentar nuevamente o reformular la pregunta en una frase más corta?",
                0,
                0,
            )
        except Exception as e:
            error_msg = str(e)

            # Cuota / rate limit: reintento corto
            if ("RESOURCE_EXHAUSTED" in error_msg or "429" in error_msg) and attempt < max_retries:
                import time, random
                sleep_time = base_delay * (2**attempt) + random.uniform(0, 1)
                time.sleep(sleep_time)
                continue

            break

    return (
        "En este momento no puedo generar una respuesta completa debido a alta demanda. "
        "Intenta de nuevo en unos minutos.",
        0,
        0,
    )