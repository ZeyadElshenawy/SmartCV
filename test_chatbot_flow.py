
import os
import sys
import django
import json

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

print("=== CHATBOT API TEST ===")

# 1. Load Env
from dotenv import load_dotenv
load_dotenv(override=True)
key = os.getenv("OPENROUTER_API_KEY")
print(f"[✓] API Key: {key[:10] if key else 'MISSING'}...")

# 2. Setup Django
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()
print("[✓] Django initialized")

# 3. Import
from django.contrib.auth import get_user_model
from profiles.models import UserProfile
from profiles.services.interviewer import get_next_question, process_user_reply

User = get_user_model()

# 4. Get or create test user
user = User.objects.first()
if not user:
    print("[-] No user found!")
    sys.exit(1)

print(f"[✓] Using user: {user.username}")

# Ensure user has a profile
profile, created = UserProfile.objects.get_or_create(user=user)
if created:
    print("[✓] Created new profile")
else:
    print("[✓] Profile exists")

# 5. Test get_next_question (initial call)
print("\n--- TEST 1: Initial Question ---")
try:
    field, question = get_next_question(user.id)
    if field and question:
        print(f"[✓] Field: {field}")
        print(f"[✓] Question: {question}")
    else:
        print("[!] No missing fields (profile complete)")
except Exception as e:
    print(f"[✗] ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# 6. Test process_user_reply
if field:
    print(f"\n--- TEST 2: Process Reply for '{field}' ---")
    test_reply = "Test User"  # Assuming first field is full_name
    try:
        success, value = process_user_reply(user, field, test_reply)
        print(f"[✓] Success: {success}")
        print(f"[✓] Extracted: {value}")
    except Exception as e:
        print(f"[✗] ERROR: {e}")
        import traceback
        traceback.print_exc()

# 7. Test get_next_question (after reply)
print("\n--- TEST 3: Next Question After Reply ---")
try:
    field2, question2 = get_next_question(user.id)
    if field2 and question2:
        print(f"[✓] Next Field: {field2}")
        print(f"[✓] Next Question: {question2}")
    else:
        print("[!] No more missing fields")
except Exception as e:
    print(f"[✗] ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n=== TEST COMPLETE ===")
