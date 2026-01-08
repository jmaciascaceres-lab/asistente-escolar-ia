# Asistente Escolar IA

Asistente Escolar IA es un prototipo de asistente conversacional con enfoque de inclusión educativa para contexto escolar chileno. Integra:

- Un bot de Telegram para interacción con estudiantes, docentes, apoderados y equipos de convivencia.
- Un backend FastAPI con lógica de casos de uso (CU1-CU8).
- Un módulo RAG (Retrieval-Augmented Generation) sobre documentos MINEDUC / UNESCO / inclusión.
- Una base de datos PostgreSQL para usuarios, interacciones y documentos.
- Un LLM externo (Gemini) para generación de texto controlada.

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

```
# desde la raíz del repo
docker compose up --build   # levanta backend + db + adminer

# en otra terminal, dentro de backend/
cd backend
python telegram_bot.py      # inicia el bot de Telegram

```

Flujo simplificado: 

```
Usuario (Telegram)
      │
      ▼
Bot de Telegram ──► FastAPI (/api/v1/messages, /api/v1/rag/search, /api/v1/alerts/…)
      │                                │
      │                                ├──► RAG (search_documents / search_snippets)
      │                                │
      │                                └──► PostgreSQL
      │                                     • users
      │                                     • interaction_logs
      │                                     • documents / document_chunks
      │                                     • reminders
      │                                     • teacher_alerts
      │
      └───────────────◄────────── Respuesta de texto (sin Markdown)
```

### Características técnicas:

RAG
- Embeddings con all-MiniLM-L6-v2 (dim=384, sentence-transformers).
- Búsqueda vectorial en PostgreSQL usando pgvector.
- Documentos: normativa MINEDUC, Ley de Autismo, DUA, PAEC, currículum, etc.

LLM externo (Gemini)
- Modelo configurable (por defecto gemini-2.0-flash).
- Envoltura centralizada en llm_client.py.
- Casos de uso que lo usan: /explicar, /resumen, /quiz, /adaptar.
- System prompts diferenciados para estudiantes y docentes, y restricción explícita: no usar Markdown (para evitar **negritas** en Telegram).
- Si la llamada al LLM falla, se devuelve un mensaje seguro:
> "En este momento no puedo generar una respuesta, intenta de nuevo en unos minutos."
y se registran llm_prompt_tokens = 0 y llm_completion_tokens = 0.

Experiencia en Telegram
- Al recibir un comando que va al backend, el bot envía primero:
> "Estoy procesando tu solicitud, dame unos segundos..."
- Al final de cada respuesta añade una línea con la latencia completa medida desde el bot:
> "Tiempo de respuesta del asistente: X.Y segundos."

## 2. Estructura del proyecto

```
.
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI, casos de uso, endpoints y logging
│   │   ├── db.py                # Conexión a PostgreSQL (get_db, init_db, etc.)
│   │   ├── rag_service.py       # Búsqueda/ingesta de documentos (RAG)
│   │   ├── llm_client.py        # Cliente Gemini (LLM externo)
│   │   └── cases/
│   │       ├── cu2_explicar.py  # explicar_con_llm (CU2)
│   │       ├── cu3_resumen.py   # resumen_con_llm (CU3)
│   │       ├── cu4_quiz.py      # quiz_con_llm     (CU4)
│   │       └── cu5_adaptar.py   # adaptar_con_llm  (CU5)
│   ├── scripts/
│   │   └── ingest_inclusion_batch.py  # Ingesta de PDFs MINEDUC/UNESCO/Ley de Autismo
│   ├── telegram_bot.py          # Bot de Telegram (adapter)
│   ├── Dockerfile
│   └── .env.example             # Ejemplo de configuración
├── db/
│   ├── init.sql                 # Creación de tablas, tipos y extensiones (pgvector)
│   └── data/                    # Volumen de datos de Postgres
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

### 3.2. Mensajes desde el bot

- POST /api/v1/messages (recibe un mensaje desde el bot de Telegram y devuelve una respuesta)

Endpoint central de orquestación:
1. Upsert de usuario (users).
2. Mapeo (rol, comando) → case_id (CU1…CU8).
3. Generación de respuesta (RAG + LLM cuando aplica).
4. Registro en interaction_logs.

Request:
```
{
  "telegram_id": 123456789,
  "role": "teacher",
  "command": "/quiz",
  "text": "/quiz fracciones 6° básico",
  "course_id": null,
  "settings": {
    "experiment_tag": "pilot_docentes_2025S1",
    "extra": {
      "contexto": "taller_docentes",
      "pais": "Chile"
    }
  }
}
```

Respuesta:
```
{
  "reply_text": "Aquí hay algunas preguntas tipo quiz...",
  "case_id": "CU4",
  "used_rag": true,
  "used_cag": true,
  "sensitive_flag": false
}
```

### 3.3. RAG

- POST /api/v1/rag/ingest
  Ingesta v0 de documentos (título + metadatos). Se usa principalmente desde scripts.

- POST /api/v1/rag/search
  Búsqueda en documents (útil para pruebas directas del RAG).

Request:
```
{
  "query": "ciclo del agua 5° básico",
  "filters": {
    "doc_type": "curriculo"
  }
}
```

Respuesta (RagSearchResult):
```
{
  "query": "ciclo del agua 5° básico",
  "documents": [
    {
      "id": 42,
      "title": "OF-CM Matematicas 5° Básico 2018",
      "doc_type": "curriculo",
      "source": "Carga Batch",
      "subject": "Ciencias Naturales",
      "year": 2018,
      "metadata": {}
    }
  ]
}
```

### 3.4. Gestión de roles de usuario

- POST /api/v1/users/set_role
  Se llama desde el bot cuando el usuario escribe /soy_docente, /soy_apoderado, etc.

Body:
```
{
  "telegram_id": 123456789,
  "role": "teacher"
}
```

### 3.5. Alertas para equipos de convivencia (CU8)

Endpoints HTTP (usados por el bot):

- GET `/api/v1/alerts?status=pending`
  Lista las últimas alertas filtrando por estado (por defecto pending).

- GET `/api/v1/alerts/{alert_id}`
  Obtiene el detalle de una alerta.

- POST `/api/v1/alerts/{alert_id}/status`
  Actualiza el estado de una alerta (pending, in_review, resolved).

Las alertas se crean automáticamente cuando el backend detecta texto sensible en mensajes de estudiantes (riesgo de autolesión, violencia familiar, posible abuso, acoso escolar, etc.).

Los mensajes de contención para estudiantes están diseñados para:
- Validar que lo que cuenta es importante.
- Derivar a un adulto responsable del colegio.
- Recordar que el bot no es un canal de emergencia ni atención clínica.

### 3.6. Comandos del bot de Telegram por rol

El adapter telegram_bot.py mantiene en memoria un diccionario user_roles y sincroniza el rol en la tabla users.

#### 3.6.1. Selección de rol

- `/soy_estudiante` → role = "student"
- `/soy_docente` → role = "teacher"
- `/soy_apoderado` → role = "caregiver"
- `/soy_coordinador` o `/soy_coordinadora` → role = "coordinator"

#### 3.6.2. Comandos comunes

- `/start` → mensaje de bienvenida adaptado al rol actual.
- `/ayuda` → listado de comandos para el rol actual

#### 3.6.3. Estudiantes (rol student)

- `/tarea` + descripción → CU1
Planificación de tareas y estudio.

Detecta si es:
- tarea puntual,
- preparación de prueba/control, o
- plan semanal.

- `/explicar` + tema → CU2
Explicación guiada con andamiaje.
- Plantillas específicas para: ciclo del agua, fotosíntesis.
- Modos extra: “repaso rápido”, “con ejercicios/preguntas”.
- Usa RAG + LLM y cita al final:
> "Fuentes consultadas (no exhaustivas): …"

> Nota: en el piloto el foco está en docentes, pero estos comandos funcionan también para estudiantes

#### 3.6.4. Docentes (rol teacher)

- `/fuente` + texto → CU7
Búsqueda de fragmentos normativos / inclusión (RAG).
Ejemplo: situaciones PIE, recreo complejos, ajustes razonables, etc.

- `/resumen` + tema → CU3
Pre-resumen de snippets relevantes para preparar reuniones o clases.
Usa RAG + LLM para condensar ideas clave y cita documentos al final.

- `/quiz` + tema → CU4
Generación de preguntas de evaluación formativa:
- 3+ preguntas base (recuerdo, comprensión, aplicación).
- Preguntas conectadas a documentos curriculares.
- Usa RAG + LLM y termina con “Fuentes consultadas (no exhaustivas)…”.

- `/adaptar` + descripción de actividad → CU5
Adaptación de actividades con enfoque DUA:
- Sugerencias en: representación, acción/expresión, compromiso.
- Ajustes particulares si se menciona TEA, TDAH, dislexia, etc.
- Usa RAG + LLM y cita 1-2 documentos de inclusión/DUA.

#### 3.6.5. Apoderados (rol caregiver)

- `/pie` + pregunta → CU3 (vía lógica de resumen explicativo).
Explicaciones simples sobre PIE, inclusión educativa, apoyos, etc.

- `/apoyo` + situación → CU6
Orientaciones breves para acompañar el estudio y el bienestar (texto plano, sin LLM).

- `/reporte_semana` → CU6
Reporte semanal v1 del uso del asistente:
- Interacciones totales en últimos 7 días.
- Uso por comando / caso de uso.
- Recordatorios completados vs. pendientes (si los hay)

#### 3.6.6. Coordinadores (rol coordinator)

Comandos “conversacionales” sobre alertas:

- `/alertas`
Lista alertas pendientes (status = pending).

- `/detalle_alerta ID`
Muestra detalle (tipo, estado, id estudiante/curso, resumen de mensaje).

- `/alerta_en_revision ID`
Cambia estado a in_review.

- `/alerta_resuelta ID`
Cambia estado a resolved.

- `/alerta_ayuda ID`
(opcional según implementación) puede marcar la alerta para apoyo externo o notas internas.

> La decisión final siempre es del equipo humano. El asistente sólo apoya la revisión de texto y la organización de alertas.

## 4. RAG: documentos y búsquedas

Ingesta de PDFs
- Desde backend/scripts/ingest_inclusion_batch.py se procesan documentos (MINEDUC, UNESCO, PAEC, etc.).
- Se extrae texto, se trocea y se genera:
- `documents`
- `document_chunks` (texto + embeddings)

Búsqueda
- `search_snippets(query, filters, k)`
Devuelve fragmentos cortos + metadatos. Usado en: `/fuente`, `/resumen`, `/quiz`, `/adaptar`.
- `search_documents(query, filters)`
Devuelve documentos completos (para citar “Fuentes consultadas…”).

Metadatos relevantes de documentos
- `doc_type` → ej. normativa_nacional, normativa_internacional, inclusion_autismo, paec, curriculo, reglamento_interno…
- `subject`, `year`
- `source` (p.ej. "Carga Batch", "MINEDUC", "UNESCO")
- `metadata` (JSON) → tags adicionales.

## 5. Loggings y métricas de uso

Cada interacción procesada por /api/v1/messages se registra en interaction_logs:

Campos principales:

Identificación
- `user_id` (FK users)
- `role` (student, teacher, caregiver, coordinator)
- `course_id` (opcional)

Caso de uso
- `command` (ej. /quiz, /tarea)
- `case_id` (CU1…CU8 o NULL)

Técnico
- `latency_ms` (sólo backend)
- `used_rag` (bool)
- `used_cag` (bool, uso de LLM / generación)
- `sensitive_flag` (mensaje marcado como sensible)

Contenido (para análisis cualitativo controlado)
- `raw_query` (texto enviado)
- `raw_reply` (texto respondido)

Investigación
- `experiment_tag` → etiqueta de piloto/estudio (ej. "pilot_docentes_2025S1").
- `extra` (JSONB) → datos de contexto (colegio, curso, cohorte, etc.).

LLM
- `llm_model` → p.ej. "gemini-2.0-flash"
- `llm_prompt_tokens`
- `llm_completion_tokens`

Ejemplos de consultas:
```
-- Uso por caso de uso y experimento
SELECT experiment_tag, case_id, COUNT(*) AS n
FROM interaction_logs
GROUP BY experiment_tag, case_id
ORDER BY experiment_tag, case_id;

-- Uso por rol y comando
SELECT role, command, COUNT(*) AS n
FROM interaction_logs
GROUP BY role, command
ORDER BY role, command;

-- Latencia promedio por caso de uso
SELECT case_id,
       AVG(latency_ms) AS avg_latency_ms,
       COUNT(*)        AS n
FROM interaction_logs
GROUP BY case_id
ORDER BY case_id;

-- Tokens promedio consumidos por tipo de CU (sólo donde hubo LLM)
SELECT case_id,
       AVG(llm_prompt_tokens)     AS avg_prompt_tokens,
       AVG(llm_completion_tokens) AS avg_completion_tokens,
       COUNT(*)                   AS n
FROM interaction_logs
WHERE llm_model IS NOT NULL
GROUP BY case_id
ORDER BY case_id;

```

## 6. Variables de entorno

### 6.1. LLM (Gemini)

Configurar en el entorno del backend:
- `GEMINI_API_KEY` (obligatoria): clave de la API de Gemini.
- `GEMINI_MODEL_NAME` (opcional): nombre del modelo, por defecto gemini-2.0-flash.

### 6.2. Telegram

Usadas por telegram_bot.py:
- `TELEGRAM_BOT_TOKEN`: token del bot.
- `BACKEND_URL`: URL del endpoint /api/v1/messages.
- En Docker típico: http://backend:8000/api/v1/messages.
- En desarrollo local puro: http://localhost:8000/api/v1/messages.

### 6.3. PostgreSQL

- `DB_HOST`
- `DB_PORT`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`

## 7. Notas éticas / limitaciones

- El sistema está diseñado para apoyo educativo, no para diagnóstico clínico.
- La detección de texto sensible es heurística y conservadora; siempre se sugiere que la evaluación la realice un equipo humano.
- Las respuestas del LLM pasan por prompts con restricciones de seguridad y estilo, pero pueden ser incompletas o requerir criterio profesional del docente/equipo.
- Los identificadores usan telegram_id + metadatos contextuales; no se guardan nombres reales ni RUT en la base por defecto (esto se puede ajustar según protocolo ético de cada estudio/piloto).

## 8. Referencias técnicas

- FastAPI: https://fastapi.tiangolo.com/
- PostgreSQL: https://www.postgresql.org/
- pgvector: https://github.com/pgvector/pgvector
- sentence-transformers: https://github.com/UKPLab/sentence-transformers
- Docker: https://docs.docker.com/
- Docker Compose: https://docs.docker.com/compose/

## 9. Mantenimiento RAG

- `rag_maintenance.py`: script para mantenimiento de documentos y chunks.

Algunas sentencias:

- Listar 50 primeros documentos (sin filtros)
`python scripts/rag_maintenance.py list`

- Listar solo normativa nacional del batch inclusion_batch_v1.1
`python scripts/rag_maintenance.py list --doc-type normativa_nacional --batch-tag inclusion_batch_v1.1`

- Listar documentos cuyo filename contiene "LeyAutismo"
`python scripts/rag_maintenance.py list --filename-substr LeyAutismo`

- Borrar todos los documentos de un batch (ej: inclusion_batch_v1.1) SIN pedir confirmación
`python scripts/rag_maintenance.py delete-batch inclusion_batch_v1.1 --force`

- Borrar todos los documentos "legacy" sin batch_tag (lo que más te interesa para limpiar v0)
`python scripts/rag_maintenance.py delete-legacy`

Limpieza directamente desde la base de datos:

```sql
BEGIN;

TRUNCATE TABLE document_chunks RESTART IDENTITY CASCADE;
TRUNCATE TABLE documents RESTART IDENTITY CASCADE;

COMMIT;
```

## 10. Ingesta de documentos

- `ingest_inclusion_batch.py`: script para ingestar documentos de inclusión.

Algunas sentencias:

-- ¿Cuántos documentos se cargaron?
```sql
SELECT COUNT(*) AS total_docs FROM documents;
```

-- Documentos por tipo (normativa, inclusión, currículo, etc.)
```sql
SELECT doc_type, COUNT(*) 
FROM documents
GROUP BY doc_type
ORDER BY doc_type;
```

-- ¿Cuántos chunks hay?
```sql
SELECT COUNT(*) AS total_chunks FROM document_chunks;
```

-- Confirmar que TODO lo nuevo corresponde al batch_tag-1.0
```sql
SELECT source, COUNT(*) 
FROM documents
GROUP BY source
ORDER BY source;
```

-- Ver 5 documentos de ejemplo, para sanity check
```sql
SELECT id, title, doc_type, source
FROM documents
ORDER BY id
LIMIT 5;
```