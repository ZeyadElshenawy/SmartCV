
import os
import sys
import django
import logging

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

print("--- DEBUG REPLY START ---")

# 1. Load Env
from dotenv import load_dotenv
load_dotenv(override=True)

# 2. Setup Django
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
try:
    django.setup()
    print("[+] Django Setup Complete.")
except Exception as e:
    print(f"[-] Django Setup Failed: {e}")
    sys.exit(1)

from profiles.services.interviewer import process_user_reply
from django.contrib.auth import get_user_model
User = get_user_model()
user = User.objects.first()

print(f"[*] Testing process_user_reply with user {user.username}...")
print("[*] Target: linkedin_url, Reply: 'it is https://linkedin.com/in/test'")

try:
    success, val = process_user_reply(user, 'linkedin_url', 'it is https://linkedin.com/in/test')
    print(f"[+] Result - Success: {success}, Value: {val}")
except Exception as e:
    print(f"[-] UNCAUGHT EXCEPTION: {e}")
    import traceback
    traceback.print_exc()

print("--- DEBUG REPLY END ---")
