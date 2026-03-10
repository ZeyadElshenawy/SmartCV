
import os
import django
import sys

print("--- Phase 2 Verification Script ---")

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
print("[*] Setting up Django...")
django.setup()
from django.conf import settings
print(f"[*] DB Config: {settings.DATABASES['default']}")
print("[*] Django setup complete.")

from django.contrib.auth import get_user_model
from profiles.models import UserProfile
print("[*] Models imported.")
from profiles.services.interviewer import get_next_question, process_user_reply
from profiles.services.profile_auditor import calculate_profile_completeness

User = get_user_model()

def run_verification():
    # 1. Setup Test User
    username = "phase2_tester"
    email = "phase2@example.com"
    password = "password123"
    
    try:
        user = User.objects.get(username=username)
        print(f"[+] Found existing test user: {username}")
    except User.DoesNotExist:
        user = User.objects.create_user(username=username, email=email, password=password)
        print(f"[+] Created test user: {username}")

    # Ensure profile exists and make it incomplete
    profile, created = UserProfile.objects.get_or_create(user=user)
    profile.linkedin_url = None  # Ensure this is missing
    profile.skills = []          # Ensure this is missing
    profile.save()
    print("[+] Reset profile: linkedin_url=None, skills=[]")

    # 2. Test Audit Logic
    score, queue = calculate_profile_completeness(user.id)
    print(f"[+] Audit Score: {score}")
    print(f"[+] Missing Fields Queue: {[item['field'] for item in queue]}")
    
    if not queue:
        print("[-] FAIL: Audit found no missing fields! Check profile_auditor.py")
        return

    # 3. Test Interviewer Agent (Llama 3.1 Question Generation)
    print("\n[+] Testing Interviewer Agent (Generating Question)...")
    field_name, question = get_next_question(user.id)
    
    if field_name:
        print(f"    Target Field: {field_name}")
        print(f"    Generated Question: \"{question}\"")
        if not question or len(question) < 5:
             print("[-] FAIL: Question seems invalid or empty.")
    else:
        print("[-] FAIL: No question generated.")
        return

    # 4. Test Extraction Loop (Simulate User Reply)
    print("\n[+] Testing Extraction Loop...")
    
    # Simulating a reply based on the target field
    if field_name == 'linkedin_url':
        simulated_reply = "Sure, you can find me at https://www.linkedin.com/in/phase2-verified/"
        target_value = "https://www.linkedin.com/in/phase2-verified/"
        print(f"    Simulating User Reply: \"{simulated_reply}\"")
        
        success, extracted_value = process_user_reply(user, field_name, simulated_reply)
        
        print(f"    Extraction Success: {success}")
        print(f"    Extracted Value: {extracted_value}")

        # Verify DB Update
        profile.refresh_from_db()
        if profile.linkedin_url == target_value: # Note: Regex might strip www or slash depending on implementation, but let's check basic match
             print("[+] SUCCESS: Database updated correctly!")
        elif extracted_value and extracted_value in str(profile.linkedin_url):
             print(f"[+] SUCCESS: Database updated (fuzzy match): {profile.linkedin_url}")
        else:
             print(f"[-] FAIL: Database value mismatch. Expected {target_value}, got {profile.linkedin_url}")

    elif field_name == 'skills':
         simulated_reply = "I am really good at Python and Django."
         print(f"    Simulating User Reply: \"{simulated_reply}\"")
         success, extracted_value = process_user_reply(user, field_name, simulated_reply)
         print(f"    Extracted Value: {extracted_value}")
         
         profile.refresh_from_db()
         print(f"    DB Skills: {profile.skills}")
         if profile.skills:
             print("[+] SUCCESS: Database updated with skills.")
         else:
             print("[-] FAIL: Skills not updated in DB.")

    else:
        print(f"    Skipping extraction test for field {field_name}, modify script to handle it.")

if __name__ == "__main__":
    run_verification()
