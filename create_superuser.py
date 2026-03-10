import os
import django
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from django.contrib.auth import get_user_model
User = get_user_model()

email = 'admin@smartcv.ai'
password = 'admin'

if not User.objects.filter(email=email).exists():
    print(f"Creating superuser: {email}")
    User.objects.create_superuser(email=email, password=password, username='admin')
    print("Superuser created successfully.")
else:
    print("Superuser already exists.")
