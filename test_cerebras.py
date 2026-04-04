import sys
import os

# add project to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from profiles.services.llm_engine import get_llm_client, LLM_MODEL
import time

client = get_llm_client()
print(f"Testing model {LLM_MODEL}...")

start = time.time()
try:
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": "Hi"}],
        max_tokens=10
    )
    print("Success:", response.choices[0].message.content)
except Exception as e:
    print("Error:", e)
print(f"Took {time.time() - start:.2f}s")
