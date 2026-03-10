
import os
import sys
import django
import logging

# Ensure stdout is unbuffered
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

print("--- DEBUG CHATBOT START ---")

# 1. Load Env
from dotenv import load_dotenv
print("[*] Loading .env...")
load_dotenv(override=True)
key = os.getenv("OPENROUTER_API_KEY")
print(f"[*] Env Key check: {key[:10] if key else 'None'}...")

# 2. Setup Django
print("[*] Setting up Django...")
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
try:
    django.setup()
    print("[+] Django Setup Complete.")
except Exception as e:
    print(f"[-] Django Setup Failed: {e}")
    sys.exit(1)

# 3. Import Services
print("[*] Importing Services...")
try:
    from profiles.models import UserProfile
    from profiles.services.interviewer import get_next_question
    from profiles.services.llm_engine import get_llm_client, logger as llm_logger
    from profiles.services.interviewer import logger as int_logger
    print("[+] Imports Complete.")
except Exception as e:
    print(f"[-] Imports Failed: {e}")
    sys.exit(1)

# 4. Configure Logging
logging.basicConfig(level=logging.INFO)
logging.getLogger('profiles.services').setLevel(logging.DEBUG)

# 5. Test Client
print("[*] Testing get_llm_client()...")
client = get_llm_client()
if client:
    print("[+] Client obtained.")
else:
    print("[-] Client returned None.")

# 6. Test Interviewer
from django.contrib.auth import get_user_model
User = get_user_model()
user = User.objects.first()
if not user:
    print("[-] No user found to test with.")
    sys.exit(0)

print(f"[*] Testing get_next_question with user {user.username}...")
try:
    field, question = get_next_question(user.id)
    print(f"[+] Result - Field: {field}, Question: {question}")
except Exception as e:
    print(f"[-] UNCAUGHT EXCEPTION in test: {e}")
    import traceback
    traceback.print_exc()

print("--- DEBUG CHATBOT END ---")
