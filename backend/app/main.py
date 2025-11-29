from enum import Enum
import time
import json
from typing import Optional

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


class HealthResponse(BaseModel):
    status: str


# ---------- Endpoints básicos ----------

@app.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok")


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
        reply_text = (
            "He recibido tu tarea y la marcaré como caso CU1 (planificación / autorregulación).\n"
            "Más adelante aquí te devolveré un plan paso a paso. [placeholder]"
        )
    elif case_id == "CU2":
        used_rag = True
        used_cag = True
        reply_text = (
            "Voy a tratar tu pregunta como CU2 (explicación adaptada de contenido).\n"
            "Pronto aquí combinaré materiales de clase + explicación simplificada. [placeholder]"
        )
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
