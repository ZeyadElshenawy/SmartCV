import os
import sys
import traceback

# Add project root to path
sys.path.append(os.getcwd())

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')

print("Attempting to import smartcv.wsgi...")

try:
    from smartcv.wsgi import application
    print("SUCCESS: WSGI application loaded.")
except Exception:
    traceback.print_exc()
