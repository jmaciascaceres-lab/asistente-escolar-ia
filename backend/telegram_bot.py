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
    raise RuntimeError(f'BACKEND_URL inválida dentro de contenedor: {BACKEND_URL}')

BASE_URL = f"https://api.telegram.org/bot{TOKEN}"
OFFSET_FILE = os.getenv("TELEGRAM_OFFSET_FILE", ".telegram_offset.json")

BOT_DEBUG = os.getenv("BOT_DEBUG", "0").strip() in ("1", "true", "True", "yes", "Y")
TELEGRAM_ALLOWED_UPDATES = os.getenv("TELEGRAM_ALLOWED_UPDATES", "message,callback_query")
TELEGRAM_LONGPOLL_TIMEOUT = int(os.getenv("TELEGRAM_LONGPOLL_TIMEOUT", "50"))
TELEGRAM_POLLING_LIMIT = int(os.getenv("TELEGRAM_POLLING_LIMIT", "100"))
TELEGRAM_DROP_PENDING_UPDATES = os.getenv("TELEGRAM_DROP_PENDING_UPDATES", "true").strip().lower() in ("1", "true", "yes")

def _parse_int_set(csv: str) -> set[int]:
    if not csv:
        return set()
    out = set()
    for x in csv.split(","):
        x = x.strip()
        if not x:
            continue
        try:
            out.add(int(x))
        except Exception:
            pass
    return out

TELEGRAM_MAX_USERS = int(os.getenv("TELEGRAM_MAX_USERS", "32") or 32)

# Allowlist (general) — en freeflow se puede desactivar con TELEGRAM_OPEN_ACCESS=1
TELEGRAM_ALLOWLIST_USER_IDS = _parse_int_set(os.getenv("TELEGRAM_ALLOWLIST_USER_IDS", ""))
TELEGRAM_ALLOWLIST_CHAT_IDS = _parse_int_set(os.getenv("TELEGRAM_ALLOWLIST_CHAT_IDS", ""))  # útil si quieres habilitar un grupo
TELEGRAM_OPEN_ACCESS = os.getenv("TELEGRAM_OPEN_ACCESS", "0").strip().lower() in ("1", "true", "yes")

# Comandos privilegiados (CU8: alertas). Por seguridad, mantener controlado.
TELEGRAM_ENABLE_COORDINATOR_COMMANDS = os.getenv("TELEGRAM_ENABLE_COORDINATOR_COMMANDS", "0").strip().lower() in ("1", "true", "yes")
TELEGRAM_COORDINATOR_USER_IDS = _parse_int_set(os.getenv("TELEGRAM_COORDINATOR_USER_IDS", ""))
TELEGRAM_COORDINATOR_CHAT_IDS = _parse_int_set(os.getenv("TELEGRAM_COORDINATOR_CHAT_IDS", ""))

if TELEGRAM_MAX_USERS > 0:
    if len(TELEGRAM_ALLOWLIST_USER_IDS) > TELEGRAM_MAX_USERS:
        raise RuntimeError(
            f"Allowlist excede TELEGRAM_MAX_USERS: {len(TELEGRAM_ALLOWLIST_USER_IDS)} > {TELEGRAM_MAX_USERS}"
        )

def is_allowed_user(from_id: int, chat_id: int) -> bool:
    """Regla:
    - Si TELEGRAM_OPEN_ACCESS=1: permite a todos.
    - Si existe allowlist (user_ids o chat_ids), SOLO permite a quienes estén listados.
    - Si allowlist está vacía, permite a todos.
    """
    if TELEGRAM_OPEN_ACCESS:
        return True
    if TELEGRAM_ALLOWLIST_USER_IDS or TELEGRAM_ALLOWLIST_CHAT_IDS:
        return (from_id in TELEGRAM_ALLOWLIST_USER_IDS) or (chat_id in TELEGRAM_ALLOWLIST_CHAT_IDS)
    return True

def is_coordinator_allowed(from_id: int, chat_id: int) -> bool:
    """Control de acceso para comandos de convivencia/alertas (CU8).
    - Si TELEGRAM_ENABLE_COORDINATOR_COMMANDS es false: deshabilita el set completo.
    - Si está habilitado: permite solo IDs/chats listados en TELEGRAM_COORDINATOR_*.
    """
    if not TELEGRAM_ENABLE_COORDINATOR_COMMANDS:
        return False
    if TELEGRAM_COORDINATOR_USER_IDS or TELEGRAM_COORDINATOR_CHAT_IDS:
        return (from_id in TELEGRAM_COORDINATOR_USER_IDS) or (chat_id in TELEGRAM_COORDINATOR_CHAT_IDS)
    # Si se habilitó pero no hay lista, por seguridad NO abrir.
    return False

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
    if json_body is not None:
        r = requests.post(url, json=json_body, timeout=timeout)
    else:
        r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

def ensure_no_webhook():
    try:
        info = tg_api("getWebhookInfo")
        url = info.get("result", {}).get("url")
        if url:
            print(f"[webhook] encontrado url={url}, desactivando webhook...")
            tg_api("deleteWebhook", params={"drop_pending_updates": "true"})
    except Exception as e:
        print("[webhook_error]", repr(e))

def send_message(chat_id: int, text: str):
    try:
        tg_api("sendMessage", json_body={"chat_id": chat_id, "text": text})
    except Exception as e:
        print("[send_error]", repr(e))

def normalize_command(cmd: str) -> str:
    """
    Normaliza:
    - /quiz@MiBot -> /quiz
    - Fuerza minúsculas
    """
    cmd = (cmd or "").strip()
    if not cmd.startswith("/"):
        return cmd
    cmd = cmd.split("@", 1)[0]
    return cmd.lower()

def get_start_message() -> str:
    return (
        "Hola, soy el Asistente Escolar IA.\n\n"
        "Puedes escribirme en lenguaje natural, sin comandos.\n"
        "Ejemplos:\n"
        "• “Explícame el ciclo del agua para 6° básico”\n"
        "• “Necesito un quiz de fotosíntesis con 5 preguntas”\n"
        "• “Adapta esta evaluación con enfoque DUA para un estudiante con TDAH”\n"
        "• “Ayúdame a organizar un plan de estudio para una prueba el lunes”\n\n"
        "Atajos (opcionales): /tarea, /explicar, /resumen, /quiz, /adaptar, /fuente, /modo"
    )

def get_help_message() -> str:
    return (
        "Puedes usar el asistente sin comandos.\n\n"
        "Atajos (opcionales):\n"
        "• /tarea + descripción\n"
        "• /explicar + tema\n"
        "• /resumen + tema\n"
        "• /quiz + tema\n"
        "• /adaptar + descripción\n"
        "• /fuente + consulta\n"
        "• /modo baja|alta\n\n"
        "Nota: los comandos de alertas (/alertas, /detalle_alerta, /alerta_ayuda) "
        "se mantienen restringidos y pueden estar deshabilitados en esta versión."
    )

def main():
    print("-- Iniciando bot de Telegram (long polling)...")

    if TELEGRAM_MODE != "polling":
        raise RuntimeError(f"TELEGRAM_MODE={TELEGRAM_MODE} no soportado en este script. Usa TELEGRAM_MODE=polling")

    ensure_no_webhook()

    print("-- Modo polling activo.")

    offset = load_offset()
    print(f"[offset] starting_offset={offset}")

    if TELEGRAM_DROP_PENDING_UPDATES:
        try:
            print("[drop_pending] deleteWebhook(drop_pending_updates=true)")
            tg_api("deleteWebhook", params={"drop_pending_updates": "true"})
        except Exception as e:
            print("[drop_pending_error]", repr(e))

    allowed_updates = [x.strip() for x in TELEGRAM_ALLOWED_UPDATES.split(",") if x.strip()]

    while True:
        try:
            params = {
                "timeout": TELEGRAM_LONGPOLL_TIMEOUT,
                "limit": TELEGRAM_POLLING_LIMIT,
                "allowed_updates": json.dumps(allowed_updates),
            }
            if offset is not None:
                params["offset"] = offset

            data = tg_api("getUpdates", params=params, timeout=TELEGRAM_LONGPOLL_TIMEOUT + 10)
            results = data.get("result", [])

            for upd in results:
                offset = upd["update_id"] + 1
                save_offset(offset)

                msg = upd.get("message") or upd.get("callback_query", {}).get("message")
                if not msg:
                    continue

                chat_id = msg["chat"]["id"]
                from_id = msg.get("from", {}).get("id") or msg.get("chat", {}).get("id") or 0

                # Texto
                text = ""
                if "text" in msg:
                    text = msg["text"].strip()
                elif "caption" in msg:
                    text = (msg["caption"] or "").strip()

                if not text:
                    continue

                # Control de acceso general
                if not is_allowed_user(from_id, chat_id):
                    send_message(chat_id, "Lo siento, este bot está en piloto y tu acceso no está habilitado.")
                    continue

                # --- /start y /ayuda ---
                if text.startswith("/start"):
                    send_message(chat_id, get_start_message())
                    continue

                if text.startswith("/ayuda"):
                    send_message(chat_id, get_help_message())
                    continue

                # --- /alertas (restringido) ---
                if text.startswith("/alertas"):
                    if not is_coordinator_allowed(from_id, chat_id):
                        send_message(
                            chat_id,
                            "El comando /alertas está restringido para equipos autorizados o puede estar deshabilitado."
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
                        send_message(chat_id, "No hay alertas pendientes.")
                        continue

                    lines = ["Alertas pendientes:"]
                    for a in alerts:
                        lines.append(f"• ID {a.get('id')}: {a.get('summary')}")
                    lines.append("\nUsa /detalle_alerta ID para ver el detalle.")
                    send_message(chat_id, "\n".join(lines))
                    continue

                # --- /detalle_alerta ID (restringido) ---
                if text.startswith("/detalle_alerta"):
                    if not is_coordinator_allowed(from_id, chat_id):
                        send_message(
                            chat_id,
                            "El comando /detalle_alerta está restringido para equipos autorizados o puede estar deshabilitado."
                        )
                        continue

                    parts = text.split(maxsplit=1)
                    if len(parts) < 2:
                        send_message(chat_id, "Uso: /detalle_alerta ID (por ejemplo, /detalle_alerta 3).")
                        continue

                    try:
                        alert_id = int(parts[1].strip())
                    except ValueError:
                        send_message(chat_id, "El ID de la alerta debe ser un número entero.")
                        continue

                    try:
                        resp = requests.get(
                            f"{BACKEND_URL.rsplit('/api', 1)[0]}/api/v1/alerts/{alert_id}",
                            timeout=20,
                        )
                        if resp.status_code != 200:
                            send_message(chat_id, "No pude obtener el detalle de esa alerta.")
                            continue
                        a = resp.json()
                    except Exception as e:
                        print("Error obteniendo detalle alerta:", e)
                        send_message(chat_id, "No pude obtener el detalle de esa alerta.")
                        continue

                    msg_txt = (
                        f"Alerta ID {a.get('id')}\n"
                        f"Estado: {a.get('status')}\n"
                        f"Creada: {a.get('created_at')}\n\n"
                        f"Resumen: {a.get('summary')}\n\n"
                        f"Detalle:\n{a.get('detail')}\n\n"
                        "Acciones:\n"
                        f"• /alerta_en_revision {a.get('id')}\n"
                        f"• /alerta_resuelta {a.get('id')}\n"
                        f"• /alerta_ayuda {a.get('id')}\n"
                    )
                    send_message(chat_id, msg_txt)
                    continue

                # --- /alerta_ayuda ID (restringido) ---
                if text.startswith("/alerta_ayuda"):
                    if not is_coordinator_allowed(from_id, chat_id):
                        send_message(
                            chat_id,
                            "El comando /alerta_ayuda está restringido para equipos autorizados o puede estar deshabilitado."
                        )
                        continue

                    parts = text.split(maxsplit=1)
                    if len(parts) < 2:
                        send_message(chat_id, "Uso: /alerta_ayuda ID (por ejemplo, /alerta_ayuda 3).")
                        continue

                    try:
                        alert_id = int(parts[1].strip())
                    except ValueError:
                        send_message(chat_id, "El ID de la alerta debe ser un número entero.")
                        continue

                    try:
                        resp = requests.post(
                            f"{BACKEND_URL.rsplit('/api', 1)[0]}/api/v1/alerts/{alert_id}/help",
                            timeout=30,
                        )
                        if resp.status_code != 200:
                            send_message(chat_id, "No pude generar orientaciones para esa alerta.")
                            continue
                        payload = resp.json()
                        send_message(chat_id, payload.get("help_text", "Sin respuesta."))
                    except Exception as e:
                        print("Error generando ayuda:", e)
                        send_message(chat_id, "No pude generar orientaciones para esa alerta.")
                    continue

                # --- /alerta_en_revision ID (restringido) ---
                if text.startswith("/alerta_en_revision"):
                    if not is_coordinator_allowed(from_id, chat_id):
                        send_message(
                            chat_id,
                            "Este comando está restringido para equipos autorizados o puede estar deshabilitado."
                        )
                        continue

                    parts = text.split(maxsplit=1)
                    if len(parts) < 2:
                        send_message(chat_id, "Uso: /alerta_en_revision ID (por ejemplo, /alerta_en_revision 3).")
                        continue
                    try:
                        alert_id = int(parts[1].strip())
                    except ValueError:
                        send_message(chat_id, "El ID de la alerta debe ser un número entero.")
                        continue

                    try:
                        resp = requests.post(
                            f"{BACKEND_URL.rsplit('/api', 1)[0]}/api/v1/alerts/{alert_id}/status",
                            json={"status": "in_review"},
                            timeout=20,
                        )
                        if resp.status_code != 200:
                            send_message(chat_id, "No pude actualizar el estado de la alerta.")
                            continue
                        send_message(chat_id, "Alerta marcada como en revisión.")
                    except Exception as e:
                        print("Error actualizando alerta:", e)
                        send_message(chat_id, "No pude actualizar el estado de la alerta.")
                    continue

                # --- /alerta_resuelta ID (restringido) ---
                if text.startswith("/alerta_resuelta"):
                    if not is_coordinator_allowed(from_id, chat_id):
                        send_message(
                            chat_id,
                            "Este comando está restringido para equipos autorizados o puede estar deshabilitado."
                        )
                        continue

                    parts = text.split(maxsplit=1)
                    if len(parts) < 2:
                        send_message(chat_id, "Uso: /alerta_resuelta ID (por ejemplo, /alerta_resuelta 3).")
                        continue
                    try:
                        alert_id = int(parts[1].strip())
                    except ValueError:
                        send_message(chat_id, "El ID de la alerta debe ser un número entero.")
                        continue

                    try:
                        resp = requests.post(
                            f"{BACKEND_URL.rsplit('/api', 1)[0]}/api/v1/alerts/{alert_id}/status",
                            json={"status": "resolved"},
                            timeout=20,
                        )
                        if resp.status_code != 200:
                            send_message(chat_id, "No pude actualizar el estado de la alerta.")
                            continue
                        send_message(chat_id, "Alerta marcada como resuelta.")
                    except Exception as e:
                        print("Error actualizando alerta:", e)
                        send_message(chat_id, "No pude actualizar el estado de la alerta.")
                    continue

                # --- resto de mensajes: van al backend /api/v1/messages ---

                settings = {}

                # Comando principal (si parte con "/")+
                raw_cmd = text.split()[0] if text.split() else ""
                command = normalize_command(raw_cmd) if raw_cmd.startswith("/") else ""

                print(f"[telegram_bot] incoming text={text!r} raw_cmd={raw_cmd!r} normalized_cmd={command!r}")
                print(f"[backend_url] {BACKEND_URL}")

                backend_payload = {
                    "telegram_id": from_id,
                    "role": None,
                    "command": (command if command else None),
                    "text": text,
                    "course_id": None,
                    "settings": settings,
                }

                try:
                    resp = requests.post(
                        BACKEND_URL,
                        json=backend_payload,
                        timeout=(10, 600),
                    )
                    if resp.status_code != 200:
                        send_message(chat_id, "Hubo un error procesando tu solicitud. Intenta de nuevo.")
                        print("[backend_error]", resp.status_code, resp.text)
                        continue

                    out = resp.json()
                    reply_text = out.get("reply_text", "(sin respuesta)")
                    send_message(chat_id, reply_text)

                except Exception as e:
                    print("[backend_exception]", repr(e))
                    send_message(chat_id, "No pude conectar con el backend en este momento. Intenta más tarde.")

        except Exception as e:
            print("[poll_loop_error]", repr(e))
            time.sleep(2)

if __name__ == "__main__":
    main()
