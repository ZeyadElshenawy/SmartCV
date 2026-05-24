"""
Django settings for smartcv project.
"""

from pathlib import Path
from decouple import config
import os
import sys
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

# Reject the insecure default in any non-test invocation. The previous guard
# only fired when DEBUG=False, which left an `manage.py runserver` started
# with DEBUG=True silently using the placeholder key — masking missing .env
# config in dev and risking leakage if the same process were ever reused
# behind a reverse proxy.
_is_test_invocation = (
    'test' in sys.argv
    or sys.argv[0].endswith('pytest')
    or os.environ.get('PYTEST_CURRENT_TEST')
)
if SECRET_KEY == _DEFAULT_SECRET and not _is_test_invocation:
    raise ImproperlyConfigured(
        "SECRET_KEY must be set to a secure value. "
        "Add SECRET_KEY=... to your .env (or environment) before running."
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
    _is_test_run = 'test' in sys.argv or 'pytest' in sys.argv[0]
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
# Persistent connections + health-check ping is Django's blessed pattern for
# PgBouncer in transaction mode: Supabase kills idle client connections, so
# we MUST validate the connection at request start (cheap SELECT) instead of
# blindly reusing it (which throws `InterfaceError: connection already closed`).
# Without conn_max_age the cold TCP+TLS handshake makes every request 2-11s.
DATABASES = {
    'default': dj_database_url.config(
        default=os.getenv('DATABASE_URL'),
        conn_max_age=60,
        conn_health_checks=True,
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
if 'test' in sys.argv:
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

# LinkedIn profile scraper (optional, opt-in).
#
# When enabled, the LinkedIn connector on /profiles/setup/connect/ drives a
# headless Chrome via Selenium to log into LinkedIn with the credentials below
# and walk the target profile. This is heavy and self-hosted only — Chrome
# must be installed on the host, the LinkedIn account can face challenges,
# and LinkedIn's ToS prohibits automated access. Default is OFF; the connector
# stays in link-only mode until the operator explicitly opts in.
LINKEDIN_SCRAPING_ENABLED = config('LINKEDIN_SCRAPING_ENABLED', default=False, cast=bool)
LINKEDIN_EMAIL = config('LINKEDIN_EMAIL', default='')
LINKEDIN_PASSWORD = config('LINKEDIN_PASSWORD', default='')
LINKEDIN_HEADLESS = config('LINKEDIN_HEADLESS', default=True, cast=bool)
LINKEDIN_USE_UNDETECTED = config('LINKEDIN_USE_UNDETECTED', default=True, cast=bool)
LINKEDIN_LOGIN_WAIT = config('LINKEDIN_LOGIN_WAIT', default=5.0, cast=float)
LINKEDIN_PAGE_WAIT = config('LINKEDIN_PAGE_WAIT', default=4.0, cast=float)
LINKEDIN_CHALLENGE_TIMEOUT = config('LINKEDIN_CHALLENGE_TIMEOUT', default=300.0, cast=float)
LINKEDIN_IMAP_USER = config('LINKEDIN_IMAP_USER', default='') or LINKEDIN_EMAIL
LINKEDIN_IMAP_PASSWORD = config('LINKEDIN_IMAP_PASSWORD', default='')
LINKEDIN_IMAP_HOST = config('LINKEDIN_IMAP_HOST', default='')
# Tolerant casts: empty string in .env (e.g. LINKEDIN_IMAP_PORT=) falls
# back to the default instead of raising. decouple's cast= raises on ''.
LINKEDIN_IMAP_PORT = int(config('LINKEDIN_IMAP_PORT', default='') or 993)
LINKEDIN_IMAP_TIMEOUT = float(config('LINKEDIN_IMAP_TIMEOUT', default='') or 120.0)
LINKEDIN_PROFILES_DIR = BASE_DIR / config(
    'LINKEDIN_PROFILES_DIR', default='chrome_profiles',
)

# Job-board scraper (Playwright). Persistent storage_state JSON saved by
# `python manage.py login_<source>` lives here; the headless scrapers
# reuse it instead of automating an interactive login each run.
JOB_SCRAPER_STORAGE_DIR = BASE_DIR / config(
    'JOB_SCRAPER_STORAGE_DIR', default='storage_state',
)
JOB_SCRAPER_DEBUG_DUMPS_DIR = BASE_DIR / config(
    'JOB_SCRAPER_DEBUG_DUMPS_DIR', default='debug_dumps',
)

# ---------------------------------------------------------------------------
# RAG (Retrieval-Augmented Generation) — feat/rag-knowledge-base
# ---------------------------------------------------------------------------
# RAG_ENABLED gates the entire retrieval flow in resume_generator.py. When
# False, the resume prompt is identical to pre-RAG behavior (baseline for the
# §5 A/B eval). Default False until benchmarks confirm a lift.
RAG_ENABLED = config('RAG_ENABLED', default=True, cast=bool)
# Total number of KB chunks injected into the resume prompt as the
# `STANDARDS, EXAMPLES & CONVENTIONS` block.
RAG_TOP_K = config('RAG_TOP_K', default=6, cast=int)
# How many of `RAG_TOP_K` come from universal categories (ats_rules +
# banned_patterns). The remainder is role/seniority/region-filtered.
RAG_UNIVERSAL_SHARE = config('RAG_UNIVERSAL_SHARE', default=3, cast=int)

# ---------------------------------------------------------------------------
# Bullet validator — §4 of the RAG plan
# ---------------------------------------------------------------------------
# BULLET_AUTOFIX = "report_only" runs validation and attaches a report but
# does not mutate the resume. "safe_autofix" additionally applies the
# deterministic substitutions (em-dash → comma, banned-word swaps).
# Default flipped to safe_autofix (Issue 4/7, 2026-05-22): the autofix
# pass only does deterministic, bounded rewrites (BANNED_PHRASES
# substitution + em-dash→comma); anything riskier stays report-only.
# Leaving banned recruiter-jargon and em-dashes in shipped resumes was
# strictly worse than auto-cleaning them. Override with
# BULLET_AUTOFIX=report_only if a deployment wants report-only behavior.
BULLET_AUTOFIX = config('BULLET_AUTOFIX', default='safe_autofix')
# BULLET_VALIDATOR_STRICT also flags the corporate-jargon set in
# prompt_guards.BANNED_JARGON_PHRASES — higher false-positive risk.
BULLET_VALIDATOR_STRICT = config('BULLET_VALIDATOR_STRICT', default=False, cast=bool)
# RESUME_PROMPT_CHAR_BUDGET (Issue 8): when the resume-gen prompt exceeds
# this many chars, pre-slim it (drop v2 grounding + standards blocks)
# instead of sending the full prompt and eating a 413 round-trip. Observed
# Groq per-request ceiling sits between the slim (~79k) and full (~88k)
# prompt sizes; 85k is a safe midpoint. The post-call 413 retry remains
# as the safety net for under-estimates.
RESUME_PROMPT_CHAR_BUDGET = config('RESUME_PROMPT_CHAR_BUDGET', default=85000, cast=int)
# BULLET_RETRY (reserved for §4 T3 treatment): on validator failure, the
# resume_generator may re-call the LLM once with the findings appended to
# the prompt. Not wired yet — placeholder so .env edits don't surprise the
# eval scripts.
BULLET_RETRY = config('BULLET_RETRY', default=False, cast=bool)

# ---------------------------------------------------------------------------
# HR/CV specialist supervisor — final review layer
# ---------------------------------------------------------------------------
# SUPERVISOR_ENABLED gates the generate -> review -> regenerate loop in
# resume_generator.generate_resume_content_supervised. Default False (dark
# ship): turning it on roughly doubles synchronous resume latency (extra
# multimodal review + a possible regen round) AND the integration suite
# replays RECORDED LLM responses — which don't exist for the supervisor — so
# defaulting on would break the green suite. Enable per-environment via .env.
SUPERVISOR_ENABLED = config('SUPERVISOR_ENABLED', default=False, cast=bool)
# Max REVISION rounds after the first draft. 1 = up to one regeneration when
# the first draft has blocking content issues. 0 = review-only shadow mode
# (surface findings, never regenerate). Each round adds one generation + one
# review call.
SUPERVISOR_MAX_REVISION_ROUNDS = config('SUPERVISOR_MAX_REVISION_ROUNDS', default=1, cast=int)
# The supervisor's vision model is selected via the per-task credential
# convention (profiles.services.llm_engine._resolve_credentials): set
# GROQ_MODEL_SUPERVISOR / GROQ_API_KEY_SUPERVISOR to override. Falls back to
# the default GROQ_MODEL (meta-llama/llama-4-scout-17b-16e-instruct), which
# the Step 0 spike confirmed serves vision.

# End of settings.py
