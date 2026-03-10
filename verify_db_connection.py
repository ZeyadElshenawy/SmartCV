import os
import psycopg2
import sys
from dotenv import load_dotenv
from urllib.parse import urlparse

load_dotenv()

db_url = os.getenv('DATABASE_URL')
if not db_url:
    print("Error: DATABASE_URL not found in .env")
    sys.exit(1)

print(f"Testing connection to: {db_url.split('@')[-1]}") # Print host only for privacy

try:
    conn = psycopg2.connect(db_url)
    print("Successfully connected to the database!")
    cursor = conn.cursor()
    cursor.execute("SELECT version();")
    record = cursor.fetchone()
    print("You are connected to - ", record, "\n")
    conn.close()
    sys.exit(0)
except Exception as e:
    print(f"Unable to connect to the database.\nError: {e}")
    sys.exit(1)
