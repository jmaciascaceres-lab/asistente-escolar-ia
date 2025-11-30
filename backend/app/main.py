from enum import Enum
import time
import json
from typing import Optional, List
from .rag_service import ingest_document, search_documents

from fastapi import FastAPI
from pydantic import BaseModel

from .db import init_db, close_db, get_db


app = FastAPI(title="Asistente Escolar IA", version="0.1.0")


# ---------- Eventos de ciclo de vida ----------

@app.on_event("startup")
def on_startup():
    init_db()


@app.on_event("shutdown")
def on_shutdown():
    close_db()


# ---------- Modelos Pydantic ----------

class UserRole(str, Enum):
    student = "student"
    teacher = "teacher"
    caregiver = "caregiver"
    coordinator = "coordinator"


class MessageIn(BaseModel):
    """
    Mensaje normalizado que llega desde el bot (o desde pruebas en Swagger).
    """
    telegram_id: int
    role: UserRole
    command: str          # ej: "/tarea"
    text: str             # texto completo enviado por la persona
    course_id: Optional[int] = None   # por ahora opcional, se puede dejar en null
    settings: dict = {}                # ej: {"modo": "baja"}


class MessageOut(BaseModel):
    reply_text: str
    case_id: Optional[str] = None      # CU1..CU8
    used_rag: bool = False
    used_cag: bool = False
    sensitive_flag: bool = False

class RagQuery(BaseModel):
    query: str
    filters: dict = {}   # ej: {"doc_type": "normativa_nacional"}


class RagDocumentOut(BaseModel):
    id: int
    title: str
    doc_type: str
    source: Optional[str] = None
    subject: Optional[str] = None
    year: Optional[int] = None
    metadata: dict = {}


class RagSearchResult(BaseModel):
    query: str
    documents: List[RagDocumentOut]


class RagIngestRequest(BaseModel):
    title: str
    doc_type: str
    source: Optional[str] = None
    subject: Optional[str] = None
    year: Optional[int] = None
    metadata: dict = {}


class HealthResponse(BaseModel):
    status: str


# ---------- Endpoints básicos ----------

@app.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok")


@app.post("/api/v1/rag/ingest")
async def rag_ingest(req: RagIngestRequest):
    """
    Ingesta v0 de documentos para el RAG.
    No parsea PDFs aún; asume que entregas título, tipo y metadata (p.ej. resumen corto).
    """
    doc_id = ingest_document(
        title=req.title,
        doc_type=req.doc_type,
        source=req.source,
        subject=req.subject,
        year=req.year,
        metadata=req.metadata,
    )
    return {"status": "ok", "id": doc_id}


@app.post("/api/v1/rag/search", response_model=RagSearchResult)
async def rag_search(payload: RagQuery):
    """
    Búsqueda v0 sobre la tabla documents.
    Útil para probar que /explicar cite fuentes correctas.
    """
    docs = search_documents(payload.query, payload.filters)
    out_docs = [
        RagDocumentOut(
            id=d["id"],
            title=d["title"],
            doc_type=d["doc_type"],
            source=d["source"],
            subject=d["subject"],
            year=d["year"],
            metadata=d["metadata"] or {},
        )
        for d in docs
    ]
    return RagSearchResult(query=payload.query, documents=out_docs)


@app.post("/api/v1/messages", response_model=MessageOut)
async def handle_message(msg: MessageIn):
    """
    Punto central de orquestación por ahora:
    - upsert de usuario
    - mapeo comando+rol -> case_id
    - generación de respuesta placeholder
    - registro en interaction_logs
    """
    start = time.time()

    # Determinar case_id para logging
    case_id = infer_case_id(msg.role, msg.command)

    # Placeholder de lógica: genera respuesta básica según CU
    reply_text, used_rag, used_cag, sensitive_flag = generate_reply_stub(msg, case_id)

    latency_ms = int((time.time() - start) * 1000)

    # Persistir en BD
    with get_db() as conn:
        user_id = upsert_user(conn, msg)
        log_interaction(
            conn=conn,
            user_id=user_id,
            msg=msg,
            case_id=case_id,
            latency_ms=latency_ms,
            used_rag=used_rag,
            used_cag=used_cag,
            sensitive_flag=sensitive_flag,
            reply_text=reply_text,
        )

    return MessageOut(
        reply_text=reply_text,
        case_id=case_id,
        used_rag=used_rag,
        used_cag=used_cag,
        sensitive_flag=sensitive_flag,
    )


# ---------- Helpers de lógica / orquestador mínimo ----------

def infer_case_id(role: UserRole, command: str) -> Optional[str]:
    """
    Mapea (rol, comando) al case_id (CU1..CU8).
    Por ahora consideramos sólo los comandos principales.
    """
    cmd = command.strip().lower()

    if role == UserRole.student:
        if cmd == "/tarea":
            return "CU1"
        if cmd == "/explicar":
            return "CU2"
        # /recordatorio lo asociamos a CU1 (apoyo a planificación)
        if cmd == "/recordatorio":
            return "CU1"

    if role == UserRole.teacher:
        if cmd == "/fuente":
            return "CU7"
        if cmd == "/resumen":
            return "CU3"
        if cmd == "/quiz":
            return "CU4"
        if cmd == "/adaptar":
            return "CU5"

    if role == UserRole.caregiver:
        if cmd == "/reporte_semana":
            return "CU6"
        if cmd == "/pie":
            return "CU3"
        if cmd == "/apoyo":
            return "CU6"

    if role == UserRole.coordinator:
        if cmd == "/alertas":
            return "CU8"

    # comandos genéricos (/start, /ayuda, etc.) o no mapeados
    return None

def extract_task_description(msg: MessageIn) -> str:
    """
    Extrae la descripción de la tarea desde msg.text.
    Si el usuario escribió `/tarea ...`, se toma lo que viene después.
    """
    text = msg.text.strip()
    if text.startswith("/tarea"):
        rest = text[len("/tarea"):].strip()
        return rest or "tu tarea"
    return text or "tu tarea"

def generate_cu1_plan(msg: MessageIn) -> str:
    """
    Genera un plan de tarea simple, alineado con el enfoque de autorregulación
    y modo baja estimulación cuando se indique en settings.
    """
    desc = extract_task_description(msg)
    low_stim = msg.settings.get("modo") == "baja"

    header = "Plan básico para organizar tu tarea\n"
    header += f"Tarea: {desc}\n\n"

    if low_stim:
        # Versión sin emojis, frases directas y cortas
        steps = [
            "1) Lee la consigna una vez con calma.",
            "2) Subraya o anota 3 palabras clave de la tarea.",
            "3) Divide la tarea en 2 o 3 partes pequeñas.",
            "4) Elige solo la primera parte y trabaja 15 a 20 minutos.",
            "5) Haz una pausa corta de 5 minutos.",
            "6) Revisa lo que hiciste y marca lo que ya completaste.",
        ]
    else:
        # Versión un poco más expresiva, pero manteniendo claridad
        steps = [
            "1) Lee la consigna con atención y asegúrate de entender qué te piden.",
            "2) Anota 3 palabras clave de la tarea (por ejemplo: tema, formato, fecha).",
            "3) Divide la tarea en 2 o 3 partes pequeñas (inicio, desarrollo, cierre).",
            "4) Empieza solo por la primera parte y trabaja 20 minutos.",
            "5) Toma una pausa corta de 5 minutos y luego revisa lo avanzado.",
            "6) Marca en una lista qué partes ya completaste y qué falta por hacer.",
        ]

    plan = header + "\n".join(steps) + "\n\n" + \
        "Si quieres, puedes pedirme otro plan escribiendo de nuevo /tarea con más detalles."
    return plan

def extract_explanation_topic(msg: MessageIn) -> str:
    """
    Extrae el tema a explicar desde msg.text.
    Si el usuario escribió `/explicar ...`, se toma lo que viene después.
    """
    text = msg.text.strip()
    if text.startswith("/explicar"):
        rest = text[len("/explicar"):].strip()
        return rest or "este contenido"
    return text or "este contenido"

def generate_cu2_explanation(msg: MessageIn) -> str:
    """
    Explicación v0 para CU2:
    - Usa una 'semiregla' para guiar al estudiante a entender el tema.
    - Consulta el RAG v0 para poder citar al menos un documento relacionado.
    """
    topic = extract_explanation_topic(msg)
    low_stim = msg.settings.get("modo") == "baja"

    # Buscar documentos relacionados (idealmente curriculares o de la asignatura)
    docs = search_documents(topic, filters={"doc_type": "curriculo"})
    if not docs:
        # Si no hay curriculo, busca en cualquier tipo
        docs = search_documents(topic, filters={})

    ref_line = ""
    if docs:
        d = docs[0]
        fuente = d.get("source") or "fuente interna"
        ref_line = f"\n\nReferencia asociada en los documentos del colegio: «{d['title']}» ({fuente})."

    if low_stim:
        body = (
            f"Vamos a entender «{topic}» en pasos simples:\n\n"
            "1) Qué es: escribe en una frase corta qué entiendes por este tema.\n"
            "2) Para qué sirve: piensa en una situación concreta donde aparezca.\n"
            "3) Ejemplo: anota un ejemplo muy sencillo (puede ser de tu vida diaria).\n"
            "4) Duda principal: escribe una pregunta específica que todavía tengas.\n\n"
            "Si quieres, puedes mandarme tu frase y tu ejemplo y seguimos desde ahí."
        )
    else:
        body = (
            f"Intentemos comprender «{topic}» ordenando la idea en 3 partes:\n\n"
            "1) Definición: escribe con tus palabras qué es, evitando copiar literalmente.\n"
            "2) Propósito: piensa para qué sirve o por qué es importante en la asignatura.\n"
            "3) Ejemplo aplicado: inventa un ejemplo sencillo que conecte con algo de tu vida diaria.\n\n"
            "Luego puedes enviarme tu definición o ejemplo y te puedo ayudar a mejorarlos."
        )

    return body + ref_line

def generate_reply_stub(msg: MessageIn, case_id: Optional[str]):
    """
    Por ahora, genera textos simples según case_id para probar el flujo.
    Más adelante aquí se invocará RAG + CAG + Safety.
    """
    used_rag = False
    used_cag = False
    sensitive_flag = False

    if case_id == "CU1":
        used_cag = True
        reply_text = generate_cu1_plan(msg)
    elif case_id == "CU2":
        used_rag = True
        used_cag = True
        reply_text = generate_cu2_explanation(msg)
    elif case_id == "CU3":
        used_rag = True
        used_cag = True
        reply_text = (
            "Este mensaje se registró como CU3 (resumen con fuentes para docentes/familias).\n"
            "Luego aquí aparecerá un resumen de documentos de inclusión o normativa. [placeholder]"
        )
    elif case_id == "CU4":
        used_rag = True
        used_cag = True
        reply_text = (
            "Lo tomaré como CU4 (preguntas de evaluación formativa). [placeholder]"
        )
    elif case_id == "CU5":
        used_rag = True
        used_cag = True
        reply_text = (
            "Lo tomaré como CU5 (adaptación de texto con apoyos DUA). [placeholder]"
        )
    elif case_id == "CU6":
        used_cag = True
        reply_text = (
            "Este flujo corresponde a CU6 (reporte / apoyo a familias). [placeholder]"
        )
    elif case_id == "CU7":
        used_rag = True
        used_cag = True
        reply_text = (
            "Estás usando CU7 (consulta de normativa / convivencia / inclusión). [placeholder]"
        )
    elif case_id == "CU8":
        # Teacher-in-the-loop; en el futuro activará alertas
        sensitive_flag = False  # aquí luego se pondrá True cuando se detecte algo sensible
        reply_text = (
            "CU8 (teacher-in-the-loop / alertas). Listaré o gestionaré alertas. [placeholder]"
        )
    else:
        reply_text = (
            "Comando recibido pero sin caso de uso específico (CU) asociado todavía.\n"
            "Luego podré darte más opciones según tu rol."
        )

    return reply_text, used_rag, used_cag, sensitive_flag


# ---------- Helpers de BD ----------

def upsert_user(conn, msg: MessageIn) -> int:
    """
    Inserta o actualiza el usuario según telegram_id.
    Devuelve el id interno de la tabla users.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (telegram_id, role, course_id, settings)
            VALUES (%s, %s::user_role, %s, %s::jsonb)
            ON CONFLICT (telegram_id) DO UPDATE
            SET role = EXCLUDED.role,
                course_id = COALESCE(EXCLUDED.course_id, users.course_id),
                settings = EXCLUDED.settings,
                updated_at = NOW()
            RETURNING id;
            """,
            (
                msg.telegram_id,
                msg.role.value,
                msg.course_id,
                json.dumps(msg.settings),
            ),
        )
        row = cur.fetchone()
    return int(row[0])


def log_interaction(
    conn,
    user_id: int,
    msg: MessageIn,
    case_id: Optional[str],
    latency_ms: int,
    used_rag: bool,
    used_cag: bool,
    sensitive_flag: bool,
    reply_text: str,
):
    """
    Inserta un registro en interaction_logs.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO interaction_logs (
                user_id,
                role,
                course_id,
                command,
                case_id,
                latency_ms,
                used_rag,
                used_cag,
                sensitive_flag,
                raw_query,
                raw_reply
            )
            VALUES (%s, %s::user_role, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                user_id,
                msg.role.value,
                msg.course_id,
                msg.command,
                case_id,
                latency_ms,
                used_rag,
                used_cag,
                sensitive_flag,
                msg.text,
                reply_text,
            ),
        )
