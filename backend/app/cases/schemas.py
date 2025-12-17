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


class RagDocument(BaseModel):
    """
    Documento encontrado por el RAG.
    """
    id: int
    title: str
    doc_type: Optional[str] = None
    source: Optional[str] = None
    subject: Optional[str] = None
    year: Optional[int] = None
    url: Optional[str] = None
    metadata: Dict[str, Any] = {}


class RagSnippet(BaseModel):
    """
    Fragmento de texto encontrado por el RAG.
    """
    document_id: int
    document_title: str
    text: str
    # score/similarity puede seguir existiendo, pero NO lo mostraremos al usuario final
    score: Optional[float] = None
    url: Optional[str] = None