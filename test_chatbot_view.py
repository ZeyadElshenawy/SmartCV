
import os
import sys
import django
import json

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from django.test import RequestFactory
from django.contrib.auth import get_user_model
from profiles.views import chatbot_api
from profiles.models import UserProfile

User = get_user_model()

def test_view():
    print("--- Testing Chatbot View ---")
    factory = RequestFactory()
    
    # 1. Setup User
    user = User.objects.first()
    if not user:
        print("[-] No user found.")
        return
    print(f"[*] User: {user.username}")

    # 2. Test Initial Load (No answer, just get question)
    data = {'answer': None, 'field': None}
    request = factory.post('/profiles/api/chatbot/', 
                           data=json.dumps(data), 
                           content_type='application/json')
    request.user = user # Simulate login
    
    print("[*] Calling view...")
    response = chatbot_api(request)
    
    print(f"[*] Status Code: {response.status_code}")
    print(f"[*] Content: {response.content.decode('utf-8')}")
    
    if response.status_code == 200:
        print("[+] success")
    else:
        print("[-] failed")

if __name__ == "__main__":
    test_view()
