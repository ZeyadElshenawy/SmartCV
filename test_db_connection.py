import os
import psycopg2
from decouple import config

# Load settings
try:
    dbname = config('SUPABASE_DB_NAME')
    user = config('SUPABASE_DB_USER')
    password = config('SUPABASE_DB_PASSWORD')
    host = config('SUPABASE_DB_HOST')
    port = config('SUPABASE_DB_PORT')
    
    print(f"Testing connection to: {host}:{port} as {user}")
    
    try:
        conn = psycopg2.connect(
            dbname=dbname,
            user=user,
            password=password,
            host=host,
            port=port,
            connect_timeout=10,
            sslmode='require' # Supabase requires SSL
        )
        print("SUCCESS! Connected to Database.")
        conn.close()
    except Exception as e:
        print(f"CONNECTION FAILED: {e}")
        
except Exception as e:
    print(f"Configuration Error: {e}")
