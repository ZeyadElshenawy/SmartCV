import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS "django_q_ormq" (
        "id" bigserial NOT NULL PRIMARY KEY,
        "key" varchar(100) NOT NULL,
        "payload" text NOT NULL,
        "lock" timestamp with time zone NULL
    );
    """)
    print("Table created.")
