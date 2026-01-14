import os
import time
import requests
import json
from dotenv import load_dotenv
from typing import Dict, List, Optional
from pathlib import Path

if not os.getenv('TELEGRAM_BOT_TOKEN'):
    load_dotenv(dotenv_path=os.getenv('DOTENV_PATH', 'backend/.env'), override=False)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000/api/v1/messages")
EXPERIMENT_TAG_DOCENTES = "pilot_docentes_2026S1"
EXTRA_BASE_DOCENTES = {
    "contexto": "taller_docentes",
    "pais": "Chile"
}
TELEGRAM_MODE = os.getenv("TELEGRAM_MODE", "polling").strip().lower()

if not TOKEN:
    raise RuntimeError('TELEGRAM_BOT_TOKEN no está definido (revisa env_file/.env).')
if not BACKEND_URL or 'localhost' in BACKEND_URL:
    raise RuntimeError(f'BACKEND_URL inválida dentro de Docker: {BACKEND_URL}. Usa http://backend:8000/api/v1/messages')

# Polling settings
TELEGRAM_LONGPOLL_TIMEOUT = int(os.getenv("TELEGRAM_LONGPOLL_TIMEOUT", "50"))
TELEGRAM_POLLING_LIMIT = int(os.getenv("TELEGRAM_POLLING_LIMIT", "100"))
TELEGRAM_DROP_PENDING_UPDATES = os.getenv("TELEGRAM_DROP_PENDING_UPDATES", "false").strip().lower() in ("1", "true", "yes")

allowed_updates_raw = os.getenv("TELEGRAM_ALLOWED_UPDATES", "").strip()
TELEGRAM_ALLOWED_UPDATES: Optional[List[str]]
if allowed_updates_raw:
    TELEGRAM_ALLOWED_UPDATES = [x.strip() for x in allowed_updates_raw.split(",") if x.strip()]
else:
    TELEGRAM_ALLOWED_UPDATES = None  # None = Telegram enviará todos los updates

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN no está definido en .env")

BASE_URL = f"https://api.telegram.org/bot{TOKEN}"

OFFSET_FILE = os.getenv("TELEGRAM_OFFSET_FILE", ".telegram_offset.json")

def _parse_int_set(csv: str) -> set[int]:
    """Parsea CSV de enteros (ej: '123,456') -> {123,456}. Ignora vacíos e inválidos."""
    out: set[int] = set()
    for raw in (csv or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.add(int(raw))
        except ValueError:
            continue
    return out

TELEGRAM_MAX_USERS = int(os.getenv("TELEGRAM_MAX_USERS", "30") or 30)
TELEGRAM_ALLOWLIST_USER_IDS = _parse_int_set(os.getenv("TELEGRAM_ALLOWLIST_USER_IDS", ""))
TELEGRAM_ALLOWLIST_CHAT_IDS = _parse_int_set(os.getenv("TELEGRAM_ALLOWLIST_CHAT_IDS", ""))  # útil si quieres habilitar un grupo

TELEGRAM_DENY_MESSAGE = os.getenv(
    "TELEGRAM_DENY_MESSAGE",
    "Acceso restringido a este bot. Si necesitas acceso, solicita tu habilitación al administrador."
)

if (TELEGRAM_ALLOWLIST_USER_IDS or TELEGRAM_ALLOWLIST_CHAT_IDS) and TELEGRAM_MAX_USERS > 0:
    if len(TELEGRAM_ALLOWLIST_USER_IDS) > TELEGRAM_MAX_USERS:
        raise RuntimeError(
            f"Allowlist excede TELEGRAM_MAX_USERS: {len(TELEGRAM_ALLOWLIST_USER_IDS)} > {TELEGRAM_MAX_USERS}"
        )


def is_allowed_user(from_id: int, chat_id: int) -> bool:
    """Regla:
    - Si existe allowlist (user_ids o chat_ids), SOLO permite a quienes estén listados.
    - Si allowlist está vacía, permite a todos (modo abierto).
    """
    if TELEGRAM_ALLOWLIST_USER_IDS or TELEGRAM_ALLOWLIST_CHAT_IDS:
        return (from_id in TELEGRAM_ALLOWLIST_USER_IDS) or (chat_id in TELEGRAM_ALLOWLIST_CHAT_IDS)
    return True

def load_offset() -> int | None:
    try:
        with open(OFFSET_FILE, "r", encoding="utf-8") as f:
            return int(json.load(f).get("offset"))
    except Exception:
        return None


def save_offset(offset: int) -> None:
    try:
        with open(OFFSET_FILE, "w", encoding="utf-8") as f:
            json.dump({"offset": offset}, f)
    except Exception as e:
        print("[offset_save_error]", repr(e))


def tg_api(method: str, params: dict | None = None, json_body: dict | None = None, timeout: float = 30.0) -> dict:
    url = f"{BASE_URL}/{method}"
    try:
        if json_body is not None:
            r = requests.post(url, json=json_body, timeout=timeout)
        else:
            r = requests.get(url, params=params, timeout=timeout)
        data = r.json()
        if not data.get("ok"):
            print(f"[telegram_api_error] method={method} status={r.status_code} body={str(data)[:2000]}")
        return data
    except Exception as e:
        print(f"[telegram_api_exception] method={method} error={repr(e)}")
        return {"ok": False, "error": str(e)}


def ensure_no_webhook():
    """
    Para long-polling, es buena práctica borrar cualquier webhook previo.
    Telegram permite drop_pending_updates en deleteWebhook para limpiar cola.
    """
    # getWebhookInfo (diagnóstico)
    info = tg_api("getWebhookInfo", timeout=15)
    if info.get("ok"):
        url = (info.get("result") or {}).get("url")
        pending = (info.get("result") or {}).get("pending_update_count")
        print(f"[webhook_info] url={url!r} pending_update_count={pending}")

    # deleteWebhook (con o sin drop_pending_updates)
    payload = {"drop_pending_updates": True} if TELEGRAM_DROP_PENDING_UPDATES else {}
    res = tg_api("deleteWebhook", json_body=payload if payload else {}, timeout=15)
    if res.get("ok"):
        print(f"[webhook_deleted] drop_pending_updates={TELEGRAM_DROP_PENDING_UPDATES}")
    else:
        print(f"[webhook_delete_failed] drop_pending_updates={TELEGRAM_DROP_PENDING_UPDATES} res={res}")


def get_updates(offset: int | None = None) -> list[dict]:
    params: dict = {
        "timeout": TELEGRAM_LONGPOLL_TIMEOUT,
        "limit": TELEGRAM_POLLING_LIMIT,
    }
    if offset is not None:
        params["offset"] = offset
    if TELEGRAM_ALLOWED_UPDATES is not None:
        params["allowed_updates"] = json.dumps(TELEGRAM_ALLOWED_UPDATES)  # Telegram espera JSON-serialized array

    # Requests timeout debe ser > longpoll timeout (margen)
    req_timeout = TELEGRAM_LONGPOLL_TIMEOUT + 10
    resp = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=req_timeout)
    data = resp.json()
    if not data.get("ok"):
        print("[get_updates_error]", data)
        return []
    return data.get("result", [])


def send_message(chat_id: int, text: str) -> dict:
    payload = {"chat_id": chat_id, "text": text}
    # timeout corto; si Telegram no responde, no queremos bloquear el worker
    resp = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=20)
    data = resp.json()
    if not data.get("ok"):
        print("Error al enviar mensaje:", data)
    return data


def normalize_command(cmd: str) -> str:
    """
    Normaliza comandos Telegram:
    - Quita @botname: /quiz@MiBot -> /quiz
    - Fuerza minúsculas
    """
    cmd = (cmd or "").strip()
    if not cmd.startswith("/"):
        return cmd
    cmd = cmd.split("@", 1)[0]
    return cmd.lower()


user_roles: Dict[int, str] = {}  # telegram_id -> role ("student", "teacher", etc.)

def get_start_message_for_role(role: str) -> str:
    if role == "teacher":
        return (
            "Hola 👋, soy el Asistente Escolar IA para docentes.\n\n"
            "Soy un prototipo en desarrollo que te ayuda a:\n"
            "• Consultar fuentes normativas e inclusivas (/fuente)\n"
            "• Generar pre-resúmenes para reuniones o clases (/resumen)\n"
            "• Crear preguntas de evaluación formativa (/quiz)\n"
            "• Proponer adaptaciones de actividades con apoyos DUA (/adaptar)\n\n"
            "Recuerda:\n"
            "• No reemplazo tu criterio profesional ni la lectura directa de la normativa.\n"
            "• Estoy en fase de prueba, por lo que algunas respuestas pueden ser incompletas.\n"
            "• Usa siempre tu juicio docente y el apoyo del equipo de convivencia/PIE."
        )

    if role == "caregiver":
        return (
            "Hola 👋, soy el Asistente Escolar IA para madres, padres y apoderados.\n\n"
            "En esta etapa piloto puedo ayudarte a:\n"
            "• Entender conceptos generales como PIE y educación inclusiva (/pie)\n"
            "• Recibir orientaciones breves para acompañar el estudio o el bienestar (/apoyo)\n"
            "• Más adelante: revisar un reporte semanal simplificado (/reporte_semana)\n\n"
            "Importante:\n"
            "• No reemplazo la comunicación directa con el colegio.\n"
            "• No soy un servicio de emergencia ni de atención clínica."
        )

    if role == "coordinator":
        return (
            "Hola 👋, soy el Asistente Escolar IA para equipos de convivencia e inclusión.\n\n"
            "Puedes usarme para:\n"
            "• Revisar alertas generadas a partir de mensajes sensibles de estudiantes (/alertas)\n"
            "• Ver el detalle de una alerta específica (/detalle_alerta ID)\n"
            "• Consultar normativa o lineamientos de inclusión y convivencia (/fuente, /resumen)\n\n"
            "Recuerda:\n"
            "• Las alertas son solo un apoyo inicial; la evaluación la realizan ustedes.\n"
            "• Este sistema está en piloto, por lo que conviene revisar los procesos internos antes de tomar decisiones."
        )

    # Por defecto: estudiante
    return (
        "Hola 👋, soy el Asistente Escolar IA.\n\n"
        "Puedo ayudarte a:\n"
        "• Organizar tus tareas y estudiar mejor (/tarea)\n"
        "• Entender temas de clases con explicaciones guiadas (/explicar)\n\n"
        "Importante:\n"
        "• No reemplazo a tus profes, solo los apoyo.\n"
        "• Si te sientes muy mal o en peligro, habla con una persona adulta de confianza. "
        "El bot NO es para emergencias.\n\n"
        "Si eres profesor, escribe /soy_docente.\n"
        "Si eres madre/padre o apoderado, escribe /soy_apoderado.\n"
        "Si eres coordinador/a o equipo de convivencia, escribe /soy_coordinador."
    )


def get_help_message_for_role(role: str) -> str:
    if role == "teacher":
        return (
            "Comandos para docentes:\n\n"
            "• /fuente + texto\n"
            "  Buscar fragmentos de normativa/inclusión para una situación concreta.\n"
            "  Ej: /fuente Estudiante autista tuvo un episodio complejo en recreo\n\n"
            "• /resumen + tema\n"
            "  Generar un pre-resumen con ideas clave desde documentos relevantes.\n"
            "  Ej: /resumen DUA en evaluación de lectura\n\n"
            "• /quiz + tema\n"
            "  Crear preguntas de evaluación formativa.\n"
            "  Ej: /quiz ciclo del agua 6° básico\n\n"
            "• /adaptar + descripción de actividad\n"
            "  Proponer adaptaciones y apoyos DUA.\n"
            "  Ej: /adaptar Prueba escrita de historia para 8° básico con estudiante con TDAH\n\n"
            "• /reporte_semana\n"
            "  Ver un resumen de cómo has usado el asistente en los últimos 7 días "
            "(comandos más usados y recordatorios).\n"
        )

    if role == "caregiver":
        return (
            "Comandos para madres, padres y apoderados:\n\n"
            "• /pie + pregunta\n"
            "  Explicaciones simples sobre PIE, inclusión educativa, etc.\n\n"
            "• /apoyo + situación\n"
            "  Orientaciones breves para acompañar el estudio y el bienestar.\n\n"
            "• /reporte_semana\n"
            "  Ver un resumen simple del uso de este chat en los últimos 7 días.\n"
            "  Más adelante, si el colegio lo autoriza, se podrá vincular al uso académico "
            "del asistente por parte de tu hijo o hija.\n"
        )

    if role == "coordinator":
        return (
            "Comandos para coordinadores/equipos de convivencia e inclusión:\n\n"
            "• /alertas\n"
            "  Lista las alertas pendientes generadas por mensajes sensibles de estudiantes.\n\n"
            "• /detalle_alerta ID\n"
            "  Muestra el detalle de una alerta específica.\n\n"
            "• /fuente + texto\n"
            "  Busca fragmentos normativos o lineamientos relevantes.\n\n"
            "• /resumen + tema\n"
            "  Pre-resumen de documentos para preparar reuniones o planes de apoyo.\n\n"
            "• /alerta_ayuda ID\n"
            "  Entrega orientaciones generales para que el equipo revise una alerta concreta.\n\n"
        )

    # Estudiante por defecto
    return (
        "Comandos principales para estudiantes:\n\n"
        "• /tarea + descripción\n"
        "  Genera un plan para organizar tu tarea, estudiar una prueba o planificar la semana.\n"
        "  Ej: /tarea Estudiar para la prueba de fracciones del lunes\n\n"
        "• /explicar + tema\n"
        "  Te guía para entender mejor un contenido y hacer un repaso.\n"
        "  Ej: /explicar ciclo del agua\n\n"
        "Más adelante podrás configurar opciones como modo de baja estimulación.\n"
        "Si eres profesor o apoderado, recuerda usar /soy_docente o /soy_apoderado."
    )

def main():
    print("-- Iniciando bot de Telegram (long polling)...")

    if TELEGRAM_MODE != "polling":
        raise RuntimeError(f"TELEGRAM_MODE={TELEGRAM_MODE} no soportado en este script. Usa TELEGRAM_MODE=polling")

    ensure_no_webhook()

    print("-- Modo polling activo.")

    offset = load_offset()
    print(f"[offset] starting_offset={offset}")

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                save_offset(offset)

                if "message" not in update:
                    continue
                message = update["message"]

                if "text" not in message:
                    continue

                text = message["text"].strip()
                chat_id = message["chat"]["id"]
                from_id = message["from"]["id"]

                # /id siempre disponible (para que el usuario pueda enviarte su identificador y lo agregues a la allowlist)
                if text == "/id":
                    send_message(chat_id, f"user_id={from_id}\nchat_id={chat_id}")
                    continue

                # Allowlist (si está configurada, restringe el acceso)
                if not is_allowed_user(from_id, chat_id):
                    send_message(chat_id, TELEGRAM_DENY_MESSAGE + "\n\nTip: envíame /id para obtener tu identificador.")
                    continue

                # --- comandos de rol ---
                if text.startswith("/soy_estudiante"):
                    user_roles[from_id] = "student"
                    requests.post(
                        f"{BACKEND_URL.rsplit('/api', 1)[0]}/api/v1/users/set_role",
                        json={"telegram_id": from_id, "role": "student"},
                        timeout=10,
                    )
                    send_message(chat_id, "Perfecto, te registraré como estudiante.")
                    continue

                if text.startswith("/soy_docente"):
                    user_roles[from_id] = "teacher"
                    requests.post(
                        f"{BACKEND_URL.rsplit('/api', 1)[0]}/api/v1/users/set_role",
                        json={"telegram_id": from_id, "role": "teacher"},
                        timeout=10,
                    )
                    send_message(chat_id, "Listo, te registraré como docente.")
                    continue

                if text.startswith("/soy_apoderado"):
                    user_roles[from_id] = "caregiver"
                    requests.post(
                        f"{BACKEND_URL.rsplit('/api', 1)[0]}/api/v1/users/set_role",
                        json={"telegram_id": from_id, "role": "caregiver"},
                        timeout=10,
                    )
                    send_message(chat_id, "Anotado, te registraré como madre/padre o apoderado.")
                    continue

                if text.startswith("/soy_coordinador") or text.startswith("/soy_coordinadora"):
                    user_roles[from_id] = "coordinator"
                    requests.post(
                        f"{BACKEND_URL.rsplit('/api', 1)[0]}/api/v1/users/set_role",
                        json={"telegram_id": from_id, "role": "coordinator"},
                        timeout=10,
                    )
                    send_message(chat_id, "Te registraré como coordinador/a o encargado/a.")
                    continue

                # --- /start y /ayuda ---
                if text.startswith("/start"):
                    role = user_roles.get(from_id, "student")
                    send_message(chat_id, get_start_message_for_role(role))
                    continue

                if text.startswith("/ayuda"):
                    role = user_roles.get(from_id, "student")
                    send_message(chat_id, get_help_message_for_role(role))
                    continue

                # --- /alertas (coordinador) ---
                if text.startswith("/alertas"):
                    role = user_roles.get(from_id, "student")
                    if role != "coordinator":
                        send_message(
                            chat_id,
                            "El comando /alertas está pensado para coordinadores o equipos de convivencia."
                        )
                        continue

                    try:
                        resp = requests.get(
                            f"{BACKEND_URL.rsplit('/api', 1)[0]}/api/v1/alerts",
                            params={"status": "pending"},
                            timeout=20,
                        )
                        if resp.status_code != 200:
                            send_message(chat_id, "No pude obtener las alertas en este momento.")
                            continue
                        data_alerts = resp.json()
                    except Exception as e:
                        print("Error obteniendo alertas:", e)
                        send_message(chat_id, "No pude obtener las alertas en este momento.")
                        continue

                    alerts = data_alerts or []
                    if not alerts:
                        send_message(chat_id, "No hay alertas pendientes por ahora.")
                        continue

                    lines = ["Alertas pendientes:"]
                    for a in alerts[:10]:
                        lines.append(
                            f"- ID {a['alert_id']} | tipo: {a['alert_type']} | estudiante_id: {a['student_id']} | estado: {a['status']}"
                        )
                    send_message(chat_id, "\n".join(lines))
                    continue

                # --- /detalle_alerta ID (coordinador) ---
                if text.startswith("/detalle_alerta"):
                    role = user_roles.get(from_id, "student")
                    if role != "coordinator":
                        send_message(
                            chat_id,
                            "El comando /detalle_alerta está pensado para coordinadores o equipos de convivencia."
                        )
                        continue

                    parts = text.split(maxsplit=1)
                    if len(parts) < 2:
                        send_message(chat_id, "Uso: /detalle_alerta ID (por ejemplo, /detalle_alerta 3).")
                        continue

                    try:
                        alert_id = int(parts[1].strip())
                    except ValueError:
                        send_message(chat_id, "El ID de la alerta debe ser un número.")
                        continue

                    try:
                        resp = requests.get(
                            f"{BACKEND_URL.rsplit('/api', 1)[0]}/api/v1/alerts/{alert_id}",
                            timeout=20,
                        )
                        if resp.status_code == 404:
                            send_message(chat_id, f"No encontré la alerta con ID {alert_id}.")
                            continue
                        if resp.status_code != 200:
                            send_message(chat_id, "No pude obtener el detalle de la alerta.")
                            continue
                        data_alert = resp.json()
                    except Exception as e:
                        print("Error obteniendo detalle de alerta:", e)
                        send_message(chat_id, "No pude obtener el detalle de la alerta.")
                        continue

                    msg_lines = [
                        f"Detalle alerta ID {data_alert['alert_id']}:",
                        f"- Tipo: {data_alert['alert_type']}",
                        f"- Estado: {data_alert['status']}",
                        f"- Estudiante_id: {data_alert['student_id']}",
                        f"- Curso_id: {data_alert['course_id']}",
                        f"- Creada: {data_alert['created_at']}",
                        "",
                        "Resumen del mensaje:",
                        data_alert["summary"],
                    ]
                    send_message(chat_id, "\n".join(msg_lines))
                    continue

                # --- /alerta_ayuda ID (coordinador) ---
                if text.startswith("/alerta_ayuda"):
                    role = user_roles.get(from_id, "student")
                    if role != "coordinator":
                        send_message(
                            chat_id,
                            "El comando /alerta_ayuda está pensado para coordinadores o equipos de convivencia."
                        )
                        continue

                    parts = text.split(maxsplit=1)
                    if len(parts) < 2:
                        send_message(chat_id, "Uso: /alerta_ayuda ID (por ejemplo, /alerta_ayuda 3).")
                        continue

                    try:
                        alert_id = int(parts[1].strip())
                    except ValueError:
                        send_message(chat_id, "El ID de la alerta debe ser un número.")
                        continue

                    # Traer detalle de la alerta
                    try:
                        resp = requests.get(
                            f"{BACKEND_URL.rsplit('/api', 1)[0]}/api/v1/alerts/{alert_id}",
                            timeout=20,
                        )
                        if resp.status_code == 404:
                            send_message(chat_id, f"No encontré la alerta con ID {alert_id}.")
                            continue
                        if resp.status_code != 200:
                            send_message(chat_id, "No pude obtener el detalle de la alerta para entregar orientación.")
                            continue
                        data_alert = resp.json()
                    except Exception as e:
                        print("Error obteniendo detalle de alerta:", e)
                        send_message(chat_id, "No pude obtener el detalle de la alerta en este momento.")
                        continue

                    # Orientación operativa (texto plano, sin LLM)
                    msg_lines = [
                        f"Orientación para revisar alerta ID {data_alert['alert_id']}",
                        f"- Tipo: {data_alert['alert_type']}",
                        f"- Estado: {data_alert['status']}",
                        f"- Estudiante_id: {data_alert['student_id']} | Curso_id: {data_alert['course_id']}",
                        "",
                        "Checklist sugerido (equipo humano):",
                        "1) Verificar urgencia: si hay riesgo inmediato, activar protocolo del establecimiento.",
                        "2) Revisar el mensaje original y el contexto (no basarse solo en el resumen).",
                        "3) Coordinar contacto con adulto responsable (profesor jefe/convivencia/PIE) según protocolo.",
                        "4) Registrar acciones y acuerdos (trazabilidad mínima).",
                        "5) Definir seguimiento (fecha, responsable, señales a monitorear).",
                        "",
                        "Resumen registrado:",
                        data_alert.get("summary", "(sin resumen)"),
                    ]
                    send_message(chat_id, "\n".join(msg_lines))
                    continue

                # --- /alerta_en_revision ID (coordinador) ---
                if text.startswith("/alerta_en_revision"):
                    role = user_roles.get(from_id, "student")
                    if role != "coordinator":
                        send_message(
                            chat_id,
                            "El comando /alerta_en_revision está pensado para coordinadores o equipos de convivencia."
                        )
                        continue

                    parts = text.split(maxsplit=1)
                    if len(parts) < 2:
                        send_message(chat_id, "Uso: /alerta_en_revision ID.")
                        continue

                    try:
                        alert_id = int(parts[1].strip())
                    except ValueError:
                        send_message(chat_id, "El ID de la alerta debe ser un número.")
                        continue

                    try:
                        resp = requests.post(
                            f"{BACKEND_URL.rsplit('/api', 1)[0]}/api/v1/alerts/{alert_id}/status",
                            json={"status": "in_review"},
                            timeout=15,
                        )
                        if resp.status_code == 404:
                            send_message(chat_id, f"No encontré la alerta con ID {alert_id}.")
                            continue
                        if resp.status_code != 200:
                            send_message(chat_id, "No pude actualizar el estado de la alerta.")
                            continue
                    except Exception as e:
                        print("Error actualizando alerta:", e)
                        send_message(chat_id, "No pude actualizar el estado de la alerta.")
                        continue

                    send_message(chat_id, f"La alerta {alert_id} ha sido marcada como en revisión.")
                    continue

                # --- /alerta_resuelta ID (coordinador) ---
                if text.startswith("/alerta_resuelta"):
                    role = user_roles.get(from_id, "student")
                    if role != "coordinator":
                        send_message(
                            chat_id,
                            "El comando /alerta_resuelta está pensado para coordinadores o equipos de convivencia."
                        )
                        continue

                    parts = text.split(maxsplit=1)
                    if len(parts) < 2:
                        send_message(chat_id, "Uso: /alerta_resuelta ID (por ejemplo, /alerta_resuelta 3).")
                        continue

                    try:
                        alert_id = int(parts[1].strip())
                    except ValueError:
                        send_message(chat_id, "El ID de la alerta debe ser un número.")
                        continue

                    try:
                        resp = requests.post(
                            f"{BACKEND_URL.rsplit('/api', 1)[0]}/api/v1/alerts/{alert_id}/status",
                            json={"status": "resolved"},
                            timeout=15,
                        )
                        if resp.status_code == 404:
                            send_message(chat_id, f"No encontré la alerta con ID {alert_id}.")
                            continue
                        if resp.status_code != 200:
                            send_message(chat_id, "No pude actualizar el estado de la alerta.")
                            continue
                    except Exception as e:
                        print("Error actualizando alerta:", e)
                        send_message(chat_id, "No pude actualizar el estado de la alerta.")
                        continue

                    send_message(chat_id, f"La alerta {alert_id} ha sido marcada como resuelta.")
                    continue

                 # --- resto de mensajes: van al backend /api/v1/messages ---
                role = user_roles.get(from_id, "student")

                settings = {}

                # Si es docente y estamos en el piloto, marcamos la interacción
                if role == "teacher":
                    settings["experiment_tag"] = EXPERIMENT_TAG_DOCENTES
                    settings["extra"] = {**EXTRA_BASE_DOCENTES}

                # Comando principal (si parte con "/")
                raw_cmd = text.split()[0] if text.split() else ""
                command = normalize_command(raw_cmd)

                print(f"[telegram_bot] incoming text={text!r} raw_cmd={raw_cmd!r} normalized_cmd={command!r} role={role}")
                print(f"[backend] role={role} command={command} text_first={text.split()[0] if text else None}")

                # 1) Mensaje preliminar para comandos "pesados" (usan RAG + LLM)
                heavy_commands = {"/explicar", "/quiz", "/adaptar", "/resumen", "/fuente"}
                if command in heavy_commands:
                    send_message(
                        chat_id,
                        "Estoy procesando tu solicitud, dame unos segundos..."
                    )

                backend_payload = {
                    "telegram_id": from_id,
                    "role": role,
                    "command": command,
                    "text": text,
                    "course_id": None,
                    "settings": settings,
                }

                # ---- Ajustes de diagnóstico (dev) ----
                BOT_DEBUG = os.getenv("BOT_DEBUG", "1") == "1"
                BACKEND_TIMEOUT_CONNECT = float(os.getenv("BACKEND_TIMEOUT_CONNECT", "10"))
                BACKEND_TIMEOUT_READ = float(os.getenv("BACKEND_TIMEOUT_READ", "600"))

                # 2) Medir tiempo de respuesta total (bot → backend → bot)
                t0 = time.time()
                try:
                    resp = requests.post(
                        BACKEND_URL,
                        json=backend_payload,
                        timeout=(BACKEND_TIMEOUT_CONNECT, BACKEND_TIMEOUT_READ),
                    )
                    elapsed = time.time() - t0

                    if resp.status_code == 200:
                        data = resp.json()
                        reply_text = data.get(
                            "reply_text",
                            "Hubo un problema al generar la respuesta en el backend.",
                        )
                    else:
                        # Intentar extraer detalle del backend
                        detail = None
                        try:
                            detail = resp.json()
                        except Exception:
                            detail = resp.text

                        print(f"[backend_error] status={resp.status_code} body={str(detail)[:2000]}")

                        if BOT_DEBUG:
                            reply_text = f"No pude procesar tu solicitud (backend {resp.status_code}).\nDetalle (dev): {str(detail)[:800]}"
                        else:
                            reply_text = "No pude procesar tu solicitud (error de servidor)."

                except requests.exceptions.ReadTimeout as e:
                    elapsed = time.time() - t0
                    print("[backend_timeout] ReadTimeout:", repr(e))
                    reply_text = (
                        "El backend está tardando más de lo normal en responder. "
                        "Intenta nuevamente en unos momentos."
                    )

                except requests.exceptions.ConnectionError as e:
                    elapsed = time.time() - t0
                    print("[backend_conn_error] ConnectionError:", repr(e))
                    reply_text = "No pude conectar con el backend en este momento."

                except Exception as e:
                    elapsed = time.time() - t0
                    print("[backend_unknown_error] Exception:", repr(e))
                    reply_text = "Ocurrió un error inesperado al llamar al backend."

                # 3) Añadir tiempo de respuesta al final del mensaje
                reply_text += f"\n\nTiempo de respuesta del asistente: {elapsed:.1f} segundos."

                send_message(chat_id, reply_text)


        except Exception as e:
            print("Error en el polling:", e)
            time.sleep(5)

if __name__ == "__main__":
    main()
