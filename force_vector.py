import os
import sys
import django
from django.db import connection

# Setup env
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

def force_vector_extension():
    print("Attempting to connect to database...")
    try:
        with connection.cursor() as cursor:
            print("Connected. Creating extension if not exists...")
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            print("SUCCESS: 'vector' extension created.")
    except Exception as e:
        print(f"ERROR: Could not create extension. {e}")

if __name__ == "__main__":
    force_vector_extension()
