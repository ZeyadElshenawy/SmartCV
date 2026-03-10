
import os
import sys
from google import genai

key = os.getenv("GOOGLE_API_KEY")
if not key:
    try:
        with open('.env') as f:
            for line in f:
                if line.startswith('GOOGLE_API_KEY'):
                    key = line.split('=')[1].strip()
                    break
    except:
        pass

if not key:
    print("NO KEY")
    sys.exit(1)

client = genai.Client(api_key=key)
try:
    # Attempt to list models. The method might vary in new SDK.
    # Documentation suggests client.models.list()
    for m in client.models.list():
        print(f"Model: {m.name}")
except Exception as e:
    print(f"Error listing models: {e}")
