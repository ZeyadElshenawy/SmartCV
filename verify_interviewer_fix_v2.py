
import os
import sys
import django

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from django.contrib.auth import get_user_model
from profiles.models import UserProfile
from profiles.services.interviewer import get_next_question

User = get_user_model()

def test_interviewer():
    print("--- Testing Interviewer Logic ---")
    
    # Get or Create a test user
    user = User.objects.first()
    if not user:
        print("[-] No users found. Creating temp user.")
        user = User.objects.create_user(username='test_interviewer_bot', password='password')
        UserProfile.objects.create(user=user, full_name="Test User")
    
    # Ensure profile exists
    if not hasattr(user, 'profile'):
        UserProfile.objects.create(user=user, full_name="Test Bot User")
        
    print(f"[*] Using User: {user.username} (ID: {user.id})")
    
    # Test Question Generation
    print("[*] Calling get_next_question...")
    try:
        field, question = get_next_question(user.id)
        if field:
            print(f"[+] SUCCESS. Field: {field}, Question: {question}")
        else:
            print("[+] SUCCESS. No missing fields found (Interview complete).")
            
    except Exception as e:
        print(f"[-] CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_interviewer()
