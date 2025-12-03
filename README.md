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

El sistema utiliza una arquitectura híbrida:
- RAG: embeddings con `all-MiniLM-L6-v2` y búsqueda en `PostgreSQL + pgvector` sobre documentos curriculares y de inclusión (MINEDUC, UNESCO, PAEC, etc.).
- LLM externo: generación de respuestas con `Gemini` (GEMINI_MODEL_NAME, por defecto `gemini-2.0-flash`), invocado desde `llm_client.py`.
- Todas las respuestas de los casos de uso `/explicar`, `/resumen`, `/quiz` y `/adaptar` combinan RAG + LLM y añaden un bloque de "Fuentes consultadas (no exhaustivas)" basado en los documentos recuperados.

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

## Variables de entorno:

### LLM (Gemini)

- `GOOGLE_API_KEY` (obligatoria): clave de la Gemini API.
- `GEMINI_MODEL_NAME` (opcional): nombre del modelo, por defecto `gemini-2.0-flash`.

### Telegram

- `TELEGRAM_BOT_TOKEN`: token del bot.
- `BACKEND_URL`: URL del endpoint `/api/v1/messages` (por defecto `http://backend:8000/api/v1/messages` en Docker).

### PostgreSQL

- `DB_HOST`: host del servidor de base de datos.
- `DB_PORT`: puerto del servidor de base de datos.
- `DB_NAME`: nombre de la base de datos.
- `DB_USER`: usuario de la base de datos.
- `DB_PASSWORD`: contraseña de la base de datos.

## Referencias

- [Documentación de FastAPI](https://fastapi.tiangolo.com/)
- [Documentación de Uvicorn](https://www.uvicorn.org/)
- [Documentación de PostgreSQL](https://www.postgresql.org/docs/)
- [Documentación de Docker](https://docs.docker.com/)
- [Documentación de Docker Compose](https://docs.docker.com/compose/)
- [Documentación de Python](https://docs.python.org/3/)
- [Documentación de Git](https://git-scm.com/docs)
- [Documentación de GitHub](https://docs.github.com/)
- [Documentación de GitHub Copilot](https://docs.github.com/en/copilot)

```
@misc{granite2025,
  author       = {{IBM Research}},
  title        = {Granite 4.0 Nano Language Models},
  year         = {2025},
  howpublished = {\url{https://github.com/ibm-granite/granite-4.0-nano-language-models}},
  note         = {Accessed: 2025-10-23}
}

@article{qwen3,
    title={Qwen3 Technical Report}, 
    author={An Yang and Anfeng Li and Baosong Yang and Beichen Zhang and Binyuan Hui and Bo Zheng and Bowen Yu and Chang Gao and Chengen Huang and Chenxu Lv and Chujie Zheng and Dayiheng Liu and Fan Zhou and Fei Huang and Feng Hu and Hao Ge and Haoran Wei and Huan Lin and Jialong Tang and Jian Yang and Jianhong Tu and Jianwei Zhang and Jianxin Yang and Jiaxi Yang and Jing Zhou and Jingren Zhou and Junyang Lin and Kai Dang and Keqin Bao and Kexin Yang and Le Yu and Lianghao Deng and Mei Li and Mingfeng Xue and Mingze Li and Pei Zhang and Peng Wang and Qin Zhu and Rui Men and Ruize Gao and Shixuan Liu and Shuang Luo and Tianhao Li and Tianyi Tang and Wenbiao Yin and Xingzhang Ren and Xinyu Wang and Xinyu Zhang and Xuancheng Ren and Yang Fan and Yang Su and Yichang Zhang and Yinger Zhang and Yu Wan and Yuqiong Liu and Zekun Wang and Zeyu Cui and Zhenru Zhang and Zhipeng Zhou and Zihan Qiu},
    journal = {arXiv preprint arXiv:2505.09388},
    year={2025}
}

@article{qwen2.5,
    title   = {Qwen2.5 Technical Report}, 
    author  = {An Yang and Baosong Yang and Beichen Zhang and Binyuan Hui and Bo Zheng and Bowen Yu and Chengyuan Li and Dayiheng Liu and Fei Huang and Haoran Wei and Huan Lin and Jian Yang and Jianhong Tu and Jianwei Zhang and Jianxin Yang and Jiaxi Yang and Jingren Zhou and Junyang Lin and Kai Dang and Keming Lu and Keqin Bao and Kexin Yang and Le Yu and Mei Li and Mingfeng Xue and Pei Zhang and Qin Zhu and Rui Men and Runji Lin and Tianhao Li and Tingyu Xia and Xingzhang Ren and Xuancheng Ren and Yang Fan and Yang Su and Yichang Zhang and Yu Wan and Yuqiong Liu and Zeyu Cui and Zhenru Zhang and Zihan Qiu},
    journal = {arXiv preprint arXiv:2412.15115},
    year    = {2024}
}

@article{qwen2,
    title   = {Qwen2 Technical Report}, 
    author  = {An Yang and Baosong Yang and Binyuan Hui and Bo Zheng and Bowen Yu and Chang Zhou and Chengpeng Li and Chengyuan Li and Dayiheng Liu and Fei Huang and Guanting Dong and Haoran Wei and Huan Lin and Jialong Tang and Jialin Wang and Jian Yang and Jianhong Tu and Jianwei Zhang and Jianxin Ma and Jin Xu and Jingren Zhou and Jinze Bai and Jinzheng He and Junyang Lin and Kai Dang and Keming Lu and Keqin Chen and Kexin Yang and Mei Li and Mingfeng Xue and Na Ni and Pei Zhang and Peng Wang and Ru Peng and Rui Men and Ruize Gao and Runji Lin and Shijie Wang and Shuai Bai and Sinan Tan and Tianhang Zhu and Tianhao Li and Tianyu Liu and Wenbin Ge and Xiaodong Deng and Xiaohuan Zhou and Xingzhang Ren and Xinyu Zhang and Xipin Wei and Xuancheng Ren and Yang Fan and Yang Yao and Yichang Zhang and Yu Wan and Yunfei Chu and Yuqiong Liu and Zeyu Cui and Zhenru Zhang and Zhihao Fan},
    journal = {arXiv preprint arXiv:2407.10671},
    year    = {2024}
}
```