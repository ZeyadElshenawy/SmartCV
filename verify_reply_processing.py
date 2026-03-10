
import os
import sys
import django

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

# Monkeypatch logger in interviewer
from profiles.services import interviewer

def mock_error(msg):
    print(f"[ERROR LOGGED]: {msg}")

interviewer.logger.error = mock_error

from profiles.services.interviewer import process_user_reply

from django.contrib.auth import get_user_model
User = get_user_model()

def test_reply_processing():
    print("--- Testing Reply Processing Logic (Debug) ---", flush=True)
    
    user = User.objects.first()
    if not user:
        print("[-] No users found.")
        return

    # Simulate a reply for 'linkedin_url'
    target_field = 'linkedin_url'
    user_reply = "My linkedin is https://linkedin.com/in/testuser"
    
    print(f"[*] Processing reply for {target_field}: '{user_reply}'", flush=True)
    
    try:
        success, value = process_user_reply(user, target_field, user_reply)
        if success:
            print(f"[+] SUCCESS. Extracted Value: {value}")
        else:
            print(f"[-] FAILED. Message: {value}")
            
    except Exception as e:
        print(f"[-] CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_reply_processing()
