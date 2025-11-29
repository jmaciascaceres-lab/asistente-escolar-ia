import os
import time
import requests
from dotenv import load_dotenv

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

                # /start simple local
                if text.startswith("/start"):
                    send_message(
                        chat_id,
                        "Hola 👋, soy el Asistente Escolar IA.\n"
                        "Por ahora estoy en fase de pruebas. Puedes usar comandos como /tarea o /explicar.",
                    )
                    continue

                # comando = primera palabra (ej: /tarea), resto es argumento
                parts = text.split(" ", 1)
                command = parts[0]
                # podríamos usar 'parts[1]' para algo más adelante

                # TODO: rol real; por ahora asumimos 'student'
                backend_payload = {
                    "telegram_id": from_id,
                    "role": "student",
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
