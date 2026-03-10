
import os
import sys
import django

# Setup Django to load settings (and potentially .env if handled there)
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

# Explicitly try to load .env if not loaded
from dotenv import load_dotenv
load_dotenv()

import logging
# Configure logging to print to console
logging.basicConfig(level=logging.DEBUG)

from profiles.services.llm_engine import get_gemini_model

def test_gemini():
    print("--- Testing Gemini Connection ---")
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("[-] ERROR: GOOGLE_API_KEY not found in environment.")
        return
        
    print(f"[*] API Key found: {api_key[:5]}...{api_key[-5:] if len(api_key)>10 else ''}")
    
    print("[*] Initializing model...")
    model = get_gemini_model()
    
    if not model:
        print("[-] ERROR: Failed to create model instance.")
        return

    print("[*] Sending test prompt...")
    try:
        response = model.generate_content("Hello, represent yourself.")
        print(f"[+] SUCCESS. Response: {response.text}")
    except Exception as e:
        print(f"[-] ERROR during generation: {e}")

if __name__ == "__main__":
    test_gemini()
