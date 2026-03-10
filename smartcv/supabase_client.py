import os
from supabase import create_client, Client
from decouple import config

url: str = config("SUPABASE_URL", default=os.environ.get("SUPABASE_URL"))
key: str = config("SUPABASE_KEY", default=os.environ.get("SUPABASE_KEY"))

supabase: Client = None

if url and key:
    try:
        supabase = create_client(url, key)
    except Exception as e:
        print(f"Error initializing Supabase client: {e}")
else:
    print("Warning: SUPABASE_URL or SUPABASE_KEY not set.")
