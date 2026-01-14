import requests
import json

url = "http://backend:8000/api/v1/messages"
payload = {
    "telegram_id": 12345,
    "role": "student",
    "command": "/explicar",
    "text": "/explicar fotosíntesis"
}
headers = {"Content-Type": "application/json"}

try:
    response = requests.post(url, json=payload, headers=headers)
    print(f"Status Code: {response.status_code}")
    print(f"Response Body: {response.text}")
except Exception as e:
    print(f"Error: {e}")
