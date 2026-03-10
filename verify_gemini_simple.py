
import os
import sys
import django
import time

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from profiles.services.llm_engine import get_gemini_model

def test_gemini():
    print("--- Testing Gemini Connectivity ---", flush=True)
    model = get_gemini_model()
    if not model:
        print("[-] Model is None", flush=True)
        return

    print("[*] Generating content...", flush=True)
    try:
        start = time.time()
        response = model.generate_content("Say hello")
        end = time.time()
        print(f"[+] Response: {response.text.strip()} (Time: {end-start:.2f}s)", flush=True)
    except Exception as e:
        print(f"[-] Error: {e}", flush=True)

if __name__ == "__main__":
    test_gemini()
