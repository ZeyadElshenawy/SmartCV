
import os
import requests
import json
import sys

# Read valid key from .env manually to be sure
key = ""
try:
    with open('.env') as f:
        for line in f:
            if line.startswith('OPENROUTER_API_KEY'):
                key = line.split('=')[1].strip()
                break
except:
    pass

if not key:
    print("NO OPENROUTER_API_KEY FOUND IN .ENV")
    sys.exit(1)

print(f"Key found: {key[:10]}...")

def test_chat():
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # "HTTP-Referer": "http://localhost:8000", # Optional
        # "X-Title": "SmartCV", # Optional
    }
    
    # Guessing model ID based on user description. Common pattern for OpenRouter.
    # Usually xiaomi/mimo-v2-flash or similar. I will try that first.
    # If fails, I will list models.
    model_id = "xiaomi/mimo-v2-flash" 
    
    data = {
        "model": model_id,
        "messages": [
            {"role": "user", "content": "Hello, explain how AI works briefly."}
        ]
    }
    
    print(f"Testing model: {model_id}...")
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            print("[SUCCESS]")
            print(response.json()['choices'][0]['message']['content'])
        else:
            print(f"[FAILED] Status: {response.status_code}")
            print(response.text)
            
            # If 404/400, imply model name issues. Attempt to list models.
            print("\n[INFO] Attempting to list available 'xiaomi' models...")
            list_url = "https://openrouter.ai/api/v1/models"
            r = requests.get(list_url)
            if r.status_code == 200:
                models = r.json()['data']
                for m in models:
                    if 'xiaomi' in m['id'].lower() or 'mimo' in m['id'].lower():
                        print(f"- {m['id']}")
            else:
                print("Could not list models.")
                
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_chat()
