from typing import List
from sentence_transformers import SentenceTransformer

_model = None


def _get_model() -> SentenceTransformer:
    """
    Carga perezosa del modelo de embeddings.
    Usa 'all-MiniLM-L6-v2' (dimensión 384, relativamente liviano).
    """
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Devuelve una lista de vectores (lista de floats) para cada texto.
    """
    model = _get_model()
    vectors = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    return [v.tolist() for v in vectors]


def vector_to_str(vec: List[float]) -> str:
    """
    Convierte un vector en la representación textual esperada por pgvector.
    Ej: [0.1, 0.2] -> "[0.100000,0.200000]"
    """
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
