from enum import Enum
import time
import json
from typing import Optional, List, Tuple    
from .rag_service import search_documents, search_snippets

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
        sensitive_flag_override, reply_override = handle_safety_and_alerts(conn, user_id, msg)
        if reply_override:
            reply_text = reply_override
        if sensitive_flag_override:
            sensitive_flag = True
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

# ---------- Helpers de lógica / orquestador mínimo ----------

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

    if role in (UserRole.teacher, UserRole.coordinator):
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
    Explicación v0 para CU2:
    - Si detecta temas específicos (ciclo del agua, fotosíntesis), usa plantillas.
    - Además consulta el RAG (documentos y/o snippets) para poder citar fuentes.
    - Si no reconoce el tema, usa un andamiaje genérico.
    """
    topic = extract_explanation_topic(msg)
    topic_lc = topic.lower()
    low_stim = msg.settings.get("modo") == "baja"

    # 1) Plantillas específicas por tema
    base_explanation = ""
    if "ciclo del agua" in topic_lc:
        base_explanation = _cu2_template_ciclo_agua(low_stim)
    elif "fotosintesis" in topic_lc or "fotosíntesis" in topic_lc:
        base_explanation = _cu2_template_fotosintesis(low_stim)
    else:
        # Andamiaje genérico (como antes)
        if low_stim:
            base_explanation = (
                f"Vamos a entender «{topic}» en pasos simples:\n\n"
                "1) Qué es: escribe en una frase corta qué entiendes por este tema.\n"
                "2) Para qué sirve: piensa en una situación concreta donde aparezca.\n"
                "3) Ejemplo: anota un ejemplo muy sencillo (puede ser de tu vida diaria).\n"
                "4) Duda principal: escribe una pregunta específica que todavía tengas.\n\n"
                "Si quieres, puedes mandarme tu frase y tu ejemplo y seguimos desde ahí."
            )
        else:
            base_explanation = (
                f"Intentemos comprender «{topic}» ordenando la idea en 3 partes:\n\n"
                "1) Definición: escribe con tus palabras qué es, evitando copiar literalmente.\n"
                "2) Propósito: piensa para qué sirve o por qué es importante en la asignatura.\n"
                "3) Ejemplo aplicado: inventa un ejemplo sencillo que conecte con algo de tu vida diaria.\n\n"
                "Luego puedes enviarme tu definición o ejemplo y te puedo ayudar a mejorarlos."
            )

    # 2) Consultar RAG para citar alguna fuente relevante (currículo y/o inclusión)
    docs = search_documents(topic, filters={"doc_type": "curriculo"})
    if not docs:
        docs = search_documents(topic, filters={})

    ref_lines = ""
    if docs:
        d = docs[0]
        fuente = d.get("source") or "fuente interna"
        ref_lines += (
            f"\n\nReferencia asociada en los documentos del colegio: «{d['title']}» ({fuente})."
        )

    # Opcional: buscar también un snippet normativo si el tema lo amerita
    # (por ejemplo, si es una duda de convivencia o inclusión, más que de contenido)
    # Por ahora, lo dejamos como posibilidad a futuro.

    return base_explanation + ref_lines

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
        reply_text = generate_cu3_summary(msg)
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
        reply_text = generate_cu7_response(msg)
    elif case_id == "CU8":
        # Teacher-in-the-loop; en el futuro activará alertas
        sensitive_flag = False  # aquí luego se pondrá True cuando se detecte algo sensible
        reply_text = (
            "CU8 (teacher-in-the-loop / alertas). Listaré o gestionaré alertas. [placeholder]"
        )
    else:
        # Mensaje especial si un rol no autorizado usa /fuente
        if msg.command == "/fuente" and msg.role not in (UserRole.teacher, UserRole.coordinator):
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

    return reply_text, used_rag, used_cag, sensitive_flag

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
        title = sn["title"]
        source = sn.get("source") or "fuente interna"
        doc_type = sn.get("doc_type") or ""
        # Pequeño preview del contenido
        content = sn["content"].replace("\n", " ")
        preview = content[:280] + ("..." if len(content) > 280 else "")
        lines.append(
            f"{i}) Documento: {title} ({source}, tipo: {doc_type}).\n"
            f"   Extracto: {preview}\n"
        )

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

def detect_sensitive_categories(text: str) -> list[str]:
    """
    Detector simple de categorías sensibles.
    No es un sistema de clasificación clínico, solo un primer filtro.
    """
    t = text.lower()
    cats: list[str] = []

    # ---- Riesgo de autolesión / ideación suicida ----
    suicidio_patterns = [
        "no quiero vivir",
        "no quiero seguir viviendo",
        "me quiero morir",
        "me quiero matar",
        "quiero matarme",
        "quiero hacerme daño",
        "quiero dañarme",
        "quiero cortarme",
        "no aguanto más vivir",
    ]
    if any(p in t for p in suicidio_patterns):
        cats.append("riesgo_autolesion")

    # ---- Autodesprecio intenso (vigilar como factor de riesgo) ----
    auto_odio_patterns = [
        "me odio a mí mismo",
        "me odio a mi mismo",
        "me odio",
        "soy una basura",
        "no valgo nada",
    ]
    if any(p in t for p in auto_odio_patterns) and "riesgo_autolesion" not in cats:
        cats.append("malestar_emocional_intenso")

    # ---- Violencia familiar / maltrato en casa ----
    violencia_familiar_patterns = [
        "me pegan en la casa",
        "me golpean en la casa",
        "me pegan mis papás",
        "me pegan mis padres",
        "mi papá me golpea",
        "mi mamá me golpea",
        "en mi casa me pegan",
    ]
    if any(p in t for p in violencia_familiar_patterns):
        cats.append("violencia_familiar")

    # ---- Posible abuso sexual (descriptores muy generales) ----
    abuso_patterns = [
        "me tocan sin permiso",
        "me tocan partes íntimas",
        "me obligan a tocar",
        "me obligan a hacer cosas sexuales",
        "me obligan a enviar fotos",
    ]
    if any(p in t for p in abuso_patterns):
        cats.append("posible_abuso_sexual")

    # ---- Acoso escolar / bullying ----
    bullying_patterns = [
        "me hacen bullying",
        "me molestan siempre",
        "me molestan todos los días",
        "me pegan en el colegio",
        "me amenazan en el curso",
        "me insultan en el curso",
    ]
    if any(p in t for p in bullying_patterns):
        cats.append("acoso_escolar")

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



