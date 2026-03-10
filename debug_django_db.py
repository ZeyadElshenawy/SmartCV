import os
import django
from django.conf import settings
import sys

# Setup Django standalone
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from django.db import connections
from django.db.utils import OperationalError

print("DATABASES setting:")
# Print nicely
import pprint
db_settings = settings.DATABASES['default'].copy()
if 'PASSWORD' in db_settings:
    db_settings['PASSWORD'] = '********'
pprint.pprint(db_settings)

print("\nAttempting Django Database Connection...")
try:
    conn = connections['default']
    conn.cursor()
    print("SUCCESS: Connected via Django!")
except OperationalError as e:
    print(f"FAILED: OperationalError: {e}")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
