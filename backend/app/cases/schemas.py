# backend/app/cases/schemas.py

from typing import List, Optional, Dict, Any
from pydantic import BaseModel


class SourceFragment(BaseModel):
    """
    Fragmento de texto explícito utilizado para responder.
    """
    doc_id: str
    title: Optional[str] = None
    score: float
    chunk: str
    metadata: Dict[str, Any] = {}


class RAGAnswer(BaseModel):
    """
    Respuesta del asistente + lista estructurada de fragmentos usados.
    """
    message: str
    sources: List[SourceFragment]
