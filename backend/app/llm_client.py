# backend/app/llm_client.py
import os
import json
import time
import random
from typing import Tuple, Optional, List
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

import requests

# Gemini
from google import genai
from google.genai import types as gemini_types

# OpenAI
from openai import OpenAI


# -------------------------
# Config general
# -------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_str(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


LLM_PROVIDER = _env_str("LLM_PROVIDER", "gemini").lower()

# Cadena explícita; si no existe, usamos primario + (openai, gemini) por defecto.
_chain_raw = _env_str("LLM_PROVIDER_CHAIN", "")
if _chain_raw:
    LLM_PROVIDER_CHAIN = [p.strip().lower() for p in _chain_raw.split(",") if p.strip()]
else:
    LLM_PROVIDER_CHAIN = [LLM_PROVIDER, "openai", "gemini"]

# Timeout por defecto y por proveedor
LLM_TIMEOUT_S = _env_int("LLM_TIMEOUT_S", 45)
OLLAMA_TIMEOUT_S = _env_int("OLLAMA_TIMEOUT_S", 20)
OPENAI_TIMEOUT_S = _env_int("OPENAI_TIMEOUT_S", 45)
GEMINI_TIMEOUT_S = _env_int("GEMINI_TIMEOUT_S", 45)

# Modelos
GEMINI_MODEL_NAME = _env_str("GEMINI_MODEL_NAME", "gemini-2.0-flash")
OPENAI_MODEL = _env_str("OPENAI_MODEL", "gpt-4o-mini")
OLLAMA_MODEL = _env_str("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_MAX_NEW_TOKENS = _env_int("OLLAMA_MAX_NEW_TOKENS", 256)

# Bases / keys
OLLAMA_BASE_URL = _env_str("OLLAMA_BASE_URL", "http://localhost:11434")
OPENAI_API_KEY = _env_str("OPENAI_API_KEY", "")
GOOGLE_API_KEY = _env_str("GOOGLE_API_KEY", "") or _env_str("GEMINI_API_KEY", "")

# Para mantener compatibilidad con imports existentes (lo usabas en llm_meta)
# Nota: esto representa el "modelo primario" esperado, pero el real se devuelve por llamada.
LLM_MODEL_NAME = {
    "gemini": GEMINI_MODEL_NAME,
    "openai": OPENAI_MODEL,
    "ollama": OLLAMA_MODEL,
}.get(LLM_PROVIDER, GEMINI_MODEL_NAME)


# -------------------------
# Helpers de clasificación de error
# -------------------------

def _is_rate_limit_error(msg: str) -> bool:
    m = (msg or "").lower()
    return ("429" in m) or ("rate limit" in m) or ("resource_exhausted" in m)


def _is_timeout_error(msg: str) -> bool:
    m = (msg or "").lower()
    return ("timeout" in m) or ("timed out" in m)


def _is_transient_error(msg: str) -> bool:
    # red / 5xx / gateway / connection resets
    m = (msg or "").lower()
    transient_markers = [
        "connection reset",
        "connection aborted",
        "temporary failure",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
        "502",
        "503",
        "504",
    ]
    return any(x in m for x in transient_markers)


# -------------------------
# Ollama (HTTP)
# -------------------------

def _ollama_chat(system_prompt: str, user_prompt: str, max_new_tokens: int, temperature: float, timeout_s: int) -> str:
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "stream": True,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {
            "temperature": temperature,
            "num_predict": max_new_tokens,
        },
    }

    r = requests.post(url, json=payload, timeout=(5, timeout_s))
    r.raise_for_status()
    data = r.json()

    # Formato típico: {"message": {"role": "...", "content": "..."}, ...}
    msg = (data or {}).get("message") or {}
    return (msg.get("content") or "").strip()


# -------------------------
# OpenAI (Responses API)
# -------------------------

def _openai_generate(system_prompt: str, user_prompt: str, max_new_tokens: int, temperature: float, timeout_s: int) -> Tuple[str, int, int]:
    """
    Usa OpenAI Responses API.
    - instructions: system message
    - input: user message
    - max_output_tokens: bound tokens
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY no configurada")

    client = OpenAI(api_key=OPENAI_API_KEY)

    # El Responses API soporta `instructions` (system) y `input` (string o lista). :contentReference[oaicite:2]{index=2}
    # El SDK expone response.output_text para texto final. :contentReference[oaicite:3]{index=3}
    def _call():
        return client.responses.create(
            model=OPENAI_MODEL,
            instructions=system_prompt,
            input=user_prompt,
            temperature=temperature,
            max_output_tokens=max_new_tokens,
        )

    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_call)
        resp = fut.result(timeout=timeout_s)

    text = (getattr(resp, "output_text", None) or "").strip()

    # tokens: depende del SDK/objeto; parse defensivo
    prompt_tokens = 0
    completion_tokens = 0
    usage = getattr(resp, "usage", None) or getattr(resp, "usage_metadata", None)
    if usage:
        prompt_tokens = getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0

    return text, int(prompt_tokens), int(completion_tokens)


# -------------------------
# Gemini (google-genai)
# -------------------------

_gemini_client = None

def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        if not GOOGLE_API_KEY:
            raise RuntimeError("GOOGLE_API_KEY / GEMINI_API_KEY no configurada")
        _gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    return _gemini_client


def _gemini_generate(system_prompt: str, user_prompt: str, max_new_tokens: int, temperature: float, timeout_s: int) -> Tuple[str, int, int]:
    client = _get_gemini_client()
    full_prompt = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}"

    config = gemini_types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_new_tokens,
    )

    def _call():
        return client.models.generate_content(
            model=GEMINI_MODEL_NAME,
            contents=full_prompt,
            config=config,
        )

    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_call)
        resp = fut.result(timeout=timeout_s)

    answer_text = (resp.text or "").strip().replace("**", "")

    prompt_tokens = 0
    completion_tokens = 0
    usage = getattr(resp, "usage_metadata", None)
    if usage is not None:
        prompt_tokens = (
            getattr(usage, "prompt_token_count", None)
            or getattr(usage, "promptTokenCount", 0)
            or 0
        )
        completion_tokens = (
            getattr(usage, "candidates_token_count", None)
            or getattr(usage, "candidatesTokenCount", 0)
            or 0
        )

    return answer_text, int(prompt_tokens), int(completion_tokens)


# -------------------------
# Función pública (con fallback)
# -------------------------

def generate_llm_answer(
    system_prompt: str,
    user_prompt: str,
    max_new_tokens: int = 512,
    temperature: float = 0.6,
) -> Tuple[str, int, int, str]:
    """
    Intenta proveedores en orden (LLM_PROVIDER_CHAIN).
    Devuelve:
      - answer_text
      - prompt_tokens
      - completion_tokens
      - model_label (ej: "openai:gpt-4o-mini" | "gemini:gemini-2.0-flash" | "ollama:llama3.1:8b")
    """
    providers = LLM_PROVIDER_CHAIN[:] if LLM_PROVIDER_CHAIN else [LLM_PROVIDER]

    last_error: Optional[str] = None

    for p in providers:
        p = (p or "").strip().lower()
        try:
            if p == "ollama":
                t = _ollama_chat(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_new_tokens=OLLAMA_MAX_NEW_TOKENS,
                    temperature=temperature,
                    timeout_s=min(OLLAMA_TIMEOUT_S, LLM_TIMEOUT_S),
                )
                if t:
                    return t, 0, 0, f"ollama:{OLLAMA_MODEL}"
                raise RuntimeError("Ollama devolvió respuesta vacía")

            if p == "openai":
                text, pt, ct = _openai_generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    timeout_s=min(OPENAI_TIMEOUT_S, LLM_TIMEOUT_S),
                )
                if text:
                    return text, pt, ct, f"openai:{OPENAI_MODEL}"
                raise RuntimeError("OpenAI devolvió respuesta vacía")

            if p == "gemini":
                text, pt, ct = _gemini_generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    timeout_s=min(GEMINI_TIMEOUT_S, LLM_TIMEOUT_S),
                )
                if text:
                    return text, pt, ct, f"gemini:{GEMINI_MODEL_NAME}"
                raise RuntimeError("Gemini devolvió respuesta vacía")

            # Proveedor desconocido
            raise RuntimeError(f"Proveedor no soportado: {p}")

        except FutTimeout:
            last_error = f"{p}: timeout"
            print(f"[LLM:{p}] Timeout -> fallback al siguiente proveedor")
            continue

        except Exception as e:
            emsg = str(e) if e else "error"
            last_error = f"{p}: {emsg}"

            # Si es rate limit / timeout / transitorio -> fallback
            if _is_rate_limit_error(emsg) or _is_timeout_error(emsg) or _is_transient_error(emsg):
                print(f"[LLM:{p}] Error recuperable ({emsg}) -> fallback")
                continue

            # Si es no recuperable, también hacemos fallback (para resiliencia),
            # pero dejamos traza para debug.
            print(f"[LLM:{p}] Error no recuperable ({emsg}) -> fallback")
            continue

    # Si todo falló:
    fallback = (
        "En este momento no puedo generar una respuesta completa (proveedores no disponibles o límites alcanzados). "
        "Intenta nuevamente en unos minutos o reformula la pregunta de forma más breve."
    )
    # incluimos error final en logs (no se expone al usuario)
    print(f"[LLM] Fallback final. Último error: {last_error}")
    return fallback, 0, 0, "none"
