# backend/app/llm_client.py
import os
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

GRANITE_MODEL_NAME = os.getenv("GRANITE_MODEL_PATH", "ibm-granite/granite-4.0-h-1b")

_model = None
_tokenizer = None
_device = "cuda" if torch.cuda.is_available() else "cpu"


def _get_model_and_tokenizer():
    global _model, _tokenizer
    if _model is None or _tokenizer is None:
        _tokenizer = AutoTokenizer.from_pretrained(GRANITE_MODEL_NAME)
        _model = AutoModelForCausalLM.from_pretrained(GRANITE_MODEL_NAME)
        _model.to(_device)
        _model.eval()
    return _model, _tokenizer


def generate_llm_answer(
    system_prompt: str,
    user_prompt: str,
    max_new_tokens: int = 512,
    temperature: float = 0.6,
):
    """
    Devuelve:
      - answer_text: str
      - prompt_tokens: int
      - completion_tokens: int
    """
    model, tokenizer = _get_model_and_tokenizer()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    chat_text = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )

    inputs = tokenizer(chat_text, return_tensors="pt").to(_device)

    prompt_tokens = inputs["input_ids"].shape[1]

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=True,
            temperature=temperature,
            pad_token_id=tokenizer.eos_token_id,
        )

    total_tokens = output_ids.shape[1]
    completion_tokens = total_tokens - prompt_tokens

    # Recortar solo la salida nueva
    generated_ids = output_ids[0, prompt_tokens:]
    answer_text = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()

    return answer_text, prompt_tokens, completion_tokens
