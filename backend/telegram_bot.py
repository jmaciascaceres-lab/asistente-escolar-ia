import os
import time
import requests
from dotenv import load_dotenv
from typing import Dict

load_dotenv()  # carga .env desde el directorio actual

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000/api/v1/messages")

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN no está definido en .env")

BASE_URL = f"https://api.telegram.org/bot{TOKEN}"


def get_updates(offset=None):
    params = {"timeout": 30}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=35)
    data = resp.json()
    return data.get("result", [])


def send_message(chat_id: int, text: str):
    requests.post(
        f"{BASE_URL}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )

user_roles: Dict[int, str] = {}  # telegram_id -> role ("student", "teacher", etc.)

def main():
    print("🚀 Iniciando bot de Telegram (long polling)...")
    offset = None

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1

                if "message" not in update:
                    continue
                message = update["message"]

                if "text" not in message:
                    continue

                text = message["text"].strip()
                chat_id = message["chat"]["id"]
                from_id = message["from"]["id"]

                # --- comandos para fijar rol ---
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

                # /start simple local
                if text.startswith("/start"):
                    send_message(
                        chat_id,
                        "Hola 👋, soy el Asistente Escolar IA.\n"
                        "Por ahora estoy en fase de pruebas. Puedes usar comandos como /tarea o /explicar.",
                    )
                    continue

                # --- comando /alertas solo para coordinadores ---
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
                        data = resp.json()
                    except Exception as e:
                        print("Error obteniendo alertas:", e)
                        send_message(chat_id, "No pude obtener las alertas en este momento.")
                        continue

                    alerts = data or []
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

                # --- comando /detalle_alerta ID solo para coordinadores ---
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
                        data = resp.json()
                    except Exception as e:
                        print("Error obteniendo detalle de alerta:", e)
                        send_message(chat_id, "No pude obtener el detalle de la alerta.")
                        continue

                    msg_lines = [
                        f"Detalle alerta ID {data['alert_id']}:",
                        f"- Tipo: {data['alert_type']}",
                        f"- Estado: {data['status']}",
                        f"- Estudiante_id: {data['student_id']}",
                        f"- Curso_id: {data['course_id']}",
                        f"- Creada: {data['created_at']}",
                        "",
                        "Resumen del mensaje:",
                        data["summary"],
                    ]
                    send_message(chat_id, "\n".join(msg_lines))
                    continue

                # comando = primera palabra (ej: /tarea), resto es argumento
                parts = text.split(" ", 1)
                command = parts[0]
                # podríamos usar 'parts[1]' para algo más adelante

                role = user_roles.get(from_id, "student")
                # TODO: obtener curso_id del usuario
                backend_payload = {
                    "telegram_id": from_id,
                    "role": role,
                    "command": command,
                    "text": text,
                    "course_id": None,
                    "settings": {},  # luego se puede setear {"modo": "baja"} según preferencias
                }

                try:
                    resp = requests.post(
                        BACKEND_URL, json=backend_payload, timeout=30
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        reply_text = data.get(
                            "reply_text",
                            "Hubo un problema al generar la respuesta en el backend.",
                        )
                    else:
                        reply_text = "No pude conectar con el backend (error de servidor)."
                except Exception as e:
                    print("Error llamando al backend:", e)
                    reply_text = "No pude conectar con el backend en este momento."

                send_message(chat_id, reply_text)

        except Exception as e:
            print("Error en el polling:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
