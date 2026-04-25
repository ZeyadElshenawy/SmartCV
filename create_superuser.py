"""Dev seed: create a Django superuser. Reads credentials from env so we
never commit `admin/admin` to a deployed instance.

Usage:
    SUPERUSER_EMAIL=me@x.com SUPERUSER_PASSWORD=correct-horse python create_superuser.py

Defaults to admin/admin@smartcv.ai ONLY when DEBUG=True. Refuses to create
the default-password account against a non-DEBUG settings module so this
script can't accidentally seed weak credentials in production.
"""
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from django.conf import settings  # noqa: E402  — must come after django.setup()
from django.contrib.auth import get_user_model  # noqa: E402

User = get_user_model()

email = os.getenv('SUPERUSER_EMAIL', 'admin@smartcv.ai')
password = os.getenv('SUPERUSER_PASSWORD', 'admin')
username = os.getenv('SUPERUSER_USERNAME', 'admin')

if not settings.DEBUG and password == 'admin':
    sys.exit(
        "Refusing to create a superuser with the default 'admin' password "
        "outside DEBUG. Set SUPERUSER_PASSWORD (and ideally SUPERUSER_EMAIL) "
        "before running this script in a non-dev environment."
    )

if not User.objects.filter(email=email).exists():
    print(f"Creating superuser: {email}")
    User.objects.create_superuser(email=email, password=password, username=username)
    print("Superuser created successfully.")
else:
    print(f"Superuser already exists: {email}")
