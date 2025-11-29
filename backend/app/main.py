from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Asistente Escolar IA")

class HealthResponse(BaseModel):
  status: str

@app.get("/health", response_model=HealthResponse)
async def health_check():
  return HealthResponse(status="ok")
