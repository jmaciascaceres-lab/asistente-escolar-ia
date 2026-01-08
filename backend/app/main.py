from enum import Enum
import time, re, unicodedata
import json, os
from typing import Optional, List, Tuple, Dict    
from .rag_service import search_documents, search_snippets, ingest_document
from .utils_sources import _safe_excerpt

from .cases.cu2_explicar import explicar_con_llm
from .cases.cu3_resumen import resumen_con_llm
from .cases.cu4_quiz import quiz_con_llm
from .cases.cu5_adaptar import adaptar_con_llm

from app.embeddings import embed_texts

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .db import init_db, close_db, get_db

from datetime import datetime, timedelta

from fastapi import HTTPException

app = FastAPI(title="Asistente Escolar IA", version="0.1.0")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STIMULATION_DEFAULT = os.getenv("STIMULATION_DEFAULT", "alto").strip().lower()
if STIMULATION_DEFAULT not in ("alto", "bajo"):
    STIMULATION_DEFAULT = "alto"


# ---------- Eventos de ciclo de vida ----------

@app.on_event("startup")
def on_startup():
    init_db()

async def _warmup():    
    try:
        embed_texts(["warmup"])
        print("✅ Embeddings warmup OK")
    except Exception as e:
        print(f"⚠️ Embeddings warmup falló: {e}")

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
    command: Optional[str] = None   # 👈 antes era str
    text: str
    course_id: Optional[int] = None
    settings: dict = Field(default_factory=dict)


class MessageOut(BaseModel):
    reply_text: str
    case_id: Optional[str] = None      # CU1..CU8
    used_rag: bool = False
    used_cag: bool = False
    sensitive_flag: bool = False

class RagQuery(BaseModel):
    query: str
    filters: dict = Field(default_factory=dict)   # ej: {"doc_type": "normativa_nacional"}


class RagDocumentOut(BaseModel):
    id: int
    title: str
    doc_type: str
    source: Optional[str] = None
    subject: Optional[str] = None
    year: Optional[int] = Field(default=None)
    url: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class RagSearchResult(BaseModel):
    query: str
    documents: List[RagDocumentOut]


class RagIngestRequest(BaseModel):
    title: str
    doc_type: str
    source: Optional[str] = None
    subject: Optional[str] = None
    year: Optional[int] = Field(default=None)
    url: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str


class SetRoleRequest(BaseModel):
    telegram_id: int
    role: UserRole


class AlertSummary(BaseModel):
    alert_id: int
    created_at: str
    student_id: Optional[int]
    course_id: Optional[int]
    alert_type: str
    status: str


class AlertDetail(BaseModel):
    alert_id: int
    created_at: str
    student_id: Optional[int]
    course_id: Optional[int]
    alert_type: str
    status: str
    summary: str
    last_update: str


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
            url=d["url"],
            metadata=d["metadata"] or {},
        )
        for d in docs
    ]
    return RagSearchResult(query=payload.query, documents=out_docs)


@app.post("/api/v1/messages", response_model=MessageOut)
async def handle_message(msg: MessageIn):
    """
    Punto central de orquestación:
    - determina cmd/case_id
    - merge settings (persistentes por usuario)
    - soporta /modo baja|alta (persistente)
    - ejecuta CU correspondiente
    - safety + logging en interaction_logs
    """
    start = time.time()

    # 1) cmd/case_id temprano
    cmd, args = parse_cmd_and_args(msg)
    case_id = infer_case_id(msg.role, cmd)

    # 2) Merge settings con BD + soporte /modo (persistente)
    #    Nota: hacemos 2 fases DB (set/merge usuario) -> ejecutar CU -> log/safety
    with get_db() as conn:
        db_settings = get_user_settings(conn, msg.telegram_id)
        effective_settings = merge_settings(db_settings, msg.settings)

        # Si venía vacío, dejamos default explícito
        effective_settings.setdefault("modo", "alta")

        # 2.a) /modo baja|alta: set persistente y respuesta inmediata
        if cmd == "/modo":
            new_mode = parse_stimulation_mode(args)

            if not new_mode:
                current = effective_settings.get("modo", "alta")
                reply_text = (
                    "Uso: /modo baja | /modo alta\n"
                    "Ejemplos:\n"
                    "• /modo baja\n"
                    "• /modo alta\n"
                    f"Modo actual: {current}"
                )

                # registrar interacción (sin CU)
                msg_eff = msg.copy(update={"settings": effective_settings, "command": cmd})
                user_id = upsert_user(conn, msg_eff)
                log_interaction(
                    conn,
                    user_id=user_id,
                    role=msg_eff.role,
                    course_id=msg_eff.course_id,
                    command=cmd,
                    case_id=None,
                    latency_ms=int((time.time() - start) * 1000),
                    used_rag=False,
                    used_cag=False,
                    sensitive_flag=False,
                    raw_query=msg_eff.text,
                    raw_reply=reply_text,
                    experiment_tag=effective_settings.get("experiment_tag"),
                    extra=effective_settings.get("extra"),
                    llm_model=None,
                    llm_prompt_tokens=None,
                    llm_completion_tokens=None,
                )

                return MessageOut(
                    reply_text=reply_text,
                    case_id=None,
                    used_rag=False,
                    used_cag=False,
                    sensitive_flag=False,
                )

            # set efectivo + persistir
            effective_settings["modo"] = new_mode

            msg_eff = msg.copy(update={"settings": effective_settings, "command": cmd})
            user_id = upsert_user(conn, msg_eff)

            reply_text = (
                f"Listo. Activé el modo de {'baja' if new_mode == 'baja' else 'alta'} estimulación.\n"
                "A partir de ahora, mis respuestas se ajustarán a este modo.\n"
                "Puedes cambiarlo cuando quieras con /modo baja o /modo alta."
            )

            log_interaction(
                conn,
                user_id=user_id,
                role=msg_eff.role,
                course_id=msg_eff.course_id,
                command=cmd,
                case_id=None,
                latency_ms=int((time.time() - start) * 1000),
                used_rag=False,
                used_cag=False,
                sensitive_flag=False,
                raw_query=msg_eff.text,
                raw_reply=reply_text,
                experiment_tag=effective_settings.get("experiment_tag"),
                extra=effective_settings.get("extra"),
                llm_model=None,
                llm_prompt_tokens=None,
                llm_completion_tokens=None,
            )

            return MessageOut(
                reply_text=reply_text,
                case_id=None,
                used_rag=False,
                used_cag=False,
                sensitive_flag=False,
            )

        # 2.b) flujo normal: persistimos settings mergeados ANTES del CU
        msg_eff = msg.copy(update={"settings": effective_settings, "command": cmd})
        user_id = upsert_user(conn, msg_eff)

    # 3) Ejecutar CU (ya con settings efectivos)
    (
        reply_text,
        used_rag,
        used_cag,
        sensitive_flag,
        llm_model,
        llm_prompt_tokens,
        llm_completion_tokens,
    ) = generate_reply_stub(msg_eff, case_id, cmd)

    latency_ms = int((time.time() - start) * 1000)

    # 4) Safety + logging (segunda fase DB)
    with get_db() as conn:
        sensitive_flag_override, reply_override = handle_safety_and_alerts(conn, user_id, msg_eff)
        if reply_override:
            reply_text = reply_override
        if sensitive_flag_override:
            sensitive_flag = True

        experiment_tag = msg_eff.settings.get("experiment_tag")
        extra = msg_eff.settings.get("extra")

        log_interaction(
            conn,
            user_id=user_id,
            role=msg_eff.role,
            course_id=msg_eff.course_id,
            command=cmd,
            case_id=case_id,
            latency_ms=latency_ms,
            used_rag=used_rag,
            used_cag=used_cag,
            sensitive_flag=sensitive_flag,
            raw_query=msg_eff.text,
            raw_reply=reply_text,
            experiment_tag=experiment_tag,
            extra=extra,
            llm_model=llm_model,
            llm_prompt_tokens=llm_prompt_tokens,
            llm_completion_tokens=llm_completion_tokens,
        )

    return MessageOut(
        reply_text=reply_text,
        case_id=case_id,
        used_rag=used_rag,
        used_cag=used_cag,
        sensitive_flag=sensitive_flag,
    )


@app.post("/api/v1/users/set_role")
async def set_user_role(req: SetRoleRequest):
    """
    Permite fijar/actualizar el rol de un usuario según su telegram_id.
    Lo usamos desde el bot cuando la persona dice /soy_docente, etc.
    """
    from .db import get_db  # ya lo tienes importado más arriba

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (telegram_id, role, course_id, settings)
                VALUES (%s, %s::user_role, NULL, '{}'::jsonb)
                ON CONFLICT (telegram_id) DO UPDATE
                SET role = EXCLUDED.role,
                    updated_at = NOW();
                """,
                (req.telegram_id, req.role.value),
            )
    return {"status": "ok"}

@app.get("/api/v1/alerts", response_model=List[AlertSummary])
async def list_alerts(status: Optional[str] = "pending"):
    with get_db() as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute(
                    """
                    SELECT id, created_at, student_id, course_id, alert_type, status
                    FROM teacher_alerts
                    WHERE status = %s
                    ORDER BY created_at DESC
                    LIMIT 20;
                    """,
                    (status,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, created_at, student_id, course_id, alert_type, status
                    FROM teacher_alerts
                    ORDER BY created_at DESC
                    LIMIT 20;
                    """
                )
            rows = cur.fetchall()

    return [
        AlertSummary(
            alert_id=r[0],
            created_at=r[1].isoformat(),
            student_id=r[2],
            course_id=r[3],
            alert_type=r[4],
            status=r[5],
        )
        for r in rows
    ]


@app.get("/api/v1/alerts/{alert_id}", response_model=AlertDetail)
async def get_alert(alert_id: int):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, created_at, student_id, course_id, alert_type, status, summary, last_update
                FROM teacher_alerts
                WHERE id = %s;
                """,
                (alert_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Alerta no encontrada")

    return AlertDetail(
        alert_id=row[0],
        created_at=row[1].isoformat(),
        student_id=row[2],
        course_id=row[3],
        alert_type=row[4],
        status=row[5],
        summary=row[6],
        last_update=row[7].isoformat() if row[7] else "",
    )

class AlertStatusUpdateRequest(BaseModel):
    status: str  # 'pending', 'in_review', 'resolved'

@app.post("/api/v1/alerts/{alert_id}/status")
async def update_alert_status(alert_id: int, req: AlertStatusUpdateRequest):
    """
    Actualiza el estado de una alerta.
    Estados sugeridos: pending, in_review, resolved.
    """
    valid_status = {"pending", "in_review", "resolved"}
    if req.status not in valid_status:
        raise HTTPException(status_code=400, detail="Estado no válido")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE teacher_alerts
                SET status = %s,
                    last_update = NOW()
                WHERE id = %s;
                """,
                (req.status, alert_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Alerta no encontrada")

    return {"status": "ok", "alert_id": alert_id, "new_status": req.status}


# ---------- Helpers de lógica / orquestador mínimo ----------

def get_user_settings_db(conn, telegram_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT settings FROM users WHERE telegram_id = %s;", (telegram_id,))
        row = cur.fetchone()
        return row[0] or {} if row else {}


def set_user_mode_db(conn, telegram_id: int, mode: str) -> None:
    mode = (mode or "").strip().lower()
    if mode not in ("alto", "bajo"):
        raise ValueError("modo inválido")
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE users
            SET settings = COALESCE(settings, '{}'::jsonb) || %s::jsonb,
                updated_at = NOW()
            WHERE telegram_id = %s;
            """,
            (json.dumps({"modo": mode}), telegram_id),
        )

def _normalize(text: str) -> str:
    # minúsculas
    t = text.lower()

    # quitar tildes/diacríticos
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))

    # colapsar caracteres repetidos (nooooo -> noo)
    t = re.sub(r"(.)\1{3,}", r"\1\1", t)

    # normalizar espacios
    t = re.sub(r"\s+", " ", t).strip()
    return t

def handle_safety_and_alerts(conn, user_id: int, msg: MessageIn) -> Tuple[bool, Optional[str]]:
    """
    Crea alertas si detecta texto sensible en mensajes de estudiantes.
    Devuelve (sensitive_flag, reply_override).
    """
    if msg.role != UserRole.student:
        return False, None

    cats = detect_sensitive_categories(msg.text)
    if not cats:
        return False, None

    alert_type = cats[0]
    summary = msg.text[:400]

    create_teacher_alert(conn, user_id, msg.course_id, alert_type, summary)

    # Mensaje muy general de contención, sin entrar en detalles clínicos
    reply = (
        "Gracias por contarme esto. Lo que estás viviendo es importante y no tienes que "
        "enfrentarlo solo.\n\n"
        "Voy a pedir a un adulto responsable de tu colegio que revise este mensaje para que "
        "pueda acompañarte de mejor forma. Si en este momento te sientes en peligro o muy mal, "
        "habla con una persona adulta de confianza lo antes posible."
    )

    return True, reply


def normalize_cmd(command: Optional[str]) -> str:
    """
    Normaliza comandos:
    - strip + lower
    - elimina sufijo @BotName (en grupos)
    """
    if not command:
        return ""
    cmd = command.strip().lower()
    if cmd.startswith("/"):
        cmd = cmd.split("@", 1)[0]
    return cmd


def parse_cmd_and_args(msg: "MessageIn") -> tuple[str, str]:
    """
    Devuelve (cmd_normalizado, args_str).
    - soporta command=None (Swagger) y /cmd@BotName (grupos)
    - args se toma desde msg.text (lo más estable)
    """
    cmd = effective_cmd(msg)  # ya normaliza /cmd@BotName
    text = (msg.text or "").strip()

    if not text:
        return cmd, ""

    # primer token del texto (podría venir /cmd@BotName)
    first = text.split()[0] if text.split() else ""
    first_norm = normalize_cmd(first)

    # si el primer token parece comando, úsalo como cmd efectivo
    if first_norm.startswith("/"):
        cmd = first_norm

    args = text[len(first):].strip() if first else ""
    return cmd, args


def parse_int_arg(args: str) -> Optional[int]:
    """
    Extrae un ID entero desde los args.
    Soporta "12" o "12," o "12.".
    """
    if not args:
        return None
    m = re.search(r"\b(\d+)\b", args)
    return int(m.group(1)) if m else None



def effective_cmd(msg: "MessageIn") -> str:
    """
    Comando robusto:
    - prioriza msg.command
    - si viene vacío, intenta parsearlo desde msg.text
    """
    cmd = normalize_cmd(msg.command)
    if cmd:
        return cmd

    text = (msg.text or "").strip()
    if text.startswith("/"):
        raw = text.split()[0]
        return normalize_cmd(raw)

    return ""


def infer_case_id(role: UserRole, command: str) -> Optional[str]:
    """
    Mapea (rol, comando) al case_id (CU1..CU8).
    Por ahora consideramos sólo los comandos principales.
    """
    cmd = command.strip().lower() if command else ""

    if role == UserRole.student:
        if cmd == "/tarea":
            return "CU1"
        if cmd == "/explicar":
            return "CU2"
        # /recordatorio lo asociamos a CU1 (apoyo a planificación)
        if cmd == "/recordatorio":
            return "CU1"
        # si quisieras, también podrías permitir /reporte_semana aquí:
        # if cmd == "/reporte_semana":
        #     return "CU6"

    if role in (UserRole.teacher, UserRole.coordinator):
        if cmd == "/fuente":
            return "CU7"
        if cmd == "/resumen":
            return "CU3"
        if cmd == "/quiz":
            return "CU4"
        if cmd == "/adaptar":
            return "CU5"
        if cmd == "/reporte_semana":
            return "CU6"

    if role == UserRole.caregiver:
        if cmd == "/reporte_semana":
            return "CU6"
        if cmd == "/pie":
            return "CU3"
        if cmd == "/apoyo":
            return "CU6"

    if role == UserRole.coordinator:
        if cmd in ("/alertas", "/detalle_alerta", "/alerta_en_revision", "/alerta_resuelta", "/alerta_ayuda"):
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
    CU1: planificación de tareas.
    Modos:
      - 'single': tarea puntual (por defecto)
      - 'exam': estudiar para prueba/control/examen
      - 'weekly': plan de estudio semanal
    """
    desc = extract_task_description(msg)
    low_stim = msg.settings.get("modo") == "baja"

    text_lc = msg.text.lower()
    if "semana" in text_lc or "semanal" in text_lc or "toda la semana" in text_lc:
        plan_type = "weekly"
    elif any(word in text_lc for word in ["prueba", "control", "examen"]):
        plan_type = "exam"
    else:
        plan_type = "single"

    if plan_type == "weekly":
        header = "Plan semanal para organizar tus estudios\n"
        header += f"Tema principal: {desc}\n\n"

        if low_stim:
            steps = [
                "Lunes: 20 minutos para leer o ver el contenido principal. Escribe 3 palabras clave.",
                "Martes: 20 minutos para hacer un esquema o dibujo simple con las ideas más importantes.",
                "Miércoles: 20 minutos para responder 3 preguntas sobre el tema (puedes pedírmelas con /quiz).",
                "Jueves: 20 minutos para revisar errores o dudas y preguntar a alguien si es posible.",
                "Viernes: repaso corto de 10 a 15 minutos: mira tu esquema y di en voz alta lo que recuerdas.",
            ]
        else:
            steps = [
                "Lunes: revisa el contenido general (apuntes, libro, guía). Anota 3 ideas centrales.",
                "Martes: construye un mapa conceptual o lista ordenada de subtemas.",
                "Miércoles: responde 3–5 preguntas sobre el tema (puedes generarlas con /quiz).",
                "Jueves: busca ejemplos o ejercicios y resuélvelos. Marca lo que no entiendes aún.",
                "Viernes: haz un repaso global de 15–20 minutos y verifica si puedes explicar el tema en voz alta.",
            ]

        plan = header + "\n".join(steps)
        plan += "\n\nSi quieres, puedes ajustar los días según tu realidad (por ejemplo, concentrar más tiempo el fin de semana)."
        return plan

    if plan_type == "exam":
        header = "Plan para preparar una prueba o control\n"
        header += f"Prueba sobre: {desc}\n\n"

        if low_stim:
            steps = [
                "Fase 1 (primer día): leer el contenido y subrayar 3 a 5 ideas importantes.",
                "Fase 2 (día siguiente): escribir un resumen muy corto (5 a 7 líneas) con esas ideas.",
                "Fase 3 (día antes de la prueba): responder 3 preguntas clave y revisar tu resumen.",
            ]
        else:
            steps = [
                "Fase 1 (2–3 días antes): repasa el contenido y organiza un esquema general.",
                "Fase 2 (1–2 días antes): responde preguntas tipo guía (puedes usar /quiz para generarlas).",
                "Fase 3 (día previo): repaso breve, dormir bien y evitar estudiar hasta muy tarde.",
            ]

        plan = header + "\n".join(steps)
        plan += "\n\nSi sabes la fecha exacta de la prueba, puedes distribuir estas fases en esos días."
        return plan

    # plan_type == "single" (tarea puntual)
    header = "Plan básico para organizar tu tarea\n"
    header += f"Tarea: {desc}\n\n"

    if low_stim:
        steps = [
            "1) Lee la consigna una vez con calma.",
            "2) Subraya o anota 3 palabras clave de la tarea.",
            "3) Divide la tarea en 2 o 3 partes pequeñas.",
            "4) Elige solo la primera parte y trabaja 15 a 20 minutos.",
            "5) Haz una pausa corta de 5 minutos.",
            "6) Revisa lo que hiciste y marca lo que ya completaste.",
        ]
    else:
        steps = [
            "1) Lee la consigna con atención y asegúrate de entender qué te piden.",
            "2) Anota 3 palabras clave de la tarea (tema, formato, fecha).",
            "3) Divide la tarea en 2 o 3 partes pequeñas (inicio, desarrollo, cierre).",
            "4) Empieza solo por la primera parte y trabaja 20 minutos.",
            "5) Toma una pausa corta de 5 minutos y luego revisa lo avanzado.",
            "6) Marca en una lista qué partes ya completaste y qué falta por hacer.",
        ]

    plan = header + "\n".join(steps)
    plan += "\n\nSi quieres, puedes pedirme otro plan escribiendo de nuevo /tarea con más detalles."
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

def _cu2_template_ciclo_agua(low_stim: bool) -> str:
    if low_stim:
        return (
            "Vamos a entender el «ciclo del agua» en pasos simples:\n\n"
            "1) El agua de ríos, mares o lagos se calienta con el sol y se convierte en vapor (evaporación).\n"
            "2) Ese vapor sube, se enfría y forma nubes (condensación).\n"
            "3) De las nubes cae agua en forma de lluvia, nieve o granizo (precipitación).\n"
            "4) Parte del agua vuelve a ríos y mares, y otra se filtra en el suelo (escorrentía e infiltración).\n\n"
            "Piensa en un ejemplo: un charco que desaparece después de un día soleado."
        )
    else:
        return (
            "El «ciclo del agua» describe cómo el agua se mueve de un lugar a otro cambiando de estado:\n\n"
            "- Evaporación: el agua líquida se calienta (por ejemplo, en mares o charcos) y pasa a vapor.\n"
            "- Condensación: el vapor se enfría en la atmósfera y forma nubes.\n"
            "- Precipitación: desde las nubes el agua cae como lluvia, nieve o granizo.\n"
            "- Escorrentía e infiltración: el agua que cae vuelve a ríos, mares o se filtra al suelo.\n\n"
            "Una buena forma de estudiarlo es dibujar un esquema con flechas que muestren esos pasos."
        )

def _cu2_template_fotosintesis(low_stim: bool) -> str:
    if low_stim:
        return (
            "La «fotosíntesis» es la forma en que las plantas producen su alimento:\n\n"
            "1) Las hojas reciben luz del sol.\n"
            "2) Las raíces toman agua del suelo.\n"
            "3) La planta toma dióxido de carbono del aire.\n"
            "4) Con la luz, el agua y el dióxido de carbono, la planta produce azúcar para alimentarse y libera oxígeno.\n\n"
            "Imagina una planta cerca de una ventana: usa la luz para vivir y crecer."
        )
    else:
        return (
            "La «fotosíntesis» es el proceso mediante el cual las plantas producen su propio alimento.\n\n"
            "En resumen:\n"
            "- Las hojas captan luz solar.\n"
            "- Las raíces absorben agua del suelo.\n"
            "- La planta toma dióxido de carbono (CO₂) del aire.\n"
            "- Dentro de las hojas, gracias a la clorofila, se transforma esa mezcla en azúcares (alimento) y se libera oxígeno (O₂).\n\n"
            "Puedes pensarla como una “fábrica” donde la luz es la energía que permite transformar agua y CO₂ en comida para la planta."
        )

def generate_cu2_explanation(msg: MessageIn) -> str:
    """
    CU2: explicación adaptada de contenido.
    - Plantillas específicas para algunos temas (ciclo del agua, fotosíntesis).
    - Modos extra:
        * repaso rápido (palabras: repaso, breve, rápido)
        * foco en ejercicios/preguntas (palabras: ejercicios, preguntas)
    - Siempre intenta citar al menos un documento relacionado vía RAG.
    """
    topic = extract_explanation_topic(msg)
    topic_lc = topic.lower()
    low_stim = msg.settings.get("modo") == "baja"

    review_mode = any(w in topic_lc for w in ["repaso", "breve", "rápido", "rapido"])
    exercise_mode = any(w in topic_lc for w in ["ejercicios", "preguntas"])

    # Intento de "limpiar" el tema sacando estas palabras
    clean_topic = topic
    for w in ["repaso", "breve", "rápido", "rapido", "ejercicios", "preguntas"]:
        clean_topic = clean_topic.replace(w, "").strip()
    if not clean_topic:
        clean_topic = topic

    # 1) Plantillas específicas
    if "ciclo del agua" in topic_lc:
        base_explanation = _cu2_template_ciclo_agua(low_stim)
    elif "fotosintesis" in topic_lc or "fotosíntesis" in topic_lc:
        base_explanation = _cu2_template_fotosintesis(low_stim)
    else:
        # Andamiaje genérico
        if low_stim:
            base_explanation = (
                f"Vamos a entender «{clean_topic}» en pasos simples:\n\n"
                "1) Qué es: escribe en una frase corta qué entiendes por este tema.\n"
                "2) Para qué sirve: piensa en una situación concreta donde aparezca.\n"
                "3) Ejemplo: anota un ejemplo muy sencillo (puede ser de tu vida diaria).\n"
                "4) Duda principal: escribe una pregunta específica que todavía tengas.\n\n"
                "Si quieres, puedes mandarme tu frase y tu ejemplo y seguimos desde ahí."
            )
        else:
            base_explanation = (
                f"Intentemos comprender «{clean_topic}» ordenando la idea en 3 partes:\n\n"
                "1) Definición: escribe con tus palabras qué es, evitando copiar literalmente.\n"
                "2) Propósito: piensa para qué sirve o por qué es importante en la asignatura.\n"
                "3) Ejemplo aplicado: inventa un ejemplo sencillo que conecte con algo de tu vida diaria.\n\n"
                "Luego puedes enviarme tu definición o ejemplo y te puedo ayudar a mejorarlos."
            )

    # 2) Bloque extra según modo
    extra = ""

    if review_mode:
        extra += (
            "\n\nPlan de repaso rápido:\n"
            "• Lee tu resumen o esquema sobre el tema.\n"
            "• Cubre el texto y trata de explicarlo en voz alta.\n"
            "• Revisa lo que olvidaste y márcalo para repasarlo otra vez.\n"
        )

    if exercise_mode:
        extra += (
            f"\n\nPreguntas para que te autoevalúes sobre «{clean_topic}»:\n"
            f"1) ¿Qué es lo primero que se te viene a la cabeza cuando piensas en «{clean_topic}»?\n"
            f"2) Explica un ejemplo donde aparezca «{clean_topic}» en tu vida diaria.\n"
            f"3) ¿Qué parte del tema te cuesta más entender y por qué?\n"
        )

    # 3) Referencias vía RAG (documentos relacionados)
    docs = search_documents(clean_topic, filters={"doc_type": "curriculo"})
    if not docs:
        docs = search_documents(clean_topic, filters={})

    ref_lines = ""
    if docs:
        d = docs[0]
        fuente = d.get("source") or "fuente interna"
        ref_lines += (
            f"\n\nReferencia asociada en los documentos del colegio: «{d['title']}» ({fuente})."
        )

    return base_explanation + extra + ref_lines


def generate_cu3_summary(msg: MessageIn) -> str:
    """
    Genera un 'pre-resumen' usando snippets de documentos relevantes.
    No es un resumen automático perfecto, sino una ayuda guiada para el docente.
    """
    query = extract_resumen_query(msg)
    low_stim = msg.settings.get("modo") == "baja"

    # 1) Buscar snippets relacionados. Priorizamos normativa/inclusión, luego currículo.
    preferred_types = ["normativa_nacional", "inclusion_autismo", "paec"]
    snippets = []
    for t in preferred_types:
        snippets = search_snippets(query, filters={"doc_type": t}, k=3)
        if snippets:
            break

    if not snippets:
        snippets = search_snippets(query, filters={}, k=3)

    if not snippets:
        return (
            f"Busqué fragmentos relacionados con «{query}» pero no encontré nada claro.\n"
            "Puede ser útil revisar directamente los documentos de inclusión o normativa del establecimiento."
        )

    intro = (
        f"Aquí tienes un pre-resumen de lo que dicen los documentos sobre «{query}».\n\n"
        "No reemplaza la lectura directa, pero puede ayudarte a preparar una reunión o clase:\n\n"
    )

    if low_stim:
        intro = (
            f"Resumen breve sobre «{query}» según los documentos cargados:\n\n"
        )

    lines = []
    for i, sn in enumerate(snippets, start=1):
        title = sn["title"]
        source = sn.get("source") or "fuente interna"
        doc_type = sn.get("doc_type") or ""
        content = sn["content"].replace("\n", " ")
        preview = content[:320] + ("..." if len(content) > 320 else "")
        lines.append(
            f"{i}) Documento: {title} ({source}, tipo: {doc_type}).\n"
            f"   Idea clave: {preview}\n"
        )

    cierre = (
        "\nSugerencia:\n"
        "- Marca las ideas que quieres compartir con el equipo o el curso.\n"
        "- Revisa el documento original para confirmar el contexto.\n"
        "- Si es un tema sensible, discútelo con el equipo de convivencia o inclusión."
    )

    if low_stim:
        cierre = (
            "\nRevisa el documento original antes de tomar decisiones importantes. "
            "Puedes usar estas ideas como notas rápidas para tu planificación."
        )

    return intro + "\n".join(lines) + cierre


def _pie_excerpt(text: str, max_chars: int = 260) -> str:
    t = re.sub(r"\s+", " ", (text or "")).strip()

    # Si parece empezar en un fragmento raro (chunk partido), intenta saltar el primer token muy corto
    first = t.split(" ", 1)[0] if t else ""
    if len(first) <= 2 and " " in t:
        t = t.split(" ", 1)[1].lstrip()

    if len(t) <= max_chars:
        return f"<<{t}>>"

    cut = t[: max_chars + 1]
    last_space = cut.rfind(" ")
    if last_space > int(max_chars * 0.6):
        cut = cut[:last_space].rstrip()

    cut = cut.rstrip(" ,;:.") + "…"
    return f"<<{cut}>>"


def generate_cu3_pie_for_caregiver(msg: MessageIn) -> str:
    """
    Versión de CU3 pensada para madres/padres/apoderados cuando usan /pie.
    Explica PIE e inclusión en lenguaje simple y cita 1 documento relevante.
    """
    query = extract_pie_query(msg)

    # Buscamos fragmentos normativos o de inclusión.
    preferred_types = {"normativa_nacional", "inclusion_autismo", "paec"}

    snippets_all = search_snippets(query, filters={}, k=8)
    snippets = [s for s in snippets_all if s.get("doc_type") in preferred_types][:1]

    if not snippets:
        snippets = search_snippets("Programa de Integración Escolar", filters={}, k=1)

    intro = (
        "Te respondo de forma general sobre el Programa de Integración Escolar (PIE) "
        "y la inclusión educativa.\n\n"
        "De manera sencilla, el PIE es un conjunto de apoyos que ofrece el colegio "
        "para que estudiantes con ciertas necesidades educativas reciban ayudas "
        "adicionales en su aprendizaje y participación.\n\n"
        "Algunas ideas clave:\n"
        "1) El objetivo es que tu hijo o hija pueda aprender y participar junto a su curso.\n"
        "2) Los apoyos pueden ser horas de profesionales, adaptaciones en actividades "
        "o materiales más accesibles.\n"
        "3) Las decisiones se toman en conjunto entre familia, escuela y profesionales.\n"
    )

    ref_lines = ""
    if snippets:
        sn = snippets[0]
        title = sn.get("title") or "Documento sin título"
        url = (sn.get("url") or "").strip()
        excerpt = _pie_excerpt(sn.get("content") or "")

        ref_lines = (
            "\nDocumento de referencia:\n"
            f"- {title}\n"
            + (f"  {url}\n" if url else "")
            + f"  Extracto: {excerpt}\n"
        )
    else:
        ref_lines = (
            "\nNo encontré en este momento un fragmento específico en los documentos cargados, "
            "pero el colegio debería tener a disposición el Proyecto de Integración Escolar y "
            "su reglamento interno, donde se explican los apoyos disponibles."
        )

    cierre = (
        "\nTe recomiendo:\n"
        "• Pedir una reunión con el profesor jefe o con el equipo PIE para conversar tu caso.\n"
        "• Solicitar que te expliquen qué apoyos concretos se están ofreciendo y cómo se evalúan.\n\n"
        "Este mensaje es solo una orientación general y no reemplaza la comunicación directa con el colegio "
        "ni con profesionales de salud o educación."
    )

    return intro + ref_lines + cierre


def generate_cu4_quiz(msg: MessageIn) -> str:
    """
    Genera un set de preguntas de evaluación formativa (CU4) a partir de un tema.
    Usa RAG para conectar algunas preguntas con documentos curriculares o de inclusión.
    """
    topic = extract_quiz_query(msg)
    low_stim = msg.settings.get("modo") == "baja"

    # 1) Recuperar snippets relacionados (primero currículo, luego lo demás)
    snippets = search_snippets(topic, filters={"doc_type": "curriculo"}, k=3)
    if not snippets:
        snippets = search_snippets(topic, filters={}, k=3)

    # 2) Preguntas base (recuerdo + comprensión + aplicación)
    base_questions = [
        f"1) Explica con tus palabras qué entiendes por «{topic}».",
        f"2) Menciona un ejemplo concreto donde se vea aplicado «{topic}».",
        f"3) ¿Por qué crees que «{topic}» es importante en la vida diaria o en tu comunidad?",
    ]

    # 3) Preguntas conectadas a documentos del colegio
    extra_lines = ""
    if snippets:
        q_lines = []
        for sn in snippets:
            title = sn["title"]
            source = sn.get("source") or "fuente interna"
            doc_type = sn.get("doc_type") or ""
            q_lines.append(
                f"- Según el documento «{title}» ({source}, tipo: {doc_type}), "
                f"¿qué idea importante rescatarías sobre «{topic}»?"
            )
        extra_lines = "\n\nPreguntas conectadas a documentos del colegio:\n" + "\n".join(q_lines)

    # 4) Tono según modo
    if low_stim:
        intro = f"Preguntas simples sobre «{topic}» para usar en clase:\n\n"
        cierre = (
            "\n\nÚsalas solo como guía y ajusta el nivel de dificultad según tu curso."
        )
    else:
        intro = (
            f"Propuesta de preguntas de evaluación formativa sobre «{topic}».\n"
            "Puedes ajustar el lenguaje y la dificultad según tu curso:\n\n"
        )
        cierre = (
            "\n\nSugerencia: puedes usar estas preguntas como salida rápida de la clase, "
            "en trabajo en parejas o como base para una rúbrica sencilla."
        )

    return intro + "\n".join(base_questions) + extra_lines + cierre

def generate_cu5_adaptation(msg: MessageIn) -> str:
    """
    CU5: Adaptar una actividad o texto incorporando apoyos DUA.
    - Usa la descripción de la actividad.
    - Propone apoyos de representación, acción/expresión y compromiso.
    - Ajustes razonables si detecta TEA, TDAH, dislexia, etc.
    - Cita 1–2 documentos relevantes de inclusión/DUA.
    """
    request = extract_adapt_request(msg)
    low_stim = msg.settings.get("modo") == "baja"
    req_lc = request.lower()

    # Detección muy simple de necesidades mencionadas
    has_tea = any(w in req_lc for w in ["tea", "autismo", "trastorno del espectro autista"])
    has_tdah = any(w in req_lc for w in ["tdah", "déficit atencional", "deficit atencional"])
    has_dislexia = any(w in req_lc for w in ["dislexia", "dificultades lectoras"])

    # 1) Cabecera
    header = "Propuesta de adaptación de actividad con enfoque DUA\n\n"
    header += f"Descripción que entregaste:\n«{request}»\n\n"

    # 2) Adaptación de la consigna (para todos los estudiantes)
    if low_stim:
        consigna = (
            "Ajustes en la consigna (para todo el curso):\n"
            "• Usa una sola instrucción por línea.\n"
            "• Resalta en negrita o subraya los verbos clave (por ejemplo: leer, subrayar, escribir).\n"
            "• Evita oraciones muy largas; corta en frases de 10–15 palabras.\n"
            "• Indica el tiempo estimado y el formato esperado de la respuesta.\n"
        )
    else:
        consigna = (
            "Sugerencias para adaptar la consigna (benefician a todo el curso):\n"
            "• Divide la instrucción en pasos numerados (1, 2, 3...).\n"
            "• Destaca los verbos clave y productos esperados (por ejemplo: “escribe un párrafo de 5 líneas”).\n"
            "• Evita detalles irrelevantes en la consigna; colócalos como ejemplos aparte.\n"
            "• Explicita el tiempo disponible y los criterios principales de logro.\n"
        )

    # 3) Apoyos DUA generales
    apoyos_generales = (
        "\nApoyos DUA sugeridos:\n"
        "• Representación: ofrece un ejemplo resuelto o modelo, usa esquemas o imágenes simples cuando sea posible.\n"
        "• Acción y expresión: permite que algunos estudiantes respondan con esquema, audio corto o viñetas en vez de solo texto largo.\n"
        "• Compromiso: cuando se pueda, deja que el estudiante elija entre 2 temas o ejemplos cercanos a su realidad.\n"
    )

    # 4) Ajustes razonables según necesidad
    ajustes_especificos = ""

    if has_tea:
        ajustes_especificos += (
            "\nAjustes sugeridos cuando se menciona TEA/autismo:\n"
            "• Anticipa la secuencia de la actividad (por ejemplo, muestra un pequeño listado visual con los pasos).\n"
            "• Reduce estímulos distractores en la hoja o pantalla (menos recuadros, menos colores fuertes).\n"
            "• Permite más tiempo para responder y, si es posible, un espacio más tranquilo.\n"
            "• Da la opción de que las instrucciones se repitan de manera clara y literal ante la duda.\n"
        )

    if has_tdah:
        ajustes_especificos += (
            "\nAjustes sugeridos cuando se menciona TDAH/déficit atencional:\n"
            "• Fragmenta la actividad en bloques de 10–15 minutos con pequeñas pausas guiadas.\n"
            "• Usa listas cortas de elementos por ítem (por ejemplo, 3 preguntas por hoja en vez de 10 juntas).\n"
            "• Permite el uso de organizadores gráficos para estructurar la respuesta.\n"
            "• Avisa con antelación cuánto tiempo falta para terminar.\n"
        )

    if has_dislexia:
        ajustes_especificos += (
            "\nAjustes sugeridos cuando se menciona dislexia/dificultades lectoras:\n"
            "• Usa letra clara y tamaño mayor (al menos 12–14 pt), con buen espaciado.\n"
            "• Evita párrafos muy densos; separa en bloques cortos con subtítulos.\n"
            "• Permite que alguien lea la consigna en voz alta o entrega audio de apoyo.\n"
            "• Ofrece más tiempo y reduce la cantidad de lectura no esencial.\n"
        )

    if not ajustes_especificos:
        ajustes_especificos = (
            "\nSi algún estudiante tiene necesidades específicas (por ejemplo, TEA, TDAH o dificultades lectoras), "
            "puede ser útil coordinar ajustes más personalizados con el equipo PIE o de inclusión."
        )

    # 5) Referencias desde RAG
    # Buscamos documentos de DUA/inclusión para citar al menos uno
    docs = search_documents("DUA", filters={"doc_type": "normativa_nacional"})
    if not docs:
        docs = search_documents("inclusion educativa", filters={})

    ref_lines = ""
    if docs:
        d = docs[0]
        fuente = d.get("source") or "fuente interna"
        ref_lines = (
            f"\n\nReferencia sugerida en los documentos del establecimiento: "
            f"«{d['title']}» ({fuente}). Revisa ese material para alinear la adaptación "
            "con las orientaciones oficiales."
        )

    cierre = (
        "\n\nRecuerda que estas son sugerencias iniciales. La decisión final sobre ajustes y adecuaciones "
        "debe tomarse en conjunto con el equipo del establecimiento, respetando la normativa vigente."
    )

    return header + consigna + apoyos_generales + ajustes_especificos + ref_lines + cierre


def generate_cu6_report(msg: MessageIn) -> str:
    """
    CU6: reporte semanal de uso del asistente.
    v1: mira las interacciones y recordatorios del propio usuario
    en los últimos 7 días. Si el rol es 'caregiver' ajusta el texto
    hablando de 'este chat' y 'hijo/hija'.
    """
    low_stim = msg.settings.get("modo") == "baja"
    telegram_id = msg.telegram_id

    with get_db() as conn:
        with conn.cursor() as cur:
            # 1) obtener id de usuario y rol en la tabla users
            cur.execute(
                "SELECT id, role FROM users WHERE telegram_id = %s;",
                (telegram_id,),
            )
            row = cur.fetchone()
            if not row:
                # texto distinto si es apoderado
                if low_stim:
                    return (
                        "Aún no tengo suficientes datos para mostrar un resumen semanal.\n"
                        "Vuelve a intentarlo después de usar el asistente algunos días."
                    )
                return (
                    "Todavía no tengo datos suficientes para generar un reporte semanal de uso.\n"
                    "Prueba nuevamente luego de unos días de usar el asistente."
                )

            user_id, role_db = row[0], row[1]

            # 2) interacciones últimos 7 días
            cur.execute(
                """
                SELECT command, case_id, COUNT(*) AS n
                FROM interaction_logs
                WHERE user_id = %s
                  AND timestamp >= NOW() - INTERVAL '7 days'
                GROUP BY command, case_id
                ORDER BY n DESC;
                """,
                (user_id,),
            )
            interactions = cur.fetchall()

            cur.execute(
                """
                SELECT COUNT(*)
                FROM interaction_logs
                WHERE user_id = %s
                  AND timestamp >= NOW() - INTERVAL '7 days';
                """,
                (user_id,),
            )
            total_interactions = cur.fetchone()[0]

            # 3) recordatorios (si los usas) últimos 7 días
            cur.execute(
                """
                SELECT
                  SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) AS completados,
                  SUM(CASE WHEN completed_at IS NULL THEN 1 ELSE 0 END) AS pendientes
                FROM reminders
                WHERE user_id = %s
                  AND due_at >= NOW() - INTERVAL '7 days';
                """,
                (user_id,),
            )
            row_rem = cur.fetchone()
            completed = row_rem[0] if row_rem[0] is not None else 0
            pending = row_rem[1] if row_rem[1] is not None else 0

    # 4) si no hay interacciones, mensaje especial para apoderado
    if total_interactions == 0:
        if role_db == "caregiver":
            return (
                "En los últimos 7 días no hay uso registrado del asistente desde este chat.\n"
                "Más adelante, cuando el colegio vincule este chat al estudiante, aquí verás "
                "un resumen del uso del asistente por parte de tu hijo o hija."
            )

        # resto de roles
        if low_stim:
            return (
                "En los últimos 7 días no hay uso registrado del asistente.\n"
                "Cuando empieces a usarlo más seguido, te podré mostrar un resumen."
            )
        return (
            "En los últimos 7 días no aparece uso registrado del asistente para este usuario.\n"
            "Cuando haya más actividad, podré generar un reporte semanal."
        )

    # 5) construir el resumen
    # primera línea: distinta si es apoderado
    if role_db == "caregiver":
        header = "Resumen de uso del asistente en este chat durante los últimos 7 días:\n"
    else:
        header = "Resumen de uso del asistente en los últimos 7 días:\n"

    lines = [header]
    lines.append(f"• Interacciones totales: {total_interactions}")

    if interactions:
        lines.append("• Detalle por comando/caso de uso:")
        for cmd, case_id, n in interactions:
            cu_label = case_id if case_id else "sin CU asignado"
            lines.append(f"   - {cmd} ({cu_label}): {n} veces")

    lines.append("\nRecordatorios asociados en la semana:")
    lines.append(f"• Recordatorios completados: {completed}")
    lines.append(f"• Recordatorios pendientes: {pending}")

    # 6) cierre distinto para apoderado vs otros
    if role_db == "caregiver":
        lines.append(
            "\nNota: por ahora este resumen considera solo el uso desde este chat. "
            "Cuando el colegio vincule este chat al estudiante, el reporte se ajustará para "
            "mostrar específicamente el uso de tu hijo o hija."
        )
    else:
        if low_stim:
            lines.append(
                "\nPuedes usar este resumen solo como referencia. "
                "Si quieres mejorar la organización, intenta usar /tarea al empezar cada trabajo."
            )
        else:
            lines.append(
                "\nPuedes usar este resumen como una foto rápida del uso del asistente.\n"
                "Si quieres apoyar mejor la organización, puede ser útil:\n"
                "• Animar a usar /tarea al planificar pruebas o trabajos.\n"
                "• Revisar juntos qué tipo de consultas se repiten más."
            )

    return "\n".join(lines)


def generate_cu6_apoyo_for_caregiver(msg: MessageIn) -> str:
    """
    CU6 para apoderados cuando usan /apoyo:
    orientaciones breves para acompañar el estudio y el bienestar.
    No entrega diagnósticos ni indicaciones clínicas.
    """
    situation = extract_apoyo_situation(msg)
    t = situation.lower()

    # Clasificación muy simple de tipo de consulta
    if any(w in t for w in ["prueba", "tarea", "estudi", "nota", "deber"]):
        tipo = "academico"
    elif any(w in t for w in ["triste", "ansioso", "ansiosa", "enojado", "enojada",
                              "no quiere ir al colegio", "no quiere ir a la escuela",
                              "no quiere ir al liceo"]):
        tipo = "emocional"
    else:
        tipo = "general"

    header = "Gracias por compartir la situación. Te propongo algunas orientaciones generales:\n\n"

    if tipo == "academico":
        cuerpo = (
            "1) Escuchar primero: pregúntale con calma qué es lo que más le cuesta de la asignatura o de la tarea.\n"
            "2) Ayudar a organizar: dividir el estudio en bloques cortos (15–20 minutos) con pequeñas pausas.\n"
            "3) Revisar indicaciones del colegio: mirar la guía, pauta o rúbrica que haya enviado la profesora o el profesor.\n"
            "4) Mantener comunicación: si las dificultades se repiten, pedir una reunión breve con el docente para coordinar apoyos.\n"
        )
    elif tipo == "emocional":
        cuerpo = (
            "1) Validar lo que siente: escuchar sin minimizar (“no es nada”) ni juzgar.\n"
            "2) Preguntar con calma: qué cosas han pasado en el curso o en el colegio que le preocupan.\n"
            "3) Acordar pasos concretos: por ejemplo, hablar con el profesor jefe o con convivencia escolar.\n"
            "4) Estar atento a señales de mayor preocupación: cambios bruscos de ánimo, aislamiento extremo u otras conductas inusuales.\n"
        )
    else:
        cuerpo = (
            "1) Conversar en un momento tranquilo sobre lo que está pasando.\n"
            "2) Identificar si la dificultad es más académica, social o emocional.\n"
            "3) Revisar las comunicaciones del colegio (agenda, correos, circulares) por si ya hay información o apoyos ofrecidos.\n"
            "4) Coordinar una reunión corta con el curso o con el equipo PIE si lo consideras necesario.\n"
        )

    # Buscamos al menos un documento de convivencia/inclusión para citar.
    snippets = search_snippets("convivencia escolar", filters={"doc_type": "normativa_nacional"}, k=1)
    if not snippets:
        snippets = search_snippets("bienestar estudiantil", filters={}, k=1)

    ref_lines = ""
    if snippets:
        sn = snippets[0]
        title = sn["title"]
        source = sn.get("source") or "fuente interna"
        ref_lines = (
            "\nEn los documentos del colegio se mencionan lineamientos de apoyo. "
            f"Por ejemplo, en «{title}» ({source}) se entregan orientaciones para abordar "
            "situaciones de bienestar y convivencia."
        )

    cierre = (
        "\n\nMuy importante:\n"
        "• Este mensaje es solo una guía general y no reemplaza la orientación profesional.\n"
        "• Si notas señales graves (ideas de hacerse daño, amenazas de violencia u otras conductas muy preocupantes), "
        "es fundamental seguir los protocolos del colegio y, si corresponde, consultar con servicios de salud o líneas de ayuda oficiales."
    )

    return header + cuerpo + ref_lines + cierre


def generate_reply_stub(msg: MessageIn, case_id: Optional[str], cmd: Optional[str]):
    """
    Por ahora, genera textos simples según case_id para probar el flujo.
    Más adelante aquí se invocará RAG + CAG + Safety.
    """
    used_rag = False
    used_cag = False
    sensitive_flag = False

    llm_model = None
    llm_prompt_tokens = None
    llm_completion_tokens = None

    if case_id == "CU1":
        used_cag = False
        reply_text = generate_cu1_plan(msg)
    elif case_id == "CU2":
        used_rag = True
        used_cag = True
        reply_text, llm_meta = explicar_con_llm(msg)
        llm_model = llm_meta["llm_model"]
        llm_prompt_tokens = llm_meta["llm_prompt_tokens"]
        llm_completion_tokens = llm_meta["llm_completion_tokens"]
    elif case_id == "CU3":
        used_rag = True
        # Opción 1: /pie (apoderado) SIEMPRE RAG-only
        if msg.role == UserRole.caregiver and cmd == "/pie":
            used_cag = False
            reply_text = generate_cu3_pie_for_caregiver(msg)
        else:
            used_cag = True
            reply_text, llm_meta = resumen_con_llm(msg)
            llm_model = llm_meta["llm_model"]
            llm_prompt_tokens = llm_meta["llm_prompt_tokens"]
            llm_completion_tokens = llm_meta["llm_completion_tokens"]
    elif case_id == "CU4":
        used_rag = True
        used_cag = True
        reply_text, llm_meta = quiz_con_llm(msg)
        llm_model = llm_meta["llm_model"]
        llm_prompt_tokens = llm_meta["llm_prompt_tokens"]
        llm_completion_tokens = llm_meta["llm_completion_tokens"]
    elif case_id == "CU5":
        used_rag = True
        used_cag = True
        reply_text, llm_meta = adaptar_con_llm(msg)
        llm_model = llm_meta["llm_model"]
        llm_prompt_tokens = llm_meta["llm_prompt_tokens"]
        llm_completion_tokens = llm_meta["llm_completion_tokens"]
    elif case_id == "CU6":
        if cmd == "/reporte_semana":
            used_cag = True
            reply_text = generate_cu6_report(msg)
        elif cmd == "/apoyo" and msg.role == UserRole.caregiver:
            used_rag = False
            used_cag = False
            reply_text = generate_cu6_apoyo_for_caregiver(msg)
    elif case_id == "CU7":
        used_rag = True
        used_cag = False
        reply_text = generate_cu7_response(msg)
    elif case_id == "CU8":
        used_cag = False
        sensitive_flag = False
        # CU8: algunos comandos usan RAG (solo /alerta_ayuda por referencias)
        used_rag = True if cmd == "/alerta_ayuda" else False
        if cmd == "/alertas":
            reply_text = generate_cu8_list_alerts(msg)
        elif cmd == "/detalle_alerta":
            reply_text = generate_cu8_alert_detail(msg)
        elif cmd == "/alerta_en_revision":
            reply_text = generate_cu8_set_status(msg, "in_review")
        elif cmd == "/alerta_resuelta":
            reply_text = generate_cu8_set_status(msg, "resolved")
        elif cmd == "/alerta_ayuda":
            reply_text = generate_cu8_alert_guidance(msg)
        else:
            reply_text = (
                "Comando CU8 no reconocido. Usa /alertas, /detalle_alerta ID, "
                "/alerta_en_revision ID, /alerta_resuelta ID o /alerta_ayuda ID."
            )

    else:
        # Mensaje especial si un rol no autorizado usa /fuente
        if cmd == "/fuente" and msg.role not in (UserRole.teacher, UserRole.coordinator):
            reply_text = (
                "El comando /fuente está pensado para docentes y equipos de convivencia. "
                "Si eres profesor o encargada/o de convivencia, puedes configurar tu rol con "
                "el comando correspondiente (por ejemplo, /soy_docente)."
            )
        else:
            reply_text = (
                "Comando recibido pero todavía no tengo un caso de uso específico asociado. "
                "Prueba con /tarea, /explicar o, si eres docente, /fuente."
            )

    # AHORA devolvemos también los metadatos del LLM
    return (
        reply_text,
        used_rag,
        used_cag,
        sensitive_flag,
        llm_model,
        llm_prompt_tokens,
        llm_completion_tokens,
    )

def generate_cu7_response(msg: MessageIn) -> str:
    """
    Usa RAG para ofrecer fragmentos de documentos relevantes (normativa, inclusión, PAEC, etc.)
    para apoyar decisiones docentes o de convivencia.
    """
    query = extract_fuente_query(msg)
    low_stim = msg.settings.get("modo") == "baja"

    # Filtramos preferentemente por normativa / inclusión (ajusta doc_type según como cargues tus docs)
    preferred_types = ["normativa_nacional", "inclusion_autismo", "paec", "reglamento_interno"]

    snippets = []
    for t in preferred_types:
        snippets = search_snippets(query, filters={"doc_type": t}, k=3)
        if snippets:
            break

    # Si no encontró nada en tipos preferidos, busca en todo
    if not snippets:
        snippets = search_snippets(query, filters={}, k=3)

    if not snippets:
        return (
            "Busqué en los documentos cargados, pero no encontré fragmentos claramente relacionados "
            "con tu consulta. Puede ser útil revisar directamente las orientaciones del establecimiento "
            "o del MINEDUC, y comentar el caso en el equipo de convivencia."
        )

    intro = (
        f"He buscado orientaciones relacionadas con: «{query}».\n\n"
        "Estos fragmentos pueden ayudarte a revisar la normativa y las orientaciones, "
        "pero siempre deben interpretarse junto al equipo del establecimiento:\n\n"
    )

    lines = []
    for i, sn in enumerate(snippets, start=1):
        title = sn.get("title") or "Documento sin título"
        year = sn.get("year")
        url = (sn.get("url") or "").strip()

        content = (sn.get("content") or "")
        excerpt, _ = _safe_excerpt(content, 280)

        header = f"{i}) {title}" + (f" ({year})" if year else "")
        lines.append(header)

        if url:
            lines.append(url)

        if excerpt:
            lines.append(f"Extracto: «{excerpt}»")

        lines.append("")  # separación

    cierre = (
        "\nTe sugiero revisar estos documentos completos y, si se trata de una situación compleja, "
        "analizarla junto al equipo de convivencia o inclusión, respetando siempre la dignidad y los "
        "derechos del estudiante."
    )

    if low_stim:
        # Quitamos adjetivos innecesarios y dejamos un formato más directo
        intro = intro.replace("claramente", "").replace("compleja", "")
        cierre = (
            "\nRevisa los documentos completos y conversa el caso con el equipo del establecimiento. "
            "La decisión final siempre es de los adultos responsables."
        )

    return intro + "\n".join(lines) + cierre


def extract_alert_id_from_text(text: str, base_cmd: str) -> Optional[int]:
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    try:
        return int(parts[1].strip())
    except ValueError:
        return None


def generate_cu8_list_alerts(msg: MessageIn) -> str:
    if msg.role != UserRole.coordinator:
        return (
            "El comando /alertas está pensado para coordinadores o equipos de convivencia."
        )

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, created_at, student_id, course_id, alert_type, status
                FROM teacher_alerts
                WHERE status = 'pending'
                ORDER BY created_at DESC
                LIMIT 20;
                """
            )
            rows = cur.fetchall()

    if not rows:
        return "No hay alertas pendientes en este momento."

    lines = ["Alertas pendientes (máx. 20):", ""]
    for r in rows:
        alert_id, created_at, student_id, course_id, alert_type, status = r
        lines.append(
            f"- ID {alert_id} | {alert_type} | estado={status} | curso_id={course_id} | {created_at.strftime('%Y-%m-%d %H:%M')}"
        )

    lines.append("")
    lines.append("Usa /detalle_alerta ID para ver detalle.")
    lines.append("Usa /alerta_en_revision ID o /alerta_resuelta ID para actualizar estado.")
    return "\n".join(lines)


def generate_cu8_alert_detail(msg: MessageIn) -> str:
    if msg.role != UserRole.coordinator:
        return (
            "El comando /detalle_alerta está pensado para coordinadores o equipos de convivencia."
        )

    _, args = parse_cmd_and_args(msg)
    alert_id = parse_int_arg(args)
    if alert_id is None:
        return "Uso esperado: /detalle_alerta ID (por ejemplo, /detalle_alerta 3)."

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, created_at, student_id, course_id, alert_type, status, summary, last_update
                FROM teacher_alerts
                WHERE id = %s;
                """,
                (alert_id,),
            )
            row = cur.fetchone()

    if not row:
        return f"No encontré la alerta con ID {alert_id}. Verifica el número e inténtalo nuevamente."

    _id, created_at, student_id, course_id, alert_type, status, summary, last_update = row

    out = (
        f"Detalle de alerta ID {_id}\n\n"
        f"- Tipo: {alert_type}\n"
        f"- Estado: {status}\n"
        f"- student_id: {student_id}\n"
        f"- course_id: {course_id}\n"
        f"- Creada: {created_at.strftime('%Y-%m-%d %H:%M')}\n"
        f"- Última actualización: {last_update.strftime('%Y-%m-%d %H:%M') if last_update else '—'}\n\n"
        "Resumen registrado (extracto):\n"
        f"{(summary or '')[:800]}{'…' if summary and len(summary) > 800 else ''}\n\n"
        "Acciones:\n"
        f"• /alerta_en_revision {_id}\n"
        f"• /alerta_resuelta {_id}\n"
        f"• /alerta_ayuda {_id}\n"
    )
    return out


def update_alert_status_db(alert_id: int, new_status: str) -> bool:
    """
    Actualiza estado de teacher_alerts.
    Retorna True si se actualizó (rowcount > 0).
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE teacher_alerts
                SET status = %s,
                    last_update = NOW()
                WHERE id = %s;
                """,
                (new_status, alert_id),
            )
            return cur.rowcount > 0


def generate_cu8_set_status(msg: MessageIn, new_status: str) -> str:
    if msg.role != UserRole.coordinator:
        return "Este comando está pensado para coordinadores o equipos de convivencia."

    _, args = parse_cmd_and_args(msg)
    alert_id = parse_int_arg(args)
    if alert_id is None:
        example = "/alerta_en_revision 3" if new_status == "in_review" else "/alerta_resuelta 3"
        return f"Uso esperado: {example}"

    ok = update_alert_status_db(alert_id, new_status)
    if not ok:
        return f"No encontré la alerta con ID {alert_id}. Verifica el número e inténtalo nuevamente."

    label = "en revisión" if new_status == "in_review" else "resuelta"
    return (
        f"Listo. Alerta ID {alert_id} marcada como {label}.\n"
        f"Puedes ver el detalle con: /detalle_alerta {alert_id}"
    )



def generate_cu8_alert_guidance(msg: MessageIn) -> str:
    """
    CU8 conversacional: orientaciones iniciales para coordinadores
    sobre una alerta concreta (/alerta_ayuda ID).
    No reemplaza protocolos internos ni indicaciones profesionales.
    """
    if msg.role != UserRole.coordinator:
        return (
            "El comando /alerta_ayuda está pensado para coordinadores o equipos de convivencia. "
            "Si tienes dudas sobre una situación específica, conversa con el equipo del establecimiento."
        )

    _, args = parse_cmd_and_args(msg)
    alert_id = parse_int_arg(args)
    if alert_id is None:
        return "Uso esperado: /alerta_ayuda ID (por ejemplo, /alerta_ayuda 3)."

    # 1) Traemos la alerta desde la BD
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, created_at, student_id, course_id, alert_type, status, summary, last_update
                FROM teacher_alerts
                WHERE id = %s;
                """,
                (alert_id,),
            )
            row = cur.fetchone()

    if not row:
        return f"No encontré la alerta con ID {alert_id}. Verifica el número e inténtalo nuevamente."

    _id, created_at, student_id, course_id, alert_type, status, summary, last_update = row

    header = (
        f"Orientaciones iniciales para revisar la alerta ID {_id}.\n\n"
        f"- Tipo de alerta: {alert_type}\n"
        f"- Estado actual: {status}\n"
        f"- Curso_id (si existe): {course_id}\n"
        f"- Fecha de creación: {created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        "Resumen del mensaje del estudiante (extracto tal como fue registrado):\n"
        f"{summary[:400]}{'...' if len(summary) > 400 else ''}\n\n"
        "Sugerencias generales para el equipo (no son un protocolo oficial):\n"
        "1) Revisar el contexto: contrastar este mensaje con la información que tengan del curso y del estudiante.\n"
        "2) Acordar quién toma contacto: definir quién del equipo (profesor jefe, orientador, psicólogo/a) conversará primero con el estudiante.\n"
        "3) Registrar la conversación y los acuerdos de manera breve y respetuosa.\n"
        "4) Valorar si es necesario informar a la familia y cómo hacerlo de forma cuidadosa.\n"
    )

    # 2) Referencias normativas rápidas
    snippets = search_snippets(alert_type or "convivencia escolar",
                               filters={"doc_type": "normativa_nacional"},
                               k=2)
    if not snippets:
        snippets = search_snippets("convivencia escolar", filters={}, k=2)

    ref_lines = ""
    if snippets:
        ref_lines = "\nAlgunos documentos relacionados que pueden ayudar a revisar el caso:\n"
        for sn in snippets:
            title = sn["title"]
            source = sn.get("source") or "fuente interna"
            doc_type = sn.get("doc_type") or ""
            ref_lines += f"- «{title}» ({source}, tipo: {doc_type}).\n"

    cierre = (
        "\nRecuerda:\n"
        "• Estas orientaciones son solo un apoyo inicial para ordenar la conversación interna.\n"
        "• Las decisiones deben alinearse con los protocolos de convivencia y de resguardo del colegio.\n"
        "• Si se identifican riesgos altos para la integridad del estudiante u otras personas, "
        "es fundamental activar de inmediato los protocolos de protección y, si corresponde, "
        "derivar a redes de salud o protección especializadas."
    )

    return header + ref_lines + cierre


def detect_sensitive_categories(text: str) -> List[str]:
    """
    Detector simple de categorías sensibles.
    No es un sistema de clasificación clínico, solo un primer filtro.
    """
    t = _normalize(text)
    cats: List[str] = []

    patterns: Dict[str, List[str]] = {
        # ---- Riesgo de autolesión / ideación suicida ----
        "riesgo_autolesion": [
            r"\bno quiero (seguir viviendo|vivir)\b",
            r"\b(me )?quiero (morir|matar|matarme|suicidar|suicidarme)\b",
            r"\b(voy|me voy) a (matarme|suicidarme)\b",
            r"\b(quiero|me quiero) quitar(me)? la vida\b",
            r"\b(acabar|terminar) con todo\b",
            r"\bno puedo mas\b",
            r"\bno aguanto mas\b",
            r"\bquisiera (dormirme|desaparecer) (y )?no despertar\b",
            r"\bseria mejor (morir|estar muerto)\b",
            r"\b(no tiene sentido|no vale la pena) vivir\b",
            r"\bme (corto|corte|estoy cortando|autolesiono|hago dano)\b",
            r"\b(autolesion|ideacion suicida|pensamientos suicidas)\b",
            r"\b(cortarme las venas)\b",
            r"\b(esta es mi despedida|me despedi de todos|deje una carta)\b",
        ],

        # ---- Malestar emocional intenso ----
        "malestar_emocional_intenso": [
            r"\bme odio\b",
            r"\b(me doy asco|me detesto)\b",
            r"\bsoy (una )?(basura|asco|estorbo|inutil|un fracaso|un perdedor)\b",
            r"\bno (valgo|sirvo) (nada|para nada)?\b",
            r"\bnadie me quiere\b",
            r"\ba nadie le importo\b",
            r"\b(no merezco vivir)\b",
            r"\b(estoy deprimid[oa]|estoy en depresion)\b",
            r"\bme siento vaci[oa]\b",
        ],

        # ---- Violencia familiar / maltrato en casa ----
        "violencia_familiar": [
            r"\b(me pegan|me golpean)\b.*\b(casa|hogar)\b",
            r"\ben mi casa (me pegan|me golpean)\b",
            r"\bmi (papa|mama|padre|madre|padrastro|madrastra)\b.*\b(me pega|me golpea)\b",
            r"\b(me patearon|me empujaron|me tiraron|me agarraron a golpes)\b",
            r"\b(me amenazan|me amenazaron)\b.*\b(casa|hogar|mi papa|mi mama)\b",
            r"\b(violencia intrafamiliar|vif|maltrato)\b",
            r"\b(me insultan|me humillan|me gritan)\b.*\b(casa|hogar)\b",
        ],

        # ---- Posible abuso sexual / grooming ----
        "posible_abuso_sexual": [
            r"\b(abuso sexual|me abusaron|abusaron de mi)\b",
            r"\b(me violaron|me violo)\b",
            r"\b(me tocan|tocamientos|me manosean)\b.*\b(sin permiso|partes intimas)?\b",
            r"\b(me obligan|me obligaron|me forzaron)\b.*\b(sexo|cosas sexuales)\b",
            r"\b(me piden|me pidieron)\b.*\b(fotos intimas|nudes|pack)\b",
            r"\b(sextorsion|sextorsion)\b",
            r"\b(grooming)\b",
            r"\b(me chantajean)\b.*\b(fotos|videos)\b",
            r"\b(me dijo|me pidio)\b.*\b(no le diga(s)? a nadie|que es secreto)\b",
        ],

        # ---- Acoso escolar / bullying (incl. ciber) ----
        "acoso_escolar": [
            r"\b(me hacen bullying|acoso escolar)\b",
            r"\b(me molestan|se burlan de mi|me insultan)\b",
            r"\b(me pegan|me golpean)\b.*\b(colegio|curso|escuela|liceo)\b",
            r"\b(me amenazan)\b.*\b(curso|colegio|escuela|liceo)\b",
            r"\b(me excluyen|me hacen el vacio|me ignoran)\b",
            r"\b(me roban)\b.*\b(cosas|cuaderno|mochila)\b",
            r"\b(me webean|me webearon|me agarran pal webeo)\b",
            r"\b(ciberbullying|me acosan por redes|me funan|me doxxearon|publicaron mis datos)\b",
        ],
    }

    # 1) match por categoría
    for cat, pats in patterns.items():
        if any(re.search(p, t) for p in pats):
            cats.append(cat)

    # 2) regla simple de prioridad: si hay autolesión, no duplicar “malestar” (opcional)
    if "riesgo_autolesion" in cats and "malestar_emocional_intenso" in cats:
        cats.remove("malestar_emocional_intenso")

    return cats
    

def create_teacher_alert(
    conn,
    student_user_id: int,
    course_id: Optional[int],
    alert_type: str,
    summary: str,
):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO teacher_alerts (student_id, course_id, alert_type, summary, status)
            VALUES (%s, %s, %s, %s, 'pending');
            """,
            (student_user_id, course_id, alert_type, summary[:1000]),
        )

# ---------- Helpers de BD ----------

def _coerce_settings(value) -> dict:
    """
    Normaliza settings desde DB (jsonb) o desde request.
    Soporta dict, None, o string JSON.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value) or {}
        except Exception:
            return {}
    return {}


def get_user_settings(conn, telegram_id: int) -> dict:
    """
    Obtiene settings actuales del usuario (si existe) desde BD.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT settings FROM users WHERE telegram_id = %s;",
            (telegram_id,),
        )
        row = cur.fetchone()
    if not row:
        return {}
    return _coerce_settings(row[0])


def merge_settings(db_settings: dict, incoming_settings: dict) -> dict:
    """
    Merge superficial + merge suave de sub-dict 'extra'.
    Regla: incoming pisa db, excepto 'extra' que se fusiona.
    """
    base = dict(_coerce_settings(db_settings))
    inc = dict(_coerce_settings(incoming_settings))

    # merge normal
    out = {**base, **inc}

    # merge de 'extra' (si ambos son dict)
    b_extra = base.get("extra")
    i_extra = inc.get("extra")
    if isinstance(b_extra, dict) and isinstance(i_extra, dict):
        out["extra"] = {**b_extra, **i_extra}
    elif i_extra is None and isinstance(b_extra, dict):
        out["extra"] = b_extra

    return out


def parse_stimulation_mode(args: str) -> str | None:
    """
    Parsea /modo baja|alta (acepta sinónimos simples).
    Retorna 'baja' | 'alta' o None si no calza.
    """
    a = (args or "").strip().lower()
    if not a:
        return None

    token = a.split()[0]

    if token in ("baja", "low", "lowstim", "low_stim"):
        return "baja"
    if token in ("alta", "high", "highstim", "high_stim"):
        return "alta"

    return None


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
                settings = COALESCE(users.settings, '{}'::jsonb) || EXCLUDED.settings,
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
    *,
    user_id: int,
    role: UserRole,
    course_id: Optional[int],
    command: Optional[str],
    case_id: Optional[str],
    latency_ms: int,
    used_rag: bool,
    used_cag: bool,
    sensitive_flag: bool,
    raw_query: str,
    raw_reply: str,
    experiment_tag: Optional[str] = None,
    extra: Optional[dict] = None,
    llm_model: Optional[str] = None,
    llm_prompt_tokens: Optional[int] = None,
    llm_completion_tokens: Optional[int] = None,
) -> None:
    import json
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO interaction_logs
            (user_id, role, course_id, command, case_id,
             latency_ms, used_rag, used_cag, sensitive_flag,
             raw_query, raw_reply,
             experiment_tag, extra,
             llm_model, llm_prompt_tokens, llm_completion_tokens)
            VALUES (%s, %s::user_role, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s::jsonb,
                    %s, %s, %s);
            """,
            (
                user_id,
                role.value if hasattr(role, "value") else role,
                course_id,
                command,
                case_id,
                latency_ms,
                used_rag,
                used_cag,
                sensitive_flag,
                raw_query,
                raw_reply,
                experiment_tag,
                json.dumps(extra or {}),
                llm_model,
                llm_prompt_tokens,
                llm_completion_tokens,
            ),
        )


def extract_fuente_query(msg: MessageIn) -> str:
    text = msg.text.strip()
    if text.startswith("/fuente"):
        rest = text[len("/fuente"):].strip()
        return rest or "tu consulta"
    return text or "tu consulta"

def extract_resumen_query(msg: MessageIn) -> str:
    text = msg.text.strip()
    if text.startswith("/resumen"):
        rest = text[len("/resumen"):].strip()
        return rest or "tu tema"
    return text or "tu tema"

def extract_quiz_query(msg: MessageIn) -> str:
    text = msg.text.strip()
    if text.startswith("/quiz"):
        rest = text[len("/quiz"):].strip()
        return rest or "este contenido"
    return text or "este contenido"

def extract_adapt_request(msg: MessageIn) -> str:
    text = msg.text.strip()
    if text.startswith("/adaptar"):
        rest = text[len("/adaptar"):].strip()
        return rest or "esta actividad"
    return text or "esta actividad"

def extract_pie_query(msg: MessageIn) -> str:
    text = (msg.text or "").strip()
    base = "Programa de Integración Escolar (PIE)"

    if text.startswith("/pie"):
        rest = text[len("/pie"):].strip()
        # ancla siempre el tópico PIE, aunque haya pregunta
        return f"{base}. {rest}".strip() if rest else base

    return f"{base}. {text}".strip() if text else base

def extract_apoyo_situation(msg: MessageIn) -> str:
    text = msg.text.strip()
    if text.startswith("/apoyo"):
        rest = text[len("/apoyo"):].strip()
        return rest or "la situación que describes"
    return text or "la situación que describes"



