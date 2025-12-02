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
  
Para levantar los servicios: `docker compose up --build` y el bot desde el archivo `telegram_bot.py` (ruta /backend/telegram_bot.py), mediante la sentencia `python telegram_bot.py`.

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

## 3. Endpoints clave del backend

### 3.1. Salud

- GET /api/v1/health (devuelve estado del backend)

Ejemplo:

```
{
  "status": "ok"
}
```

### 3.2. Interacción con usuarios

- POST /api/v1/messages (recibe un mensaje de Telegram y lo procesa)

Ejemplo:

```
{
  "telegram_id": 123456789,
  "role": "student",
  "command": "/tarea",
  "text": "/tarea Estudiar para la prueba de fracciones del lunes",
  "course_id": null,
  "settings": {
    "modo": "baja",
    "experiment_tag": "pilot_docentes_2025S1",
    "extra": {"colegio": "Liceo X", "curso": "8B"}
  }
}
```

Respuesta esperada:

```
{
  "reply_text": "Plan sugerido...",
  "case_id": "CU1",
  "used_rag": false,
  "used_cag": true,
  "sensitive_flag": false
}
```

Cada llamada se registra en la tabla `interaction_logs`.

### 3.3. Búsqueda de documentos

- GET /api/v1/rag/search (realiza una búsqueda en los documentos ingestados)

Ejemplo:

```
{
  "query": "¿Qué es la inclusión educativa?",
  "experiment_tag": "pilot_docentes_2025S1",
  "extra": {"colegio": "Liceo X", "curso": "8B"}
}
```

Respuesta esperada:

```
{
  "results": [
    {
      "title": "Inclusión educativa",
      "content": "La inclusión educativa es...",
      "source": "https://www.mineduc.cl/...",
      "score": 0.9
    },
    ...
  ]
}
```

Cada búsqueda se registra en la tabla `rag_logs`.

### 3.4. Alertas

- GET /api/v1/alerts (obtiene alertas para un usuario)

Ejemplo:

```
{
  "telegram_id": 123456789,
  "role": "student",
  "experiment_tag": "pilot_docentes_2025S1",
  "extra": {"colegio": "Liceo X", "curso": "8B"}
}
```

Respuesta esperada:

```
{
  "results": [
    {
      "title": "Inclusión educativa",
      "content": "La inclusión educativa es...",
      "source": "https://www.mineduc.cl/...",
      "score": 0.9
    },
    ...
  ]
}
```

Cada búsqueda se registra en la tabla `rag_logs`.

- POST /api/v1/alerts (crea una alerta para un usuario)

Ejemplo:

```
{
  "telegram_id": 123456789,
  "role": "student",
  "experiment_tag": "pilot_docentes_2025S1",
  "extra": {"colegio": "Liceo X", "curso": "8B"}
}
```

Respuesta esperada:

```
{
  "results": [
    {
      "title": "Inclusión educativa",
      "content": "La inclusión educativa es...",
      "source": "https://www.mineduc.cl/...",
      "score": 0.9
    },
    ...
  ]
}
```

- DELETE /api/v1/alerts (elimina una alerta para un usuario)

Ejemplo:

```
{
  "telegram_id": 123456789,
  "role": "student",
  "experiment_tag": "pilot_docentes_2025S1",
  "extra": {"colegio": "Liceo X", "curso": "8B"}
}
```

Respuesta esperada:

```
{
  "results": [
    {
      "title": "Inclusión educativa",
      "content": "La inclusión educativa es...",
      "source": "https://www.mineduc.cl/...",
      "score": 0.9
    },
    ...
  ]
}
```

### 3.5. Recordatorios

- GET /api/v1/reminders (obtiene recordatorios para un usuario)

Ejemplo:

```
{
  "telegram_id": 123456789,
  "role": "student",
  "experiment_tag": "pilot_docentes_2025S1",
  "extra": {"colegio": "Liceo X", "curso": "8B"}
}
```

Respuesta esperada:

```
{
  "results": [
    {
      "title": "Inclusión educativa",
      "content": "La inclusión educativa es...",
      "source": "https://www.mineduc.cl/...",
      "score": 0.9
    },
    ...
  ]
}
```

- POST /api/v1/reminders (crea un recordatorio para un usuario)

Ejemplo:

```
{
  "telegram_id": 123456789,
  "role": "student",
  "experiment_tag": "pilot_docentes_2025S1",
  "extra": {"colegio": "Liceo X", "curso": "8B"}
}
```

Respuesta esperada:

```
{
  "results": [
    {
      "title": "Inclusión educativa",
      "content": "La inclusión educativa es...",
      "source": "https://www.mineduc.cl/...",
      "score": 0.9
    },
    ...
  ]
}
```

- DELETE /api/v1/reminders (elimina un recordatorio para un usuario)

Ejemplo:

```
{
  "telegram_id": 123456789,
  "role": "student",
  "experiment_tag": "pilot_docentes_2025S1",
  "extra": {"colegio": "Liceo X", "curso": "8B"}
}
```

Respuesta esperada:

```
{
  "results": [
    {
      "title": "Inclusión educativa",
      "content": "La inclusión educativa es...",
      "source": "https://www.mineduc.cl/...",
      "score": 0.9
    },
    ...
  ]
}
```

## 4. Comandos del bot de Telegram por rol

El adapter `telegram_bot.py` hace long polling y mapea comandos a llamadas HTTP al backend (o a endpoints específicos de alertas). Los roles se guardan en `users.role` y en memoria (`user_roles`).

### 4.1. Comandos por rol

- `/soy_estudiante` -> role = student
- `/soy_docente` -> role = teacher
- `/soy_apoderado` -> role = caregiver
- `/soy_coordinador` o `/soy_coordinadora` -> role = coordinator

### 4.2. Comandos comunes

- `/start`, mensaje de bienvenida adaptado al rol actual
- `/ayuda`, lista de comandos disponibles adaptados al rol actual

### 5.3. Estudiantes

- `/tarea + descripción` -> CU1: planificación de tareas y estudio, detectando: tarea puntual, preparación de prueba/control.
- `/explicacion + tema` -> CU2: explicación guiada con andamiaje, plantilla específica (ciclo del agua, fotosíntensis) y modos adicionales ("repaso rápido", "con ejercicios")

### 5.4. Docentes

- `/fuente + texto` -> CU7: búsqueda de fragmentos normativos / inclusión (RAG).
- `/resumen + tema` -> CU3: pre-resumen de documentos relevantes.
- `/quiz + tema` -> CU4: preguntas de evaluación formativa (base + ligadas a documentos).
- `/adaptar + descripción de actividad` -> CU5: adaptación con apoyos DUA (incluye sugerencias específicas para TEA, TDAH, dislexia cuando se mencionan).

### 5.5. Apoderados

- `/reporte_semana` -> CU6: reporte semanal v1. 

### 5.6. Coordinadores / convivencia (CU8)

- `/alertas`
Lista alertas pendientes (status = pending) desde teacher_alerts.

- `/detalle_alerta ID`
Muestra detalle y resumen de la alerta.

- `/alerta_en_revision ID`
Cambia estado a in_review.

- `/alerta_resuelta ID`
Cambia estado a resolved.

Nota: la detección de mensajes sensibles (riesgo de autolesión, violencia, etc.) se hace en el backend antes de responder al estudiante, y crea registros en teacher_alerts. El `bot NUNCA da diagnósticos`; solo genera mensajes de contención y derivación a adultos responsables.

## 6. RAG

El módulo `rag_service` maneja:
- Ingesta de PDFs (MINEDUC, UNESCO, Ley de Autismo, DUA, currículum, etc.)

Uso típico: `python backend/scripts/ingest_inclusion_batch.py`
- Indexación en documents y document_chunks (texto + embeddings).

- Búsqueda:
`search_snippets(query, filters, k) → usado por /fuente, /resumen, /quiz, /adaptar.`
`search_documents(query, filters) → para citar documentos relevantes.`

- Metadatos relevantes de documentos:
`doc_type` (ej. normativa_nacional, normativa_internacional, inclusion_autismo, paec, curriculo…)
`subject`, `year`, `source`, `metadata` -> tags.

## 7. Logging

Cada interacción que pasa por `/api/v1/messages` se registra en `interaction_logs`:

- Identificación: `user_id`, `role`, `course_id`
- Caso de uso: `command`, `case_id` (CU1…CU8)
- Técnica: `latency_ms`, `used_rag`, `used_cag`, `sensitive_flag`
- Contenido: `raw_query`, `raw_reply`

Investigación:
- `experiment_tag` → etiqueta de piloto/estudio (ej. "pilot_docentes_2025S1").
- `extra` (JSONB) → datos adicionales (colegio, curso, cohorte, etc.).

Ejmplos de consultas útiles:

```sql
-- uso por caso de estudio
SELECT experiment_tag, case_id, COUNT(*) 
FROM interaction_logs
GROUP BY experiment_tag, case_id
ORDER BY experiment_tag, case_id;

-- uso por rol y comando
SELECT role, command, COUNT(*) 
FROM interaction_logs
GROUP BY role, command
ORDER BY role, command;

```
