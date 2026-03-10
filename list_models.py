
import os
import sys
import google.generativeai as genai
from django.conf import settings

# Setup env for API key
import django
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

def list_models():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("No API Key found.")
        return
        
    genai.configure(api_key=api_key)
    
    print("Listing models...")
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"- {m.name}")

if __name__ == "__main__":
    list_models()
