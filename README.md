# Asistente Escolar IA

Asistente Escolar IA es un prototipo de asistente conversacional con enfoque de **inclusión educativa** para contexto escolar chileno.  
Integra:

- Un **bot de Telegram** para interacción con estudiantes, docentes, apoderados y equipos de convivencia.
- Un backend **FastAPI** con lógica de casos de uso (CU1–CU8).
- Un módulo **RAG** (Retrieval-Augmented Generation) sobre documentos MINEDUC / UNESCO / inclusión.
- Una base de datos **PostgreSQL** para usuarios, interacciones y documentos.

> Proyecto en fase de investigación/piloto. No reemplaza atención clínica ni protocolos formales de los establecimientos.

---

## 1. Arquitectura general

Servicios principales:

- **backend (FastAPI)**  
  - Expuesto en `http://localhost:8000`  
  - Swagger: `http://localhost:8000/docs`
- **db (PostgreSQL)**  
  - Base de datos: `asistente_escolar_ia`
- **adminer**  
  - Cliente web para la BD: `http://localhost:8080`
- **telegram_bot**  
  - Proceso Python que hace *long polling* a Telegram y habla con el backend vía HTTP.

Flujo simplificado:

```
Usuario (Telegram)
      │
      ▼
Bot de Telegram  ──►  FastAPI (/api/v1/messages, /api/v1/rag/search, /api/v1/alerts/…)
      │                                 │
      │                                 ▼
      └──────────────────────────►  PostgreSQL
                                     • users
                                     • interaction_logs
                                     • documents / document_chunks
                                     • reminders
                                     • teacher_alerts


```

## 2. Estructura del proyecto

```
.
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI, casos de uso, endpoints
│   │   ├── db.py                  # Conexión a PostgreSQL
│   │   ├── rag_service.py         # Ingesta + búsqueda de documentos (RAG)
│   │   └── models.py / schemas.py # Pydantic, enums, etc. (según tu estructura)
│   ├── scripts/
│   │   └── ingest_inclusion_batch.py  # Ingesta de PDFs MINEDUC/UNESCO
│   ├── telegram_bot.py            # Bot Telegram (adapter)
│   ├── Dockerfile
│   └── .env.example / .env
├── db/
│   ├── init.sql                   # Creación de tablas y tipos
│   └── data/                      # Volumen de datos de Postgres
├── docker-compose.yml
└── README.md
```
