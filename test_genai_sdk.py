
import os
import sys
from google import genai
from google.genai import types

# Manually set key for testing if env not picked up
key = os.getenv("GOOGLE_API_KEY")
if not key:
    # Try to read from .env manually just in case
    try:
        with open('.env') as f:
            for line in f:
                if line.startswith('GOOGLE_API_KEY'):
                    key = line.split('=')[1].strip()
                    break
    except:
        pass

if not key:
    print("NO KEY FOUND")
    sys.exit(1)

print(f"Key found: {key[:5]}...")

client = genai.Client(api_key=key)
try:
    response = client.models.generate_content(
        model='models/gemini-1.5-flash',
        contents='Hello',
        config=types.GenerateContentConfig(
            max_output_tokens=100
        )
    )
    print("SUCCESS")
    print(response.text)
except Exception as e:
    print(f"ERROR: {e}")
