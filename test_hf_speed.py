import os, time, sys
sys.path.insert(0, 'g:/New folder/SmartCV')

# Load env manually
with open('.env') as f:
    for line in f:
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')

import django
django.setup()

from profiles.services.llm_engine import get_llm_client, LLM_MODEL
print(f"Model: {LLM_MODEL}")

client = get_llm_client()
if not client:
    print("ERROR: No LLM client")
    sys.exit(1)

t = time.time()
r = client.chat.completions.create(
    model=LLM_MODEL,
    messages=[
        {"role": "system", "content": "Output only valid JSON."},
        {"role": "user", "content": 'Reply with: {"status": "ok", "speed": "fast"}'}
    ],
    max_tokens=30,
    temperature=0.0
)
elapsed = time.time() - t
print(f"LLM response in {elapsed:.2f}s: {r.choices[0].message.content[:100]}")

# Test embedding
from profiles.services.embeddings import get_embedding
t = time.time()
emb = get_embedding("Python Django developer with 5 years experience")
elapsed = time.time() - t
if emb:
    print(f"Embedding in {elapsed:.2f}s: dim={len(emb)}, first3={emb[:3]}")
else:
    print(f"Embedding FAILED after {elapsed:.2f}s")
