import requests
import json

url = "http://localhost:8000/api/v1/messages"
headers = {"Content-Type": "application/json"}
data = {
    "telegram_id": 123456789,
    "role": "student",
    "command": "/tarea",
    "text": "Hola, necesito ayuda",
    "settings": {"modo": "baja"}
}

try:
    response = requests.post(url, headers=headers, json=data)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")
    if response.status_code == 200:
        print("✅ Verification SUCCESS")
    else:
        print("❌ Verification FAILED")
except Exception as e:
    print(f"❌ Verification ERROR: {e}")
