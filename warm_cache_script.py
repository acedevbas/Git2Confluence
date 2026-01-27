import requests
import json

url = "http://localhost:8000/api/v1/cache/warm"
payload = {
    "project_path": "pickup/metrics-and-events/rms-api",
    "gitlab_token": "YOUR_TOKEN_HERE",
    "compute_history": True
}
headers = {
    "Content-Type": "application/json"
}

try:
    print(f"Sending request to {url}...")
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    print("Success!")
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))
except Exception as e:
    print(f"Error: {e}")
    if 'response' in locals():
        print(response.text)
