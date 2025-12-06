from typing import List
import os

from FlagEmbedding import BGEM3FlagModel  # nuevo import

_model = None


def _get_model() -> BGEM3FlagModel:
    """
    Carga perezosa del modelo de embeddings BGE-M3.
    - Modelo por defecto: 'BAAI/bge-m3'
    - Dimensión de salida (dense_vecs): 1024
    """
    global _model
    if _model is None:
        model_name = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3")
        # True si tienes GPU; en CPU dejar en False
        use_fp16 = os.getenv("EMBEDDING_USE_FP16", "false").lower() == "true"
        _model = BGEM3FlagModel(model_name, use_fp16=use_fp16)
    return _model


def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Dada una lista de textos (frases, párrafos, etc.), devuelve
    una lista de vectores list[float] aptos para pgvector.

    En BGE-M3 usamos la salida 'dense_vecs', de 1024 dimensiones.
    """
    if not texts:
        return []

    model = _get_model()

    # encode devuelve un dict con 'dense_vecs', 'sparse_vecs', 'colbert_vecs', etc.
    out = model.encode(
        texts,
        batch_size=16,
        max_length=512,
    )
    dense_vecs = out["dense_vecs"]

    # Aseguramos list[float] (pgvector espera una lista de floats)
    result: List[List[float]] = []
    for v in dense_vecs:
        if hasattr(v, "tolist"):
            v = v.tolist()
        result.append([float(x) for x in v])
    return result


def vector_to_str(vec: List[float]) -> str:
    """
    Convierte un vector en la representación textual esperada por pgvector.
    Ej: [0.1, 0.2] -> "[0.100000,0.200000]"
    """
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
