import requests
import json

url = "http://backend:8000/api/v1/rag/ingest"
headers = {"Content-Type": "application/json"}
data = {
    "title": "Documento de prueba",
    "doc_type": "normativa",
    "metadata": {"tags": ["prueba"]}
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
