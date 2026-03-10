
import os
from openai import OpenAI

# Manually load key
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
    print("NO KEY")
    import sys
    sys.exit(1)

print(f"Key: {key[:10]}...")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=key,
)

model_id = "xiaomi/mimo-v2-flash:free"
print(f"Sending request to {model_id}...")
try:
    response = client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "user", "content": "Hello"}
        ],
        max_tokens=50
    )
    print("SUCCESS")
    print(response.choices[0].message.content)
except Exception as e:
    print(f"ERROR: {e}")
