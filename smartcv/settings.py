"""
Django settings for smartcv project.
"""

from pathlib import Path
from decouple import config
import os
import dj_database_url
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
_DEFAULT_SECRET = 'django-insecure-default-key'
SECRET_KEY = config('SECRET_KEY', default=_DEFAULT_SECRET)

DEBUG = config('DEBUG', default=True, cast=bool)

if not DEBUG and SECRET_KEY == _DEFAULT_SECRET:
    raise ImproperlyConfigured(
        "SECRET_KEY must be set to a secure value in production. "
        "Set the SECRET_KEY environment variable."
    )

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=lambda v: [s.strip() for s in v.split(',')])


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    
    # Local Apps
    'accounts',
    'jobs',
    'profiles',
    'analysis',
    'resumes',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    "whitenoise.middleware.WhiteNoiseMiddleware",  # WhiteNoise
    'django.contrib.sessions.middleware.SessionMiddleware',
    "corsheaders.middleware.CorsMiddleware",       # CORS
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Last so it observes the final status code & duration of the actual response.
    # Defensive: any failure inside is swallowed (see core/middleware.py).
    'core.middleware.RequestObservabilityMiddleware',
]

# django-debug-toolbar (dev only, never in production).
# Off automatically in the test runner and when DEBUG=False.
if DEBUG:
    import sys as _sys
    _is_test_run = 'test' in _sys.argv or 'pytest' in _sys.argv[0]
    if not _is_test_run:
        INSTALLED_APPS.append('debug_toolbar')
        MIDDLEWARE.insert(
            MIDDLEWARE.index("whitenoise.middleware.WhiteNoiseMiddleware") + 1,
            'debug_toolbar.middleware.DebugToolbarMiddleware',
        )
        INTERNAL_IPS = ['127.0.0.1', 'localhost']
        DEBUG_TOOLBAR_CONFIG = {
            # Skip the 500ms+ panel that eats the page on every nav.
            'SHOW_TEMPLATE_CONTEXT': True,
            'RESULTS_CACHE_SIZE': 10,
        }

ROOT_URLCONF = 'smartcv.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'], # Added templates dir
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.onboarding',
            ],
        },
    },
]

WSGI_APPLICATION = 'smartcv.wsgi.application'


# Database
# `conn_health_checks` adds a round-trip on every request and worsens cold-start
# pool pressure against Supabase PgBouncer; only enable it in production.
DATABASES = {
    'default': dj_database_url.config(
        default=os.getenv('DATABASE_URL'),
        conn_max_age=0,
        conn_health_checks=not DEBUG,
    )
}

# Supabase PgBouncer (Transaction pooling on port 6543) requires disabling server-side cursors.
# `connect_timeout=10` makes a saturated pool raise OperationalError instead of hanging
# the runserver boot indefinitely (which happens when previous Python processes were
# killed -force and their PgBouncer client slots haven't been reaped yet).
DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'] = True
DATABASES['default']['OPTIONS'] = {'sslmode': 'require', 'connect_timeout': 10}

# Tests get an in-memory SQLite DB. Supabase's PgBouncer holds connections
# open which blocks CREATE DATABASE test_... with "database is being accessed
# by other users". SQLite keeps tests fast (no network) and side-steps the
# pooler entirely. Trigger: any `manage.py test ...` invocation.
import sys as _sys
if 'test' in _sys.argv:
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

if not DEBUG:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Media Files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

# Rest Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    )
}

# CORS
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
]

# Email
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = 'SmartCV <noreply@smartcv.local>'

# Custom CSRF failure page (styled 403 instead of Django's raw dev page)
CSRF_FAILURE_VIEW = 'core.views.csrf_failure'

# OpenAI
OPENAI_API_KEY = config('OPENAI_API_KEY', default='')

# Celery
CELERY_BROKER_URL = config('REDIS_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = config('REDIS_URL', default='redis://localhost:6379/0')

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '[%(asctime)s] %(levelname)s %(name)s: %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

# End of settings.py
