# SmartCV — Complete Context Document

> **Generated:** 2026-05-05
> **Scope:** every Python module, every template, every service, every flow, every algorithm.
> **Audience:** an engineer (or future-Claude) who has never opened the project and needs to be load-bearing on it within an hour.

---

## 0. Document map

1. Project at a glance — what the product is, who it's for, headline architecture
2. Repo layout
3. Tech stack & runtime
4. The LLM layer — `llm_engine`, prompt guards, structured schemas, per-task key routing
5. Project configuration — `smartcv/settings.py` line by line
6. App: `accounts` — custom UUID user, outreach token rotation
7. App: `core` — landing, agent chat, action planner, career stage, observability, health, metrics
8. App: `profiles` — the centre of gravity. CV parsing, JSONB profile, signal aggregators, chatbot, outreach, profile strength, project enrichment, dedupe, LinkedIn scraper, IMAP autofill, preference suggester
9. App: `jobs` — single-page scrapers, skill extractor, Playwright job-board discovery, scoring, RecommendedJob upsert
10. App: `analysis` — gap analyzer (two-phase), learning paths, salary negotiation, skill score
11. App: `resumes` — tailored resume generator, cover letters, ATS scoring, PDF/DOCX export, the textarea↔list bug
12. Frontend & design system — Tailwind v4 CSS-first, base.html, Alpine, Shepherd, components
13. End-to-end flows — onboarding, gap analysis, resume generation, outreach, job discovery, scan-and-score
14. Browser extension — MV3 outreach + discovery extension
15. Benchmarks — D1–D5 + B + E orchestrator
16. Background work, threading, async — the one allowed worker thread (job scraper) and why
17. Database, PgBouncer, performance
18. Testing, CI signals
19. Known issues, anti-patterns intentionally avoided, conventions
20. Glossary of model names and the data they own

---

## 1. Project at a glance

SmartCV is an AI-powered career assistant. A single user can:

- Upload a CV (PDF / DOCX / TXT) → SmartCV parses it, validates it via an LLM, stores everything in a JSONB blob.
- Connect external profiles (GitHub, Google Scholar, Kaggle, LinkedIn) → those signals are aggregated and used as **evidence** when matching jobs and writing résumé bullets.
- Save a job (URL paste or manual) → SmartCV scrapes the posting, extracts skills with an LLM, and runs a two-phase **gap analysis** to score the user against it (matched / missing / partial skills + similarity score).
- Generate a tailored résumé for that job (with multiple PDF templates), a cover letter, a learning path for missing skills, a salary-negotiation script.
- Run **automated outreach campaigns** to the hiring team via a Chrome extension that drives LinkedIn UI clicks with humanised pacing.
- Configure **job-discovery preferences** and trigger a Playwright-based scan of LinkedIn / Indeed / Glassdoor that pulls candidate listings, scores them against the user, and seeds a **Recommended Jobs** panel on the dashboard.

It is a Django 5.2 monolith, built around a single central LLM hub (`profiles/services/llm_engine.py`) talking to **Groq** via LangChain. There is no general task queue — *all* LLM calls run synchronously in the request thread, with one exception (the job-board discovery runner, which spawns a daemon thread because Playwright requires its own asyncio loop).

Headline architectural decisions (and why):

- **JSONB profile (`UserProfile.data_content`)** instead of N rigid section tables. Lets the parser keep arbitrary CV sections without migrations and makes LLM round-trips easy.
- **Pure-LLM gap analysis with fuzzy reconciliation** instead of vectors. Vectors exist (pgvector 384-d) but are largely deprecated; the LLM reasons over the full profile + signal blocks.
- **Synchronous everything** + persistent DB connections + careful PgBouncer config. The whole stack is tuned to avoid stale connections and to ride 60-second TCP/TLS keepalives.
- **Anti-AI-tell discipline** is shared across every prose generator via `prompt_guards.HUMAN_VOICE_RULE` — same banned-word list, same closer rules, same opener rules. Single source of truth.
- **Two LinkedIn auth strategies coexist**: undetected-chromedriver + IMAP autofill for the *profile* scraper (requires logged-in session), and Playwright `storage_state` JSON saved by an interactive `python manage.py login_<source>` command for the *job-board search* scraper (LinkedIn job *search* is anonymous-public, but Indeed and Glassdoor both demand a saved session).

---

## 2. Repo layout

Top level:

```
SmartCV/
├── accounts/          # Custom UUID user model + email auth + outreach token rotation
├── analysis/          # Gap analysis, learning paths, salary negotiation
├── benchmarks/        # Reproducible D1–D5 + B + E evaluation suite
├── chrome_profiles/   # Runtime cache for undetected-chromedriver profile scraper (gitignored)
├── core/              # Landing, agent chat, action planner, career stage, middleware, metrics, health
├── docs/              # benchmarks.md, gap_analysis_system.md, implementation_plan.md, screenshots
├── extension-outreach/# Chrome MV3 extension (LinkedIn outreach + job-page discovery)
├── jobs/              # Job model, single-page scrapers, skill extractor, job_sources/ (Playwright), scoring
├── media/             # User uploads (CV files); gitignored
├── profiles/          # User profile, CV parsing, signal aggregators, chatbot, outreach, scrapers, suggesters
├── resumes/           # Tailored resume gen, cover letters, ATS scoring, PDF/DOCX exporters, templates
├── smartcv/           # Project config (settings.py, urls.py, wsgi.py, asgi.py)
├── static/            # Tailwind input.css + compiled output.css; small static JS
├── storage_state/     # Runtime cache for Playwright job-board scraper sessions (gitignored)
├── debug_dumps/       # Failed-scrape HTML dumps for selector debugging (gitignored)
├── templates/         # 64 Django HTML templates (base.html + per-app + components/)
├── benchmarks/results/# Per-day JSON snapshots of every eval run
├── benchmarks/fixtures/# 25 CVs (real + synthetic) + 30 JDs (5 hand + 25 auto-paired) + label files
├── manage.py          # WMI-disabled wrapper around django.core.management
├── requirements.txt   # Pinned deps
├── package.json       # Tailwind v4 standalone CLI ("@tailwindcss/cli")
├── .env / .env.example# python-decouple loads from .env
├── CLAUDE.md          # Project conventions for AI assistants
└── README.md
```

Apps in `INSTALLED_APPS` order: `accounts`, `jobs`, `profiles`, `analysis`, `resumes`, `core`. Each app follows the standard Django layout (`models.py`, `views.py`, `urls.py`, `services/`, `tests*.py`, `migrations/`, plus `management/commands/` where needed).

Stats (current):
- ~29,500 LOC of Python (excluding migrations)
- 64 templates
- 47 service modules across all apps
- 281 tests passing as of 2026-05-05

---

## 3. Tech stack & runtime

| Layer | Choice | Why |
|---|---|---|
| Web framework | Django 5.2.7+ | Full-stack, ORM, admin, templates, auth, middleware all in one place |
| Database | PostgreSQL via Supabase PgBouncer (port 6543, transaction pooling) | Hosted, with pgvector |
| Vector store | pgvector 0.4.2 (384-d, all-MiniLM-L6-v2) | Largely deprecated — kept for migration parity, gap analysis is pure-LLM |
| LLM | Groq API via `langchain-groq` (`ChatGroq`) | Low latency (~700 ms typical), cheap, structured-output via `with_structured_output()` |
| Default model | `meta-llama/llama-4-scout-17b-16e-instruct` | Tool-use capable, JSON-mode reliable, free tier sufficient for dev |
| Pydantic | v2.9+ | Required for Python 3.13 wheels and structured-output validation |
| API | Django REST Framework + SimpleJWT | Outreach API is token-authed; web UI is session-authed |
| Frontend | Tailwind v4 (CSS-first, no `tailwind.config.js`) + Alpine.js + Shepherd.js | No build pipeline beyond `npx tailwindcss`, component library in `templates/components/` |
| Static files | WhiteNoise (compressed manifest in prod) | No CDN required |
| CV parsing | pdfplumber + python-docx | Text extraction; LLM does the structuring |
| Single-page scrape | requests + BeautifulSoup + lxml; Playwright for Indeed | Fast for LinkedIn / Greenhouse / Lever; Playwright only when DOM hydration is required |
| Job-board scrape | Playwright async + persistent storage_state JSON | LinkedIn/Indeed/Glassdoor; threaded runner |
| Profile scrape | undetected-chromedriver + Selenium + IMAP autofill | LinkedIn profile scraper (heavy, opt-in via `LINKEDIN_SCRAPING_ENABLED`) |
| PDF export | xhtml2pdf 0.2.16+ (reportlab 4.x) | Pinned for Python 3.13 wheels |
| DOCX export | python-docx | Custom layout per template |
| Fuzzy match | rapidfuzz 3.5.2 | Used in skill extractor JD-anchoring + job-discovery prefilter + gap analysis reconciliation |
| Python | 3.13 | Some deps had to be unpinned for cp313 wheels (commit `e926fba`) |

Environment / build:

```bash
# Setup
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
npm install                                            # Tailwind CLI only
python -m playwright install chromium                  # for the job-board scraper
cp .env.example .env                                   # then fill DATABASE_URL, GROQ_API_KEY
python manage.py migrate
npm run build:css                                      # static/css/output.css
python manage.py runserver

# Job-board scraper one-time logins
python manage.py login_linkedin    # optional (LinkedIn job search is anonymous; login adds rate cushion)
python manage.py login_indeed
python manage.py login_glassdoor   # required (Cloudflare blocks anonymous)
```

Windows note (`manage.py:11–18`): Python 3.13's `platform._wmi_query` can hang indefinitely; `manage.py` stubs it before any imports because torch (transitively imported by `langchain_groq`) calls `platform.machine()` at import time and would otherwise hang every CLI invocation. Same workaround in `wsgi.py` and `asgi.py`.

---

## 4. The LLM layer

Single hub: **`profiles/services/llm_engine.py`** (115 lines).

### 4.1 Public API

```python
from profiles.services.llm_engine import get_llm, get_structured_llm, get_llm_client

llm = get_llm(temperature=0.3, max_tokens=4096, task=None)      # returns ChatGroq
structured = get_structured_llm(MyPydanticSchema, ...)          # returns ChatGroq.with_structured_output(MySchema)
legacy = get_llm_client(task=None)                              # returns shim mimicking openai-py's client.chat.completions.create
```

- `get_llm()` is for unstructured text generation (cover letters, salary negotiation, agent chat).
- `get_structured_llm(Schema)` returns a runnable whose `.invoke(prompt)` returns an instance of `Schema` directly — no manual JSON parsing.
- `get_llm_client(task=None)` is a backward-compat shim with `client.chat.completions.create(model=..., messages=...)`. Marked deprecated; call sites are being migrated to the modern API.

`max_retries=1, timeout=20s` are the safe defaults — Groq is fast; we'd rather fail and surface than retry-storm.

### 4.2 Per-task credential routing

Per-task routing exists so different LLM workloads can use **different Groq accounts** to spread per-account rate limits. Pattern: each call site passes a `task` string (e.g., `"agent_chat"`, `"learning_path"`, `"resume_gen"`, `"gap_analyzer"`).

Resolution (`_resolve_credentials`):

1. Compute `suffix = task.upper().replace("-", "_")`.
2. Look up `GROQ_API_KEY_<SUFFIX>` and `GROQ_MODEL_<SUFFIX>` from env.
3. Fall back to `GROQ_API_KEY` and `GROQ_MODEL` (default `meta-llama/llama-4-scout-17b-16e-instruct`).

Diagnostics endpoint at `/healthz/llm/` (staff-only) shows masked keys and which tasks have dedicated overrides.

Known tasks (a non-exhaustive list; each call site uses its own):
- `agent_chat`, `gap_analyzer`, `learning_path`, `resume_gen`, `cover_letter`, `salary`, `chatbot`, `interview`, `cv_validate`, `outreach_campaign`, `outreach_target`, `project_enricher`, `dedupe`, `prompt_guard`, `preference_suggester`, `judge` (used only by `benchmarks/llm_judge.py`).

### 4.3 Anti-AI-tell discipline (`profiles/services/prompt_guards.py`)

Single source of truth: `HUMAN_VOICE_RULE`. Eight rules:

1. **Banned words** — about 30 buzzwords, every single one a real recruiter pet-peeve: `leverage / utilize / synergy / robust / seamless / delve / unleash / elevate / spearhead / cutting-edge / world-class / paradigm / tapestry / holistic / ecosystem (figurative) / foster (figurative) / transformative / dynamic / innovative / passionate / results-driven / go-getter / thought leader / ...`. Each has a plain-English replacement listed inline.
2. **Banned closer pattern**: `<action>, demonstrating <skill>` — produced by every LLM resume generator on Earth. Replacement is concrete-result language. Substituting "leveraging" for "demonstrating" is also banned (same AI tell).
3. **Specificity rule** — every bullet must name at least one concrete thing (named tool / framework / dataset / metric / time-scoped result). Generic bullets like "Built reusable components to improve team productivity" are forbidden.
4. **Vary sentence structure** — no two bullets in the same role start with the same opening verb; of any 3 consecutive bullets, at least one must NOT start with a verb (lead with system name, outcome, or scale).
5. **No inside-out openers** — banned templates: `"With <N> years of experience in <X>, I bring..."`, `"As a <role> with <expertise>, I am passionate..."`, `"Driven by <quality>, I excel at..."`. The LinkedIn About-section giveaway.
6. **Summary tone** — banned eye-rolls: "highly motivated, results-oriented, detail-oriented, team player, self-starter, fast-paced environment". Show, don't claim. Lead with role + years + ONE concrete proof from the CV.
7. **No em dashes** — replace with comma or delete. (Real humans use em dashes; the rule exists because Groq llama-4 over-uses them in a tell-tale rhythm.)
8. **No first-person filler** — `"I am writing to express my interest"` and similar.

Helper: `append_human_voice(prompt) -> str` appends the rule block to the **end** of an existing prompt — placement at the very end keeps it in the model's last-attention window. Used by `resume_generator`, `cover_letter_generator`, `outreach_generator`, `salary_negotiator`.

### 4.4 Structured-output schemas (`profiles/services/schemas.py`, 466 lines)

The single file holds all Pydantic schemas the LLM ever returns. Critical ones:

- `ResumeSchema` — what a parsed CV looks like in `data_content` (full_name, email, phone, location, linkedin_url, github_url, portfolio_url, normalized_summary, objective, skills[], experiences[], education[], projects[], certifications[], languages[], extended sections like volunteer_experience / awards / publications / patents / military_experience / hobbies / references / courses).
- Sub-schemas: `Skill (name, proficiency, category)`, `Experience (title, company, location, start_date, end_date, description, highlights[])`, `Education (degree, field, institution, start_date, end_date, gpa, honors)`, `Project (name, description, technologies[], highlights[], url, start_date, end_date)`, `Certification (name, issuer, date, credential_id, url)`, `ItemDetailed (used for less-structured sections like awards, volunteer)`.
- `GapAnalysisResult` — `matched_skills, critical_missing_skills, soft_skill_gaps, similarity_score`. The output of `compute_gap_analysis`.
- `SkillListResult` — `skills: list[str]`. Used by `extract_skills`.
- `ExtractedExperienceBullet` — used by experience-rewrite calls.
- `ChatReplyAnalysis`, `ChatNextQuestion`, `ChatTurnResult` — chatbot interview state machine.
- `SemanticValidationResult` — used by `semantic_validator` to confirm an answer matches a question's topic.
- `GuardrailResult` — used by `prompt_guards`/audit calls.
- `OutreachCampaignResult` — `linkedin_message, cold_email_subject, cold_email_body`. Three free-form strings.
- `ResumeContentResult` (and its parts: `ResumeExperience, ResumeEducation, ResumeProject, ResumeCertification`) — the tailored resume schema. Description fields are **`List[str]`** (bullets), not strings — the textarea ↔ list conversion is in `resumes/views.py` (see §11).
- `SectionFilterResult` — used by `resume_generator` to decide which sections to keep.
- `LearningPathResult` (with `LearningPathItem`) — output of `generate_learning_path`. Includes `importance, resources[]`, `project_idea`, `time_estimate`.
- `EnrichedProject`, `EnrichedProjectBatch` — output of `project_enricher`.
- `DedupeDecision`, `DedupeBatch` — output of `project_dedupe`.
- `KeywordCandidate`, `SuggestedJobPreferences` — the new (May 2026) auto-fill suggester for the job-discovery preferences form. `keyword`, `keyword_candidates: List[KeywordCandidate(keyword, why)]`, `locations`, `experience_levels`, `workplace_types`, `rationale`.

`with_structured_output()` enforces the schema via Groq's tool-call mode. When Groq fails (`tool_use_failed`, often when the model emits multi-line JSON with apostrophes), most generators have a recovery path — see §11 (resume_generator) and §8 (outreach_generator) for the patterns.

---

## 5. Project configuration (`smartcv/settings.py`, 280 lines)

### 5.1 Bootstrap

```python
from decouple import config       # python-decouple reads .env
from dotenv import load_dotenv
load_dotenv()
BASE_DIR = Path(__file__).resolve().parent.parent
```

### 5.2 Secret + debug guard

```python
_DEFAULT_SECRET = 'django-insecure-default-key'
SECRET_KEY = config('SECRET_KEY', default=_DEFAULT_SECRET)
DEBUG = config('DEBUG', default=True, cast=bool)

_is_test_invocation = (
    'test' in sys.argv or sys.argv[0].endswith('pytest') or os.environ.get('PYTEST_CURRENT_TEST')
)
if SECRET_KEY == _DEFAULT_SECRET and not _is_test_invocation:
    raise ImproperlyConfigured("SECRET_KEY must be set ...")
```

The previous guard only fired when `DEBUG=False`. Tighter: any non-test invocation raises if `.env` is missing — so `runserver` with the placeholder no longer silently boots.

### 5.3 INSTALLED_APPS (declared order)

```
django.contrib.{admin, auth, contenttypes, sessions, messages, staticfiles}
+ rest_framework, rest_framework_simplejwt, corsheaders
+ accounts, jobs, profiles, analysis, resumes, core
[+ debug_toolbar — only when DEBUG and not in tests]
```

### 5.4 MIDDLEWARE (declared order — the order matters)

1. `SecurityMiddleware`
2. `WhiteNoiseMiddleware` — serves static files with gzip, plays nicely with Django dev server
3. `SessionMiddleware`
4. `CorsMiddleware` (corsheaders) — between sessions and common
5. `CommonMiddleware`
6. `CsrfViewMiddleware`
7. `AuthenticationMiddleware`
8. `MessageMiddleware`
9. `XFrameOptionsMiddleware`
10. **`core.middleware.RequestObservabilityMiddleware`** — *last*, so it observes the final status code and total duration. Defensive: any failure inside is swallowed so a metrics bug can't break responses.

When `DEBUG=True` and not in tests, `DebugToolbarMiddleware` is inserted at position 2 (after WhiteNoise).

### 5.5 Database

```python
DATABASES = {
    'default': dj_database_url.config(
        default=os.getenv('DATABASE_URL'),
        conn_max_age=60,
        conn_health_checks=True,
    )
}
DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'] = True
DATABASES['default']['OPTIONS'] = {'sslmode': 'require', 'connect_timeout': 10}
```

Why each setting matters (PgBouncer, transaction-pool mode):

- **`conn_max_age=60`** — without this, every request opens a new TCP+TLS connection to Supabase eu-west-1, which is 2–11 s on a clean cache. With 60 s persistence, only the first request after the timeout pays the handshake.
- **`conn_health_checks=True`** — Supabase's idle-timeout (~5–10 min) closes idle client connections silently. A cheap `SELECT 1` before reuse detects stale sockets and forces a reconnect rather than throwing `InterfaceError: connection already closed`.
- **`DISABLE_SERVER_SIDE_CURSORS=True`** — PgBouncer transaction pooling can't carry server-side cursors across connections.
- **`sslmode='require'`** — Supabase only accepts TLS.
- **`connect_timeout=10`** — when a previous Python process was force-killed, PgBouncer client slots aren't immediately reaped; without a timeout the boot hangs.

Tests get an in-memory SQLite DB instead. Triggered by `'test' in sys.argv`. Reason: PgBouncer blocks `CREATE DATABASE test_…` ("database is being accessed by other users"), and SQLite is fast enough that the test suite (281 tests) runs in ~130 s including the few selenium-based tests.

### 5.6 Auth

```python
AUTH_USER_MODEL = 'accounts.User'
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ('rest_framework_simplejwt.authentication.JWTAuthentication',)
}
```

### 5.7 Templates

```python
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
        'core.context_processors.onboarding',     # Custom: exposes in_onboarding session flag
    ]},
}]
```

### 5.8 Static / media / email

```python
STATIC_URL='/static/'; STATIC_ROOT=BASE_DIR/'staticfiles'; STATICFILES_DIRS=[BASE_DIR/'static']
if not DEBUG: STATICFILES_STORAGE='whitenoise.storage.CompressedManifestStaticFilesStorage'
MEDIA_URL='/media/'; MEDIA_ROOT=BASE_DIR/'media'
EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL='SmartCV <noreply@smartcv.local>'
CSRF_FAILURE_VIEW='core.views.csrf_failure'
```

### 5.9 Logging

Single console handler, format `[YYYY-MM-DD HH:MM:SS] LEVEL logger.name: message`. Root level INFO; Django's own loggers WARNING (to suppress request-served noise).

### 5.10 LinkedIn profile scraper config (opt-in)

```python
LINKEDIN_SCRAPING_ENABLED = config(..., default=False, cast=bool)
LINKEDIN_EMAIL = config(..., default='')
LINKEDIN_PASSWORD = config(..., default='')
LINKEDIN_HEADLESS = config(..., default=True, cast=bool)
LINKEDIN_USE_UNDETECTED = config(..., default=True, cast=bool)
LINKEDIN_LOGIN_WAIT = config(..., default=5.0, cast=float)
LINKEDIN_PAGE_WAIT = config(..., default=4.0, cast=float)
LINKEDIN_CHALLENGE_TIMEOUT = config(..., default=300.0, cast=float)
# IMAP autofill (used to read 6-digit codes from the email-verification challenge)
LINKEDIN_IMAP_USER = config(..., default='') or LINKEDIN_EMAIL
LINKEDIN_IMAP_PASSWORD = config(..., default='')
LINKEDIN_IMAP_HOST = config(..., default='')
LINKEDIN_IMAP_PORT = int(config(..., default='') or 993)         # tolerant of empty .env
LINKEDIN_IMAP_TIMEOUT = float(config(..., default='') or 120.0)
LINKEDIN_PROFILES_DIR = BASE_DIR / config(..., default='chrome_profiles')
```

### 5.11 Job-board scraper config

```python
JOB_SCRAPER_STORAGE_DIR     = BASE_DIR / config(..., default='storage_state')
JOB_SCRAPER_DEBUG_DUMPS_DIR = BASE_DIR / config(..., default='debug_dumps')
```

### 5.12 Top-level URL conf (`smartcv/urls.py`)

```python
urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('jobs/',    include('jobs.urls')),
    path('profiles/',include('profiles.urls')),
    path('analysis/',include('analysis.urls')),
    path('resumes/', include('resumes.urls')),
    path('', include('core.urls')),    # home, dashboard redirect, agent, applications, insights, healthz
]
handler404 = 'core.views.custom_404'
handler500 = 'core.views.custom_500'
# DEBUG-only: media files + django-debug-toolbar
```

---

## 6. App: `accounts` — custom user

Single model in `accounts/models.py`:

```python
class User(AbstractUser):
    id    = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    outreach_token = models.UUIDField(null=True, blank=True, unique=True, db_index=True)
    outreach_token_rotated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']
    class Meta: db_table = 'users'

    def rotate_outreach_token(self) -> uuid.UUID:
        self.outreach_token = uuid.uuid4()
        self.outreach_token_rotated_at = timezone.now()
        self.save(update_fields=['outreach_token', 'outreach_token_rotated_at'])
        return self.outreach_token
```

- UUID primary key (Django default `BigAutoField` overridden via field).
- Email-based login (`USERNAME_FIELD = 'email'`).
- `outreach_token` is the credential the Chrome extension uses to authenticate (unrelated to Django session). `rotated_at` lets the pairing UI surface "last issued X minutes ago"; there is no TTL on the token by design (long-running extensions shouldn't break overnight).
- `accounts.views`, `accounts.urls`, `accounts.admin` are minimal — most user flows live in `core/views.py` (login, register, password reset templates exist under `templates/accounts/`).

---

## 7. App: `core`

The dashboard backbone, agent chat, action planning, observability, health.

### 7.1 `core/views.py`

| View | Route | Purpose |
|---|---|---|
| `home_view` | `/` | If authenticated → redirect dashboard; else render `core/home.html` (landing page) |
| `dashboard_view` | `/dashboard/` | Legacy redirect → `profiles:dashboard` |
| `welcome_view` | `/welcome/` | First-run onboarding picker (upload CV / build by form / skip). Sets `session['has_seen_welcome']` and `session['in_onboarding']`. |
| `skip_onboarding_view` | `/skip-onboarding/` | Clears `in_onboarding` flag, redirects |
| `agent_chat_view` | `/agent/?job=<uuid>` | Renders `core/agent_chat.html` with optional job context. Validates UUID + ownership; redirects with warning if foreign |
| `agent_chat_api` | POST `/agent/api/` | Body `{history: [...], message: "...", job_id?: "..."}`. Validates job ownership (403 if foreign), calls `core.services.agent_chat.chat(...)`, returns `{reply}` or `{error}` on 502 |
| `applications_view` | `/applications/` | Kanban board; jobs grouped by status |
| `insights_view` | `/insights/` | Profile strength + top skills + recent gap analyses + recent resumes + evidence confidence |
| `custom_404`, `custom_500` | error handlers | Render branded `404.html`/`500.html` |
| `csrf_failure` | CSRF 403 page | Logs cause; renders `403_csrf.html` |
| `design_system_view` | `/design/` | Internal styleguide |

### 7.2 `core/middleware.py` — `RequestObservabilityMiddleware`

Last in the middleware chain. Per request:

1. Skip if path starts with `/static/`, `/media/`, or `/healthz`.
2. `t0 = time.perf_counter()`.
3. Call `get_response(request)` to obtain the response.
4. `duration_ms = (time.perf_counter() - t0) * 1000`.
5. Set `X-Response-Time-ms` header.
6. Build a low-cardinality route label (`f"{method} {url_name}"` from `resolver_match`, falling back to `f"{method} {path}"`).
7. `metrics.record(label, status, duration_ms)`.
8. Log SLOW (≥1500 ms) at INFO and VERY_SLOW (≥3000 ms) at WARNING.
9. Log 5xx at WARN, 4xx at INFO (skipping 401/404 to reduce noise).

Entire body is wrapped in try/except — a metrics bug can never break a response.

### 7.3 `core/metrics.py` — process-local counters

Thread-safe (`threading.Lock`). Each request increments per-route counters: total count, status code histogram, 4xx/5xx error counts, latency deque (`maxlen=500`).

`snapshot()` returns:

```json
{
  "total": {"requests": ..., "errors_4xx": ..., "errors_5xx": ..., "error_rate": ...},
  "routes": {
    "GET profiles:dashboard": {
      "count": ..., "by_status": {"200": ...}, "errors_4xx": ..., "errors_5xx": ...,
      "error_rate": ..., "latency_ms": {"p50": ..., "p95": ..., "p99": ..., "samples": <=500}
    }, ...
  }
}
```

`_percentile()` is linear-interpolation (matches `benchmarks/_io.percentile()` so live and benchmark numbers are directly comparable).

### 7.4 `core/health.py`

Four endpoints:

- `GET /healthz/` — liveness. Always 200, no DB. For load-balancer pings.
- `GET /healthz/deep/` — readiness. Runs `SELECT 1`, returns `{checks: {db: {ok, latency_ms, error?}}, status: ok|degraded}` and HTTP 200/503. `@cache_page(15)` so external monitors don't hammer the pool.
- `GET /healthz/metrics/` — staff-only JSON dump of `metrics.snapshot()`.
- `GET /healthz/llm/` — staff-only LLM task→key/model routing diagnostic. Lists `KNOWN_LLM_TASKS`, calls `_resolve_credentials(task)`, returns masked keys + `dedicated`, `model_overridden`, summary counts.

### 7.5 `core/services/agent_chat.py` — global career agent

`chat(user, history, user_message, job=None) -> ChatResult` (lines 228–257):

1. `system = build_system_prompt(user, job=job)`
2. Convert history + new message to LangChain messages.
3. `llm = get_llm(temperature=0.6, max_tokens=700, task="agent_chat")`
4. `result = llm.invoke([SystemMessage(system), HumanMessage(user_message), ...])`
5. Return `ChatResult(reply=result.content, error=None)` or `ChatResult(reply='', error=str(exc))` on failure.

`build_system_prompt(user, job)` assembles:

- `_profile_summary(profile)` — name, location, top 15 skills, most recent role, education.
- `_signals_summary(profile)` — GitHub (repos, stars, languages, 90d commits), Scholar (citations, h-index, top 5 publications), Kaggle (tier, comp/dataset/notebook counts with medals).
- `_applications_summary(user)` — job counts by status: "Application pipeline: N saved, M applied, ...".
- `_build_job_context_block(job)` (if job provided) — title, company, status, required skills, gap analysis (if cached), profile snapshot diff (master vs job-specific), artifacts (resume? cover letter?).

Output prompt instructs the agent to be warm, direct, evidence-first, concise.

### 7.6 `core/services/action_planner.py`

`get_recommended_actions(user) -> list[dict]`. Rules engine; no ML. Returns 5 prioritized P0–P3 cards.

Pre-fetches in 3 bulk queries to avoid N+1: gap_map = {job_id: GapAnalysis}, resume_job_ids = set, cover_letter_job_ids = set.

Rule order (each emits one recommendation):

- No profile → **P0** "Upload your CV"
- No jobs → **P0** "Add your first job"
- Per job:
  - No gap analysis → **P1** "Run AI gap analysis"
  - Gap < 50% → **P1** "Build missing skills" (learning path)
  - 50% ≤ Gap < 80% → **P1** "Improve your profile" (chatbot)
  - Gap ≥ 50% AND no resume → **P1** "Generate tailored resume"
  - Resume exists AND no cover letter → **P2** "Create cover letter"
  - Job in saved status for ≥3 days → **P2** "Ready to apply?"
  - Job status = `offer` → **P1** "Negotiate your offer"
  - Job status in (saved, applied) → **P3** "Reach out" (outreach)

Sort by priority, return top 5.

### 7.7 `core/services/career_stage.py`

`detect_stage_for_dashboard(profile, kanban_boards) -> CareerStage`. Six stages, picked in priority order:

| Key | Trigger | Primary CTA | Tone |
|---|---|---|---|
| `getting_started` | no master profile | Upload your CV | brand |
| `offer_in_hand` | ≥1 offer | Negotiate {company} | success |
| `interviewing` | ≥1 interviewing (overrides applying) | Prep for {company} | accent |
| `actively_applying` | ≥1 saved or applied | Add a new job | brand |
| `reflecting` | only rejected jobs | Build a learning path | neutral |
| `ready_to_look` | profile complete, no jobs | Show your agent a job | brand |

Each stage returns a `CareerStage` TypedDict with `key, label, detail, primary_label, primary_href, primary_route?, tone, secondary_actions: list[StageAction]`.

`secondary_actions` for `interviewing` deep-link to chatbot/gap/agent for the most recent interviewing job. For `offer_in_hand`, deep-link to that offer's negotiator. Etc.

### 7.8 `core/context_processors.py`

Single function `onboarding(request)` returns `{'in_onboarding': bool(request.session.get('in_onboarding'))}`. Set to True by `welcome_view`, cleared by `skip_onboarding_view`. Templates use it to render the "Skip onboarding" button.

---

## 8. App: `profiles` — the centre of gravity

The largest app. Owns the user profile, all signal aggregation, the chatbot, the LinkedIn profile scraper, outreach generation/dispatch, project enrichment, project dedupe, profile strength scoring, and the new (May 2026) job-discovery preference suggester.

### 8.1 Models (`profiles/models.py`)

#### UserProfile

Single JSONB blob is the design. Per-section tables would force migrations every time the LLM started returning a new resume section.

```python
class UserProfile(models.Model):
    id            = UUIDField(primary_key=True, default=uuid.uuid4)
    user          = ForeignKey(User, on_delete=CASCADE)
    full_name     = CharField(max_length=256)
    input_method  = CharField(max_length=32, choices=['upload','linkedin','url','manual'])
    github_url    = URLField(blank=True)
    data_content  = JSONField(default=dict)               # the everything blob
    embedding     = VectorField(384, null=True)            # legacy (largely deprecated)
    embeddings_multi = JSONField(default=dict)            # multi-vector design (replaces single embedding)
    created_at, updated_at = ...
```

Property accessors on the model expose `data_content` keys as attributes — `profile.skills`, `profile.experiences`, `profile.education`, `profile.projects`, `profile.certifications`, `profile.summary`, `profile.normalized_summary`, `profile.location`, `profile.linkedin_url`, `profile.github_url`, etc. They read from `data_content`; some delegate to dedicated fields.

`data_content` keys actually written (non-exhaustive):

- **Core fields**: `full_name, email, phone, location, linkedin_url, github_url, portfolio_url, other_urls`
- **Sections**: `skills, experiences, education, projects, certifications, languages, volunteer_experience, awards, publications, speaking_engagements, patents, military_experience, hobbies, references, courses`
- **Text content**: `summary, normalized_summary, objective`
- **Enrichment signals (cached)**: `github_signals, kaggle_signals, scholar_signals, linkedin_signals` (each is a sub-dict with timestamps and either data or `{error: …}`)
- **User confirmation flags**: `completed_skills (sorted list of done learning-path skills), confirmed_projects, projects_confirmed_at`
- **Enrichment cache**: `enriched_projects_cache, enriched_projects_hash, dedupe_decisions`
- **Onboarding state**: `has_seen_welcome, has_seen_tour, onboarding_banner_dismissed`
- **Learning path persistence**: `learning_path, learning_path_skills, learning_path_generated_at`
- **Misc**: `parser_warnings, raw_text`

#### JobProfileSnapshot

Per-job profile variant — captures the user's customisation for a specific job (chatbot answers, hand edits) so it can be rolled back.

```python
class JobProfileSnapshot(models.Model):
    id = UUIDField(...)
    profile = ForeignKey(UserProfile)
    job = OneToOneField(jobs.Job)
    data_content = JSONField()        # snapshot of UserProfile.data_content at update time
    pre_chatbot_data = JSONField()    # pre-update state for "this job only" rollback
    created_at = ...
```

#### OutreachCampaign

```python
class OutreachCampaign(models.Model):
    id, user, job
    status            = CharField(choices=['draft','running','paused','done','failed'])
    daily_invite_cap  = PositiveIntegerField(default=10)   # rolling 24h, NOT calendar day
    created_at, updated_at, last_activity_at
```

State machine: `draft → running ↔ paused → done | failed`. Auto-completes (`_maybe_finish_campaign`) when no queued/in_flight actions remain.

#### OutreachAction

```python
class OutreachAction(models.Model):
    id
    campaign         = ForeignKey(OutreachCampaign)
    target_handle    = CharField(128)               # LinkedIn vanity slug
    target_name      = CharField(256, blank=True)
    kind             = CharField(32)                # 'linkedin_connect', 'email', ...
    status           = CharField(choices=['queued','in_flight','sent','accepted','skipped','failed'])
    attempts         = PositiveIntegerField(default=0)   # incremented per claim
    connect_message  = TextField()                  # LLM-generated note
    follow_up_message= TextField()
    queued_at, completed_at
```

State: `queued → in_flight → (sent | failed | skipped)`. Failed terminal only after 3 attempts. Composite index on `(campaign, status)`.

#### OutreachActionEvent

Append-only audit log. Admin overrides `has_add_permission` and `has_change_permission` to return False — events can only be written by the dispatcher.

Fields: `action FK, from_status, to_status, actor (extension|server_dispatch|server_finish), reason, detail, attempts_after, created_at`.

#### DiscoveredTarget

```python
class DiscoveredTarget(models.Model):
    id, user, job
    handle = CharField(128)                          # LinkedIn vanity
    name, role
    source = CharField(choices=['hiring_team','people_you_know','company_people','manual'])
    discovered_at = ...
    class Meta:
        unique_together = ('user', 'job', 'handle')
```

Lightweight pre-queue — the Chrome extension scrapes LinkedIn job pages and pushes targets here. Distinct from `OutreachAction`: a `DiscoveredTarget` may never be promoted to an action (user reviewed and dismissed it).

#### JobPreferences (added May 2026)

```python
class JobPreferences(models.Model):
    id                 = UUIDField(...)
    user               = OneToOneField(User, related_name='job_preferences')
    keyword            = CharField(200, blank=True)
    locations          = JSONField(default=list)        # list[str]
    sources            = JSONField(default=list)        # subset of ['linkedin','indeed','glassdoor']
    date_posted        = CharField(choices=['any','24h','week','month'], default='week')
    experience_levels  = JSONField(default=list)        # subset of ['internship','entry','associate','mid_senior','director','executive']
    workplace_types    = JSONField(default=list)        # subset of ['onsite','remote','hybrid']
    max_jobs           = PositiveIntegerField(default=30)
    last_scan_at       = DateTimeField(null=True)
    last_scan_failed_at= DateTimeField(null=True)
    scan_failure_count = PositiveIntegerField(default=0)
    created_at, updated_at
    class Meta: db_table = 'job_preferences'
    def to_params(self) -> dict: ...   # shape consumed by jobs.services.job_sources.runner
```

`to_params()` returns the dict the runner reads.

### 8.2 Views (`profiles/views.py`, ~1300 lines)

Major routes (named):

- `profile_input_choice (job_id)` — choose CV upload vs manual form per-job
- `profile_upload_cv (job_id)` — upload CV, parse, redirect to review
- `profile_manual_form (job_id)` — manual entry form
- `profile_chatbot (job_id)` — chatbot UI
- `chatbot_api` — POST endpoint for chat turns; calls `interviewer.process_chat_turn` (see §8.4.6)
- `chatbot_scope_decision (job_id)` — "this job only" vs "update master" decision
- `upload_master_profile` — onboarding step 2 of 4
- `review_master_profile` — onboarding step 3 of 4
- `connect_accounts_view` — onboarding step 4 of 4 (GitHub / LinkedIn / Scholar / Kaggle)
- `dashboard` — the dashboard. Pre-fetches: kanban_boards, top_skills, recommended_jobs, profile_strength, career_stage, next_actions, has_job_preferences, active_scrape_job_id, scan_failure_banner.
- `dismiss_onboarding_banner_view`, `dismiss_tour_view` — POST AJAX
- `generate_outreach_view`, `outreach_campaign_view` — outreach UI
- `refresh_github_signals`, `refresh_linkedin_signals`, `refresh_scholar_signals`, `refresh_kaggle_signals` — POST AJAX to re-pull signals
- `enrich_from_signals_view` — POST `/api/projects/enrich-from-signals/`
- `projects_review_view` — review enriched projects (kept reachable but no longer in default flow; auto-merge happens silently)
- `job_preferences_view` — GET shows form (auto-seeds from CV on first GET); POST saves
- `job_sources_setup_view` — read-only page showing which job-board sources have a saved storage_state
- `suggest_job_preferences_view` — POST `/preferences/jobs/suggest/`. Calls `preference_suggester.suggest_job_preferences(profile)`, returns JSON.

The dashboard view also auto-fires a retry scrape on load when `JobPreferences.last_scan_failed_at` is set and the backoff window has elapsed (30 min → 2 h → 12 h → manual); see §13.6.

#### `profiles/views_outreach_api.py` — extension-facing API

Token-authed endpoints (use `X-Outreach-Token` header validated against `User.outreach_token`):

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/outreach/next` | GET | Atomic claim of next queued action. 200 with action JSON, 204 if empty, 401/403 if token wrong, 429 with Retry-After if rate-limited (Groq), 5xx on server error |
| `/api/outreach/result/<uuid:action_id>/` | POST | Body `{status, error?, detail?, trace?}`. Updates action status, records event, maybe finishes campaign |
| `/api/outreach/discovery/push/` | POST | Body `{linkedin_job_id, targets: [{handle, name, role, source}]}`. Resolves LinkedIn job ID to `Job.url__icontains='/jobs/view/{id}'`, upserts `DiscoveredTarget` rows |
| `/api/outreach/discovery/<uuid:job_id>/` | GET | Web UI polls every few seconds. Returns `{targets: [...], count: N}` |
| `/api/outreach/campaigns/` | POST | Create campaign, queue actions |
| `/api/outreach/draft-target/` | POST | Manual paste of LinkedIn handle when discovery returned nothing. Calls `generate_outreach_for_target()` |
| `/api/outreach/campaigns/<uuid>/pause/` | POST | Pause |
| `/api/outreach/campaigns/<uuid>/retry/` | POST | Retry failed actions |
| `/api/outreach/campaigns/<uuid>/status/` | GET | Status snapshot |
| `/extension/pair/` | GET | Pairing UI — shows current outreach_token, button to rotate |

### 8.3 `profiles/forms.py` — `JobPreferencesForm` (`ModelForm`)

Captures the form state and converts to/from the JSONField columns:

- `keyword` — CharField (rendered with Tailwind classes inline)
- `locations_text` — comma-separated string, cleaned to `list[str]` in `clean_locations_text`. Stored in `instance.locations` in `save()`.
- `sources` — `MultipleChoiceField` (LinkedIn / Indeed / Glassdoor); checkbox group; default `['linkedin']`.
- `experience_levels` — `MultipleChoiceField` (internship/entry/associate/mid_senior/director/executive). LinkedIn-only.
- `workplace_types` — `MultipleChoiceField` (onsite/remote/hybrid). LinkedIn-only.
- `date_posted` — RadioSelect (any / 24h / week / month).
- `max_jobs` — IntegerField, range 1–200 (200 cap), default 30.

Helper `seed_defaults_from_profile(prefs, profile)`:
- If `prefs.keyword` empty → take latest experience title.
- If `prefs.locations` empty → `[profile.location]` or `['Remote']`.
- If `prefs.sources` empty → `['linkedin']`.
- If `prefs.workplace_types` empty AND no location → `['remote']`.

### 8.4 Services (`profiles/services/`)

24 modules. The big ones:

#### 8.4.1 `cv_parser.parse_cv(file_path: str) -> dict`

Wraps an external CVExtractor library. Returns a 17-key dict matching `ResumeSchema`. Skills are flattened from `{category: [skill...]}` to `[{name, proficiency=None, category}]`. Location is extracted from `personal_information.address`. **LLM refinement is intentionally skipped here** — `llm_validator` does that downstream so we have the raw text available.

#### 8.4.2 `llm_validator.validate_and_map_cv_data(parsed_data, raw_cv_text) -> dict`

Takes the parser's output + the raw extracted text and runs an LLM pass to:
- Backfill missing fields (e.g., GitHub URL inferred from a profile photo URL? rare).
- Re-extract a clean `normalized_summary` if the parser didn't produce one (defensive: synthesises from raw text if LLM omits it; observation `1280` shows the safety net).
- Coerce types (e.g., dates).
- Assemble extended sections (volunteer, awards, publications, etc.) into `data_content`.

Output fully matches `ResumeSchema`.

#### 8.4.3 `embeddings.py`

Two embedding helpers (kept as stubs): `generate_profile_embeddings(profile_id)` and similarity helpers. **Largely deprecated** — gap analysis is pure LLM. The `embedding` field still exists on `UserProfile` and `Job` for migration parity but no flow reads it actively.

#### 8.4.4 `experience_math.compute_years_of_experience(experiences) -> float`

Sum of (end_date - start_date) across experiences, **handling overlapping periods correctly** (this was a bug; April 17 commit `85b9788` introduced 22 regression tests). Uses `_parse_year_month` for tolerant date parsing — accepts `2024-06`, `Jun 2024`, `2024`, `present`, `current`, `now`.

#### 8.4.5 Signal aggregators

Same shape across all four:

```python
def fetch_<service>_snapshot(handle_or_url, top_n=...) -> dict
```

Each builds a `requests.Session()` per call (no shared session — see PgBouncer connection-recycling parallel), pulls public profile data, returns a dict, and caches into `profile.data_content[<service>_signals]`.

- **`github_aggregator`** — GitHub REST API (no token in dev; rate limits apply). Returns `{username, public_repos, total_stars, recent_commits_90d, language_breakdown, top_repos: [{name, stars, language, description}, ...]}`. Used by gap analyzer's `_format_github_activity()`.
- **`linkedin_aggregator`** — Reads LINKEDIN_USE_UNDETECTED setting and toggles between the standalone profile scraper and a no-op stub. The aggregator is the entry point; the actual scraping happens in `linkedin_scraper.py`.
- **`scholar_aggregator`** — Google Scholar (citations, h-index, i10-index, top 5 publications). Brittle to Google's anti-scraping; handles 429 / capture-redirect.
- **`kaggle_aggregator`** — Kaggle competitions/datasets/notebooks/discussions with medal counts per tier.

All four are **synchronous, blocking the request thread**. Several views chain them sequentially during onboarding (`connect_accounts_view`).

#### 8.4.6 `interviewer.py` — chatbot turn processor

`process_chat_turn(profile, job, history, user_message) -> ChatTurnResult`

Multi-pass LLM workflow:

1. `ChatReplyAnalysis` — classify the user reply (answered? skipped? asked back?).
2. `semantic_validator.validate_answer_semantically(question, answer, topic)` — LLM check that the user actually answered the topic (not "yeah whatever" to "what was the team size?").
3. If valid, write back to `data_content` (and to a `JobProfileSnapshot` if the user chose "this job only").
4. `ChatNextQuestion` — pick the next question from a curriculum (impact, scale, tools used, ownership boundary, conflict resolved, etc.). Stops at 4–6 turns.

Output: `ChatTurnResult(reply_text, profile_updated, update_label, next_question, done)`.

#### 8.4.7 `outreach_generator.py`

Two entry points:

- `generate_outreach_campaign(job, max_attempts=1)` — single-call LLM, returns `{campaign_message: str, ...}` with default copy.
- `generate_outreach_for_target(target: Target)` — per-target tailoring; returns `{connect_message, follow_up_message}`.

Both call `get_structured_llm(OutreachCampaignResult, temperature=0.7, max_tokens=1024, task='outreach_*')`. Three-tier fallback recovery:

1. Structured LLM call.
2. On Groq `tool_use_failed`: parse `error.body.failed_generation` JSON salvage.
3. Retry with `get_llm` (no function-calling) + plain-JSON instruction.

Helper `_get_skill_names(skills)` accepts both dict and string skill formats. `HUMAN_VOICE_RULE` appended at end via `prompt_guards.append_human_voice`.

#### 8.4.8 `outreach_dispatcher.py` — dispatch / cap / state machine

```python
def invites_sent_today(user) -> int                # rolling 24h count, NOT calendar day
def claim_next_action(user) -> OutreachAction|None # atomic SELECT FOR UPDATE on oldest queued
def record_action_result(action, status, reason)   # writes status, increments attempts, writes event
def _maybe_finish_campaign(campaign)               # transitions to done/failed when no queued/in_flight remain
```

Cap enforcement: `max(daily_invite_cap)` across the user's running campaigns. Failed terminal only after 3 attempts.

State invariant: every status transition writes an `OutreachActionEvent` with from_status, to_status, actor, reason, attempts_after.

#### 8.4.9 `linkedin_scraper.py` — profile scraper (4-phase login)

Drives undetected-chromedriver on LinkedIn. Heavy, opt-in via `LINKEDIN_SCRAPING_ENABLED`. Used only for *profile* scraping (the user pasting a LinkedIn URL of someone they want enriched), not for job-board search.

Login pipeline (`_ensure_logged_in`):

1. **Phase 1**: cookies-only — try with the persistent profile dir at `LINKEDIN_PROFILES_DIR`. If `/feed` resolves, done.
2. **Phase 2a**: form login — fill email + password on `linkedin.com/login`. Wait for redirect.
3. **Phase 2b** (added May 2026): **email verification challenge**. If LinkedIn shows the "We sent a code to your email" page, call `_try_imap_autofill(driver, imap_creds)`:
   - Connect to IMAP host (`imaplib.IMAP4_SSL`).
   - Search recent messages from `noreply@linkedin.com`.
   - 4-pattern code regex + 6-digit fallback.
   - Mark message read after consuming.
   - Type code into the verification input.
4. **Phase 3**: hard-CAPTCHA — wait `LINKEDIN_CHALLENGE_TIMEOUT` seconds for human resolution (UI shows "we're waiting on you, hit refresh after").

ChromeDriver version mismatch fix (commit `f4696ac`): `_detect_chrome_major()` reads the local Chrome version from the Windows registry; passed as `version_main=` to `uc.Chrome()`. Without it, `undetected-chromedriver` downloads the latest driver which may not match the installed Chrome.

`scrape_profile(profile_url, imap_creds=None) -> dict` returns `{full_name, headline, summary, current_position, experiences, education, projects, featured: [...]}`.

#### 8.4.10 `email_verification.py`

Standalone module for the IMAP autofill flow. Public API: `imap_credentials_present(creds) -> bool`, `fetch_latest_linkedin_code(creds, lookback_minutes=10) -> str|None`. Auto-detects Gmail/Outlook/Yahoo/iCloud hosts. Stdlib-only (`imaplib`, `email`).

#### 8.4.11 `project_enricher.py`

`enrich_profile(profile, force=False) -> List[EnrichedProject]`. Reads cached results unless `force=True`. LLM call schema = `EnrichedProjectBatch`. Each enriched project gets `name, description, technologies, metrics, source` (where source ∈ github_signals, linkedin_signals, kaggle_signals).

Cache key: SHA1 of (signals snapshot timestamps) → stored as `data_content.enriched_projects_hash`. Output stored in `data_content.enriched_projects_cache`.

#### 8.4.12 `project_dedupe.py`

`dedupe_projects(typed_projects, enriched_projects) -> list[DedupeDecision]`. Per pairwise (typed, enriched) candidate, the LLM picks `add_new | merge | keep_existing | keep_new` with a confidence score and reason.

`auto_apply_enriched_projects(profile)` (called by `connect_accounts_view`) silently merges based on the LLM's verdict — there is no manual review screen any more (the user explicitly asked for hands-off behaviour). Only emits a small banner if rows changed.

#### 8.4.13 `profile_strength.py`

`compute_profile_strength(profile, user) -> ProfileStrength`. Pure function, no DB writes.

Three components:

- `_score_completeness()` — has required sections (name, email, education, ≥1 experience, ≥3 skills, etc.).
- `_score_evidence()` — strength of supporting evidence (skills with proficiency, experiences with highlights, projects with technologies).
- `_score_signals()` — external platform signals (GitHub, LinkedIn, Scholar, Kaggle) presence + non-error.

Sum clamped 0–100. `_tier(score)` → `incomplete | developing | strong | exceptional`.

`_top_actions(components)` returns 3 CTAs ranked by impact, each with `label, description, href` (HREF_BY_KEY dict maps section keys to `/profiles/setup/review/`).

Used by `dashboard`, `insights_view`, `profile_strength_ring.html`, `profile_strength_breakdown.html`.

#### 8.4.14 `profile_auditor.py`

Quality audit: detect missing sections, duplicate skills (case-insensitive), inconsistent dates (start > end), unrealistic GPAs, etc. Returns a list of warnings stored in `data_content.parser_warnings`.

#### 8.4.15 `preference_suggester.py` (added May 2026)

LLM-driven auto-fill for `JobPreferencesForm`. Public API:

```python
def suggest_job_preferences(profile) -> dict
# returns: {keyword, keyword_candidates, locations, experience_levels, workplace_types, rationale}
```

`_build_profile_summary(profile)` extracts:
- `location`
- `summary` (first 600 chars of normalized_summary || summary)
- `skills` (top 30 names)
- `experiences` (top 5 with title, company, start, end, location)
- **`projects`** (top 6 with name + technologies up to 8 — added so the LLM sees what the user *built*, fixing the bug where Flutter users got "IoT" suggestions)
- `github_signals.languages` and `github_signals.top_repos` (top 6 each, defensively handles list-of-strings, list-of-dicts, dict shapes)
- `linkedin_signals.headline` and `linkedin_signals.current_position`

LLM prompt is anchored on **dominant skill cluster + projects, not isolated buzzwords**. Explicit GOOD/BAD examples in-prompt. Returns `SuggestedJobPreferences` schema; `_coerce_suggestion` does defensive cleanup:

- `_clean_keyword(raw)` — strips parens/brackets, slashes, seniority words (`junior, jr, senior, sr, lead, staff, principal, intern, internship, trainee, graduate, grad, entry, mid, associate`), caps at 4 words. Empty → fall back to most-recent role title.
- Candidates: dedupe case-insensitively, cap at 5; ensure `keyword` is at index 0.
- `experience_levels`: clamp to valid enum; fall back to YOE-based heuristic via `_estimated_years_of_experience(experiences)`.
- `workplace_types`: clamp; default to all three if empty.

Backed by 5 unit tests in `jobs/tests.py::PreferenceSuggesterCleanupTests`.

#### 8.4.16 `prompt_guards.py`

Discussed in §4.3. Single source of truth: `HUMAN_VOICE_RULE`, `append_human_voice(prompt)`.

#### 8.4.17 `semantic_validator.py`

`validate_answer_semantically(question, answer, topic) -> SemanticValidationResult`. Used by chatbot to detect topic-dodging.

### 8.5 Tests

`profiles/tests.py` (~870 lines, 124 tests), `tests_interviewer.py`, `tests_outreach.py`, `tests_prompt_guards.py`. Cover: CV parsing flow, fuzzy match, GitHub snapshot, profile strength scoring, profile auditor warnings, IMAP autofill (9 tests), outreach state transitions (12 tests), prompt guard rule compliance.

---

## 9. App: `jobs`

Manages job sourcing, extraction, skill analysis, and recommendation. Three pipelines:

1. **Single-page job scraping** — user pastes a URL, app extracts title/description/skills.
2. **Job-board discovery scanning** — Playwright background scraper that searches LinkedIn/Indeed/Glassdoor.
3. **Recommended jobs scoring + dedup** — converts scraped listings into RecommendedJob rows.

### 9.1 Models

```python
class Job(models.Model):
    id, user, url(2000), title(200), company(200,null), description, raw_html
    extracted_skills = JSONField(default=list)
    embedding = VectorField(384, null=True)             # legacy
    application_status = CharField(['saved','applied','interviewing','offer','rejected'], default='saved')
    created_at
    class Meta: db_table = 'jobs'; ordering = ['-created_at']

class RecommendedJob(models.Model):
    id, user, url(2000), title(200), company(200,null), description
    match_score = IntegerField(help_text="0-100 from gap_analysis similarity_score * 100")
    status = CharField(['new','saved','dismissed'], default='new')
    created_at
    class Meta: db_table = 'recommended_jobs'; ordering = ['-match_score','-created_at']

class ScrapeJob(models.Model):                          # added May 2026
    id, user
    created_at, finished_at(null)
    params_json = JSONField(default=dict)               # JobPreferences.to_params() at queue time
    status = CharField(['pending','running','done','error','cancelled'])
    progress_pct, total_steps, completed_steps
    current_step(255), message(255), error
    cancel_requested = BooleanField(default=False)
    class Meta: db_table = 'scrape_jobs'

class JobListing(models.Model):                         # added May 2026
    id, scrape_job FK
    source(32), title(512), company(512), company_url(2000), location(512), country(128)
    posted(128), salary(255), url(2000), description, raw_text
    scraped_at, unique_hash(64)
    class Meta:
        db_table = 'job_listings'
        unique_together = ('scrape_job', 'unique_hash')   # SHA1 dedup within a scrape
        indexes = [Index(['scrape_job', 'source'])]
    @staticmethod
    def make_hash(source, url, title, company, location) -> str:
        key = url.strip() if url else f"{source}|{title}|{company}|{location}"
        return hashlib.sha1(key.encode('utf-8',errors='ignore')).hexdigest()
```

**Critical contract**: when `RecommendedJob` rows are upserted from a new scrape, if a row already exists with `status='saved'` or `'dismissed'`, the user's status is **never overwritten** — only metadata + match_score refresh. This is what makes the "Dismiss" button stick across re-scans.

### 9.2 Single-page scrapers (`jobs/services/scrapers/`)

Used by `job_input_view`. Dispatcher pattern: each scraper module exposes `matches(url) -> bool` and `scrape(url) -> dict`.

`scrapers/dispatcher.py:scrape_job(url)`:

```python
_SCRAPERS = [
    ('linkedin',   linkedin),     # linkedin.com/jobs
    ('indeed',     indeed),       # indeed.com (Playwright; needs JS-rendered DOM)
    ('greenhouse', greenhouse),   # boards.greenhouse.io (public JSON API)
    ('lever',      lever),        # jobs.lever.co (public JSON API)
    ('generic',    generic),      # JSON-LD JobPosting fallback
]
```

`scrapers/base.py` provides shared utilities:

- `DEFAULT_HEADERS` (Chrome UA + Accept), `DEFAULT_TIMEOUT=10`
- `ScrapeError` — user-facing exception
- `fetch(url, ...)` — `requests.get` with 2 retries, sleep on 429, raise on 4xx, log on 5xx
- `html_to_text(html_or_soup)` — BeautifulSoup → plain text, collapsed whitespace
- `normalize_result(source, **fields) -> dict` — standardised output shape

Per-source notes:

- **LinkedIn** (`scrapers/linkedin.py`): converts URLs to canonical `/jobs/view/{id}/`, requests with no auth (public job pages), parses `h3.sub-nav-cta__header / a.sub-nav-cta__optional-url / div.show-more-less-html__markup / ul.description__job-criteria-list`.
- **Indeed** (`scrapers/indeed.py`): extracts `jk` from URL; needs Playwright to wait for JS hydration; selectors `h1` (title), `div[data-company-name="true"]` (company), `div[data-testid="job-location"]` (location), `#jobDescriptionText` (description). Detects Cloudflare challenge text and raises ScrapeError with friendly copy.
- **Greenhouse** (`scrapers/greenhouse.py`): extracts board token + job ID; calls public API `boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}`. Greenhouse ToS explicitly permits this.
- **Lever** (`scrapers/lever.py`): public API `api.lever.co/v0/postings/{org}/{posting_id}`.
- **Generic** (`scrapers/generic.py`): walks `<script type="application/ld+json">` blocks, finds JobPosting object (handles @graph, lists, single objects), parses standard schema.org fields. Workday / SmartRecruiters / Ashby work here.

### 9.3 `jobs/services/skill_extractor.py` (176 lines)

Three-stage pipeline:

1. **LLM call**: `get_structured_llm(SkillListResult, temperature=0.0, max_tokens=512, task='skill_extractor')`. Prompt instructs: extract technical skills only, map to canonical names, ban soft skills.
2. **Denylist filter**: `_GENERIC_SOFT_SKILL_DENYLIST = {"technical leadership", "problem solving", "problem-solving", "communication", "teamwork", "collaboration", "code review", "pair programming", "pairing sessions", "mentorship", "leadership"}`. If skill in denylist AND not in JD text, drop. Each entry was a real benchmark hallucination.
3. **JD-anchoring filter** (`_is_jd_anchored(skill, jd_lower)`):
   - **Pass 1**: full skill name as substring of JD. `"PostgreSQL"` ∈ `"we use postgresql for storage"` → ✓.
   - **Pass 2**: trim boilerplate suffixes (` pipelines`, ` api`, ` workflows`, ` testing`, ` clients`, ` sessions`), retry. `"REST API"` → `"REST"` finds `"design rest endpoints"`.
   - **Pass 3**: every word >2 chars in skill present in JD (word-anchor). `"Tailwind CSS"` survives if `"tailwind"` and `"css"` both appear.
   - All three fail → drop as hallucination.

Output: list of canonical skill names. Empty input → return `[]` (no LLM call). LLM exception → return `[]`.

Also exports a `SKILL_KB` (about 100 lines) — large curated alias map for fallback canonicalisation. Unused in the primary flow now (LLM does canonicalisation), kept for reference.

### 9.4 `jobs/services/job_sources/` — Playwright job-board scraper (new May 2026)

A wholesale port of an external production scraper, adapted for SmartCV's multi-tenant model.

#### `base.py`

```python
@dataclass
class JobRecord:
    source: str          # 'LinkedIn' | 'Indeed' | 'Glassdoor'
    title, company, company_url, location, country, posted, salary, url, description, raw_text: str

@dataclass
class ProgressReporter:
    on_step: Callable[[int, str], None] | None
    on_total: Callable[[int], None] | None
    on_cancel_check: Callable[[], bool] | None

CHROMIUM_LAUNCH_ARGS = ['--disable-blink-features=AutomationControlled', ...]
DEFAULT_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/131.0.0.0 ...'
DEFAULT_VIEWPORT = {'width': 1366, 'height': 900}
DEFAULT_HEADERS = { Accept, Accept-Language, Accept-Encoding, Sec-Ch-Ua, ... }

STEALTH_INIT_SCRIPT = r"""
  Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
  Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
  ...
"""

async def make_stealth_context(browser, state_source: str | None = None):
    """Apply stealth UA/headers/viewport. If state_source matches a saved storage_state, load cookies."""

def debug_dump(source, location, html) -> str | None:
    """Save HTML to settings.JOB_SCRAPER_DEBUG_DUMPS_DIR for selector-drift triage."""

LINKEDIN_DATE_MAP = {'any':'', '24h':'r86400', 'week':'r604800', 'month':'r2592000'}
LINKEDIN_EXP_MAP  = {'internship':'1','entry':'2','associate':'3','mid_senior':'4','director':'5','executive':'6'}
LINKEDIN_WT_MAP   = {'onsite':'1','remote':'2','hybrid':'3'}
INDEED_DATE_DAYS  = {'any':None,'24h':1,'week':7,'month':30}

def extract_salary(text) -> str:                 # regex over $/€/£ amounts
```

#### `auth.py`

```python
def _state_dir() -> Path                           # settings.JOB_SCRAPER_STORAGE_DIR
def state_path(source) -> Path                     # storage_state/<source>.json
def has_saved_state(source) -> bool                # exists AND >50 bytes
def STATE_DIR() -> Path                            # backward-compat constant accessor
```

#### `linkedin.py`

```python
async def scrape_linkedin(keyword, location, *, experience_levels=[], workplace_types=[],
                          date_posted='any', max_jobs=50, fetch_details=True, reporter=None) -> List[JobRecord]
```

Algorithm:
1. Build URL `linkedin.com/jobs/search/?keywords=...&location=...&f_E=...&f_WT=...&f_TPR=...&position=1&pageNum=0`.
2. Launch Playwright (headless, stealth). LinkedIn job *search* is anonymous-public — context does **not** load `state_source`.
3. Navigate, then `_human_scroll(page, MAX_SCROLL_ATTEMPTS=20, reporter)`: scroll to bottom, sleep 1.5–2.5 s, click `button.infinite-scroller__show-more-button` if visible, repeat until height stops growing.
4. `BeautifulSoup(html).find_all('div', class_='base-card')[:max_jobs]`.
5. `_parse_card(card, country=location)`: extract title from `a.base-card__full-link span.sr-only` or `h3.base-search-card__title`; company from `h4.base-search-card__subtitle a`; location from `span.job-search-card__location`; posted from `time.job-search-card__listdate` or `time`; URL from `a.base-card__full-link href` (query-stripped).
6. If `fetch_details=True`: parallel detail fetches with semaphore `DETAIL_CONCURRENCY=3` (LinkedIn rate-limits guest detail pages aggressively). Each fetch opens a new page, waits for `div.description__text, div.show-more-less-html__markup`, extracts plain text + extracts salary if not yet found.
7. Reporter `step(1, "LinkedIn: <title>")` per card.

Constants: `MAX_SCROLL_ATTEMPTS=20`, `SCROLL_PAUSE=(1.5,2.5)`, `DETAIL_TIMEOUT=25`, `NAV_TIMEOUT=30`.

#### `indeed.py`

```python
async def scrape_indeed(keyword, location, *, date_posted='any', max_jobs=50, reporter=None) -> List[JobRecord]
```

Region routing: `INDEED_COUNTRY_HOSTS` maps location strings to country-specific subdomains (`'berlin' → 'de.indeed.com'`, `'cairo' → 'eg.indeed.com'`, etc., 30+ hosts). Without this, the global `.com` refuses queries for many non-US locations.

Pagination: 10 cards per page. URL: `{base}/jobs?q=...&l=...&start={start}&fromage={days}` where `days = INDEED_DATE_DAYS[date_posted]`.

Card selectors (try in order until one matches):
```
div.job_seen_beacon
div[data-testid='slider_item']
li[data-testid='jobListItem']
div.cardOutline
td.resultContent
```

Per card: title from `h2.jobTitle` (multiple variants), company from `span[data-testid='company-name']`, location from `div[data-testid='text-location']`, URL rebuilt as `/viewjob?jk={data-jk-attr-or-extracted-from-href}`.

Loads with saved storage_state via `make_stealth_context(browser, state_source='indeed')`. Saves debug HTML on zero-cards via `debug_dump`.

#### `glassdoor.py`

```python
async def scrape_glassdoor(keyword, location, *, max_jobs=30, reporter=None) -> List[JobRecord]
```

**Hard requirement**: `has_saved_state('glassdoor')` must be True. Cloudflare blocks anonymous scrapers; the first thing the function does is short-circuit with a logger warning + empty list.

Location-resolution dance: Glassdoor's URL needs `locId+locT` (numeric IDs), not a string. The function navigates to homepage, calls `_resolve_glassdoor_location(page, 'Berlin')` which hits two undocumented endpoints (`findPopularLocationAjax.htm` and `searchsuggest/typeahead`) to get the IDs, then builds the search URL.

Card selectors (multiple fallbacks because Glassdoor rotates DOM frequently). URL cleaning preserves only `jl=` (canonical job listing id), strips tracking params (`src, srs, ao, s, guid, pos, t, vt, ea, uido, cb, jobListingId`).

Modal dismissal: `_try_dismiss_modal(page)` clicks any of `button[data-test='modal-close']`, `button[alt='Close']`, etc. (Glassdoor pops a sign-up modal aggressively).

#### `runner.py` — the threaded runner

`start_in_thread(scrape_job_id) -> Thread` — daemon worker. The whole point of this module: Playwright is async, Django ORM is sync, and the user's request thread needs to return immediately (the scan can take 30–120 s).

`run(scrape_job_id)`:

1. Set Windows `ProactorEventLoopPolicy` (Playwright requirement).
2. `close_old_connections()` — own DB connection.
3. Set env `DJANGO_ALLOW_ASYNC_UNSAFE=true` (otherwise ORM raises `SynchronousOnlyOperation` from inside the asyncio loop). This is the **only** place in the project that's permitted to do this — see the CLAUDE.md addendum.
4. Load `ScrapeJob`, `params = scrape_job.params_json`.
5. Set `coarse_total = max_jobs * len(sources) * len(locations)`. Update `STATUS_RUNNING`.
6. For each `(source, location)` combo:
   - Check `cancel_requested` → mark `STATUS_CANCELLED` and exit.
   - Build `ProgressReporter(on_step, on_total, on_cancel_check)` that writes to ScrapeJob.
   - Call the async scraper via `asyncio.run(scrape_<source>(...))`.
   - For each returned `JobRecord`: `_save_listing(scrape_job, rec)` → `JobListing.objects.update_or_create(scrape_job=, unique_hash=hash, defaults=...)`.
7. `score_listings_for_user(user_id, scrape_job_id)` (see §9.5) — converts top-K listings to `RecommendedJob` rows.
8. Update ScrapeJob to STATUS_DONE with message `"Saved N listings · M recommendations"`.
9. Update `JobPreferences.last_scan_at`. If all sources failed, set `last_scan_failed_at = now()` and increment `scan_failure_count`. If any succeeded, reset `last_scan_failed_at = None` and `scan_failure_count = 0`.
10. `close_old_connections()` (in `finally`).

Per-source/per-location exceptions are caught and logged to `ScrapeJob.message` but don't kill the run — partial successes are surfaced.

### 9.5 `jobs/services/job_scoring.py` (180 lines)

`score_listings_for_user(user_id, scrape_job_id, top_k=10) -> int`:

1. Load `UserProfile`. If missing → return 0.
2. Load `JobListing`s for this scrape.
3. **Cheap rapidfuzz prefilter** (`_prefilter`, `PREFILTER_TOP_N=20`, `PREFILTER_MIN_SCORE=40.0`):
   - Build profile haystack (`_profile_signal_text`): skills + recent role titles + summary, lowercased.
   - For each listing: `score = fuzz.partial_ratio((title + raw_text)[:2000].lower(), haystack)`.
   - Drop everything < 40. Sort descending. Keep top 20.
4. For each survivor:
   - `extract_skills(description or raw_text)` → `extracted_skills` (best-effort; exceptions logged not raised).
   - Build `_CandidateJob(title, company, description, extracted_skills)` adapter (quacks like Job for `compute_gap_analysis`).
   - `result = compute_gap_analysis(profile, candidate)`. Score = `result['similarity_score']`.
5. Sort scored descending, take top K (default 10).
6. For each: `_upsert_recommendation(user_id, listing, score)`:
   - Normalise URL via `url_normalizer.normalize_url(listing.url)`.
   - Lookup existing `RecommendedJob.objects.filter(user_id=, url=normed).first()`.
   - **If existing has status `saved` or `dismissed`**: refresh title/company/description/match_score, **preserve status**.
   - **Else**: create new with `status='new'`, or update existing with `status='new'`.

Returns count of rows created/updated.

### 9.6 `jobs/services/url_normalizer.py`

```python
def normalize_url(url: str) -> str:
    # Indeed: keep only ?jk=... (without it Indeed serves a different page)
    # Glassdoor: keep only ?jl=... (without it Glassdoor serves "Job is OOO" placeholder)
    # Default: drop query + fragment, strip trailing slash
```

Tested in `jobs/tests.py::UrlNormalizerTests`.

### 9.7 Views (`jobs/views.py`, ~430 lines)

| View | Route | Purpose |
|---|---|---|
| `job_input_view` | `/input/` | URL paste OR text paste. Calls `scrape_job(url)` → `extract_skills(desc)` → `Job.objects.create(...)`. Redirects to `review_extracted_job`. |
| `review_extracted_job` | `/review/<job_id>/` | User confirms scraped data; if description changed, busts embedding + re-extracts skills. Redirects to gap analysis. |
| `job_detail_view` | `/<job_id>/` | Detail page. POST updates `application_status`. |
| `job_delete_view` | `/delete/<job_id>/` | Delete. |
| `save_job_extension_view` | `/api/v1/extension/save-job/` | DRF endpoint for the original extension (legacy; outreach extension uses different routes). |
| `update_job_status_api` | `/api/v1/update-status/` | Kanban drag-drop AJAX. |
| `scan_recommended_jobs` | POST `/recommend/scan/` | Creates `ScrapeJob`, calls `runner.start_in_thread`. Returns 202 with `scrape_job_id`. Returns 400 if no preferences. Returns 202 with `already_running=True` if a scrape is already in flight. |
| `scrape_status` | GET `/recommend/scrape/<id>/status/` | Polling endpoint. Returns `{id, status, progress_pct, completed_steps, total_steps, current_step, message, is_terminal, recommendations_count?, error?}`. |
| `scrape_cancel` | POST `/recommend/scrape/<id>/cancel/` | Cooperative cancel. Sets `cancel_requested=True`. |
| `recommended_save` | POST `/recommend/<rec_id>/save/` | Promote RecommendedJob → Job (status='saved'). Best-effort skill extraction. Sets rec.status='saved'. |
| `recommended_dismiss` | POST `/recommend/<rec_id>/dismiss/` | Sets rec.status='dismissed'. |
| `recommended_detail` | GET `/recommend/<rec_id>/` | Render `templates/jobs/recommended_detail.html`. |

**URL declaration order matters** in `jobs/urls.py`: the `recommend/...` routes must be declared *before* the `<uuid:job_id>/` catch-all, otherwise `/recommend/scan/` would route to `job_detail_view`.

### 9.8 Management commands (`jobs/management/commands/`)

| Command | Purpose |
|---|---|
| `_login_base.py` | Shared `InteractiveLoginCommand` base + `run_login(source, login_url, success_url_substring, post_login_check, fresh_profile, allow_bundled)`. Launches a *persistent* browser context backed by a real user-data dir, prefers system Chrome (`channel='chrome'`) over bundled Chromium (Cloudflare fingerprints bundled). Polls `page.url` for up to 10 minutes; saves `context.storage_state(path=state_path(source))`. Flags: `--fresh` (wipe profile dir), `--allow-bundled`. |
| `login_linkedin.py` | source='linkedin', login_url='https://www.linkedin.com/login', success_url_substring='login', post_login_check='[data-test-global-nav], header.global-nav'. |
| `login_indeed.py` | source='indeed', login_url='https://secure.indeed.com/auth', success_url_substring='secure.indeed.com'. |
| `login_glassdoor.py` | source='glassdoor', login_url='https://www.glassdoor.com/profile/login_input.htm', success_url_substring='login'. |
| `discover_jobs.py` | Cron-friendly: `--user <id_or_email>` or `--all-users [--max-users N]`. Iterates JobPreferences-having users, creates ScrapeJob, calls `runner.start_in_thread`, polls `is_terminal` with 2 s sleep until `--timeout` (default 900 s). Sequential per user. |

### 9.9 Tests

`jobs/tests.py` (~509 lines):

- `IsJdAnchoredTests` (7 tests) — JD anchoring filter rules
- `ExtractSkillsFilterTests` (6 tests, mocked LLM) — denylist + anchoring
- `GenericSoftSkillDenylistTests` (2 tests) — denylist content sanity
- `UrlNormalizerTests` (4 tests) — Indeed/Glassdoor special cases + default
- `JobScoringTests` (3 tests, mocked gap_analyzer + extract_skills) — top-K persistence, dedup with status preservation, empty input
- `JobPreferencesFormSeedTests` (2 tests) — seed from CV
- `PreferenceSuggesterCleanupTests` (5 tests) — `_clean_keyword` strips parens/seniority/slashes/caps-words

Total: 281 tests across all apps; all green.

---

## 10. App: `analysis`

### 10.1 Models

```python
class GapAnalysis(models.Model):
    id, job FK, user FK
    matched_skills = JSONField(default=list)           # skills user has AND job requires
    missing_skills = JSONField(default=list)           # critical technical gaps
    partial_skills = JSONField(default=list)           # soft/negotiable gaps
    similarity_score = FloatField(default=0.0)         # 0.0–1.0 rounded to 2 dp
    created_at
    class Meta:
        db_table = 'gap_analyses'
        unique_together = ('job', 'user')              # one per (user, job)
```

### 10.2 Views

| View | Route | Purpose |
|---|---|---|
| `gap_analysis_view` | GET `/gap/<job_id>/` | Renders `analysis/gap_analysis.html` with cached GapAnalysis. If `?refresh=1` recomputes. Computes `evidence` (0–3 confidence from GitHub/Scholar/Kaggle). |
| `compute_gap_api` | POST `/api/compute/<job_id>/` | Calls `analysis.tasks.compute_gap_analysis_task()` synchronously. Returns `{success: True}` or 500 with `{error, retryable: True}`. |
| `update_gap_skills` | POST `/api/update-skills/<job_id>/` | Body `{matched_skills, missing_skills, soft_skill_gaps}`. Persists, recomputes `similarity_score = (matched + 0.5*soft) / total` clamped 0–1, returns counts + new score. Used by the drag-and-drop UI. |
| `generate_learning_path_view` | GET/POST `/learning-path/[<job_id>/]` | GET: pools missing skills across user's gap analyses, takes top 5 by frequency. POST: calls `learning_path_generator.generate_learning_path(skills)`, persists to `data_content.learning_path`. |
| `mark_skill_complete_view` | POST `/api/learning-path/skill-done/` | Body `{skill}`. Toggle inclusion in `data_content.completed_skills`. |
| `negotiate_salary_view` | GET/POST `/negotiate/<job_id>/` | GET form, POST calls `salary_negotiator.generate_negotiation_script(profile, job, current_offer, target_salary)`, returns the email script. |

### 10.3 `analysis/services/gap_analyzer.py` — the heart of the matching engine

`compute_gap_analysis(profile, job) -> dict` (returning):
```python
{
  'matched_skills': List[str],           # in JD's spelling
  'missing_skills': List[str],
  'partial_skills': List[str],           # currently empty; reserved
  'soft_skill_gaps': List[str],
  'critical_missing_skills': List[str],  # alias = missing_skills[:5]
  'seniority_mismatch': None,
  'similarity_score': float,             # 0.0–1.0 rounded to 2 dp
  'analysis_method': 'llm' | 'fallback' | 'no_job_skills' | 'empty_profile',
}
```

#### Phase 1: LLM-driven gap analysis

Context assembled by `_build_full_candidate_context(profile)`:

```
=== CANDIDATE SKILLS ===
- Python (5 yrs, Expert)
- Django (3 yrs)
- ...

=== WORK EXPERIENCE ===
1. Senior Backend Engineer at Acme (2022-2024) — built X, scaled Y, ...
   Highlights:
   - cut p95 latency from 800 ms to 120 ms by adding ...
   - ...
[top 5 experiences]

=== PROJECTS ===
1. taskflow (Python, Django, Postgres) — ...
[top 5 projects]

=== CERTIFICATIONS ===
[top 10]

=== EDUCATION ===
[top 3]

=== GITHUB ACTIVITY ===  (built by _format_github_activity)
- Username: zeyad
- Public repos: 42
- Total stars: 187
- Recent commits (90 days): 312
- Top repos:
  - smartcv (Python, 89 stars) — AI-powered career assistant
  - ...

=== GOOGLE SCHOLAR ===  (only if non-empty)
=== KAGGLE ===          (only if non-empty)
```

LLM prompt (verbatim core):

```
You are an expert technical recruiter. Compare the candidate's FULL profile against the job requirements.

JOB TITLE: {job.title}
JOB COMPANY: {job.company or 'Unknown'}
JOB REQUIRED SKILLS: {json.dumps(job_skills)}

{candidate_context}

=== YOUR TASK ===
1. Identify MATCHED SKILLS — skills the candidate demonstrably has (from skills list, experience, projects, OR certifications).
2. Identify CRITICAL MISSING SKILLS — hard technical skills the candidate clearly lacks.
3. Identify SOFT SKILL GAPS — soft skills required but missing.
4. Provide a similarity_score from 0.0 to 1.0 representing overall job fit.

=== CRITICAL MATCHING RULES ===

RULE 1 — HOLISTIC EVIDENCE:
A skill is MATCHED if the candidate demonstrates it ANYWHERE:
- Explicitly in CANDIDATE SKILLS
- In WORK EXPERIENCE highlights/descriptions
- In PROJECT highlights/technologies
- In CERTIFICATIONS
- GITHUB ACTIVITY (a language with multiple repos OR top repo)
- GOOGLE SCHOLAR (publication on a topic implies deep knowledge)
- KAGGLE (competition tier + medals prove domain expertise)
- Foundational prerequisite of skills they have

RULE 2 — DIRECTIONAL SPECIFICITY:
- Job requires BROAD ("SQL") + candidate has SPECIFIC ("PostgreSQL") → MATCH
- Job requires SPECIFIC ("Tableau") + candidate has BROAD ("Data Visualization") → NO MATCH

RULE 3 — NO DUPLICATES:
Each skill in exactly ONE list. Use JOB REQUIRED SKILLS spelling.

RULE 4 — CASE-INSENSITIVE: "PySpark" = "pyspark"; don't list separately.

RULE 5 — SENIORITY & CAREER-SWITCH SIGNALS go to soft_skill_gaps. Keep <20 words, constructive tone.

=== SIMILARITY SCORE RUBRIC ===
Let M = len(matched_skills), X = len(missing_skills), T = M + X.
Base score = M / T (rounded to nearest 0.05).
Adjustments:
- Subtract 0.05 per soft_skill_gaps entry (cap −0.15 total)
- Add 0.05 if M >= 0.7 * T AND GitHub/Scholar/Kaggle corroborate ≥2 matched skills
- Final score in [0.0, 1.0]
```

Schema = `GapAnalysisResult`. Config: `temperature=0.1, max_tokens=1500, task='gap_analyzer'`.

#### Phase 2: Programmatic reconciliation

After the LLM returns:

1. **Dedup**: any skill in both matched and missing → drop from missing.
2. **Reconciliation**: for each `job_skill` not in matched OR missing, run `difflib.get_close_matches(job_skill, matched_set, n=1, cutoff=0.85)`. If found, count as matched (typo / synonym). Else add to missing. Logged at INFO.
3. **100% coverage invariant**: every `job_skill` ends up in one of matched/missing.

#### Fallback (no LLM)

If the LLM call raises (rate limit, timeout, validation), pure fuzzy fallback:

1. `difflib.get_close_matches(job_skill, profile_skills, n=1, cutoff=0.8)` for each.
2. Found → matched. Else → missing.
3. `similarity_score = matched / total_job_skills` rounded to 2 dp.
4. `analysis_method = 'fallback'`.

#### Early exits

- `not job_skills` → `similarity_score=0.0, analysis_method='no_job_skills'`.
- `not candidate_context.strip()` → `similarity_score=0.0, analysis_method='empty_profile'`.

### 10.4 `analysis/services/learning_path_generator.py`

`generate_learning_path(skills_list) -> List[dict]` returning per skill:

```python
{
  'skill': 'React',
  'importance': '...1-2 sentences...',
  'resources': [
    {'name': 'React Official Documentation', 'url': 'https://react.dev/', 'provider': 'Official docs'},
    {'name': '...', 'url': '...', 'provider': 'Coursera|Udemy|edX|YouTube|MDN|...'},
  ],
  'project_idea': '...1-2 sentences...',
  'time_estimate': '10–15 hours over 2 weeks',
}
```

Schema: `LearningPathResult` (with `LearningPathItem`). Config: `temperature=0.3, max_tokens=2048, task='learning_path'`.

**Anti-hallucination rule** in prompt: only output resources for the listed skills; URLs must be plausibly real; if unsure, fall back to provider's base URL. Never invent a course slug.

**Recovery path**: Groq sometimes emits a bare top-level list of items instead of `{items: [...]}`. On `tool_use_failed`, parse `error.failed_generation` JSON, detect bare list vs wrapped form, validate each item through `LearningPathItem`. If recovery fails, return `[]`.

### 10.5 `analysis/services/salary_negotiator.py`

`generate_negotiation_script(profile, job, current_offer, target_salary) -> str`:

LLM prompt instructs to write a polite, persuasive email rooted in the candidate's specific skills + market rate. Banned words enforced. Anti-hallucination rule: never invent skills/metrics not in the profile.

Config: `get_llm(temperature=0.7, max_tokens=2048, task='salary')` — *unstructured* (returns plain email text).

### 10.6 `analysis/services/skill_score.py`

`compute_match_score(matched_count, missing_count, soft_count) -> float`:

```
total = m + x + s
score = (m + 0.5 * s) / total  if total > 0 else 0.0
return round(score, 4)
```

Used by `update_gap_skills` after drag-and-drop, and by Alpine in `gap_analysis.html` for live preview (same formula, different language).

---

## 11. App: `resumes`

### 11.1 Models

```python
class GeneratedResume(models.Model):
    id
    gap_analysis = ForeignKey(GapAnalysis)              # ties resume to a specific job
    name = CharField(default='Tailored Resume')
    content = JSONField()                               # ResumeContentResult shape
    html_content = TextField(blank=True)                # deprecated; templates render on-the-fly
    ats_score = FloatField()                            # 0–100
    version = IntegerField(default=1)
    created_at
    class Meta: ordering = ['-created_at']

class CoverLetter(models.Model):
    id, job FK, profile FK
    content = TextField()
    created_at
    class Meta: ordering = ['-created_at']
```

### 11.2 Views (`resumes/views.py`, 763 lines)

Highlights:

- `generate_resume_view (job_id)` — GET form, POST renders generating-state; the front-end then POSTs to `trigger_resume_generation_api`.
- `trigger_resume_generation_api (job_id)` — synchronous call to `resumes.tasks.generate_resume_task(job_id, user_id)`. Returns `{success, resume_id}`.
- `resume_preview_view (resume_id)` — render preview with chosen template.
- `resume_edit_view (resume_id)` — GET form, POST save. Two key transformations:
  1. **Auto-sync**: if `profile.updated_at > resume.created_at`, regenerate the entire resume via LLM. Else call `_ensure_profile_data_preserved()` to merge supplemental fields (location, GPA, project tech, etc.) by positional index from master profile.
  2. **List ↔ string conversion** (commit `fd90299`): description fields are stored as `List[str]` (bullets), but textareas need `\n`-joined strings. The dedicated helpers are:

```python
def _description_text_to_list(raw: str) -> List[str]:
    """Normalise CRLF / CR / LF, split, trim, drop empties."""

def _description_list_to_text(value) -> str:
    """List → '\\n'.join. None/empty → ''. String → returned as-is (legacy compat)."""
```

The bug this fixes: prior versions called `str(my_list)` which produced `"['line one', 'line two']"` and the *bracket notation literally appeared in the textarea*. The helpers are tested in `resumes/tests.py`.

- `update_section_order_view (resume_id)` — body `{order: [...]}`. Validates against `RESUME_SECTION_KEYS` whitelist (`summary, skills, experience, education, projects, certifications, languages`) so a typo can't poison saved state. Append missing whitelisted keys at end (partial orders supported).
- `regenerate_section_view (resume_id, section)` — `section ∈ {professional_summary, skills, experience, projects}`. Body optional `{current_content: {...}}` so the LLM sees the user's working draft. Validates non-empty regeneration.
- `export_pdf_view (resume_id)` — calls `pdf_exporter.generate_pdf(resume_obj, output_path, template_name)`. xhtml2pdf renders the chosen template. On error, render branded error page with retry + DOCX fallback.
- `export_docx_view (resume_id)` — calls `docx_exporter.generate_docx(resume_obj)`.
- `generate_cover_letter_view (job_id)` — LLM gen + persist `CoverLetter`.
- `cover_letter_preview_view (letter_id)`.
- `resume_list_view`, `resume_delete_view`.

Helpers also include `_normalize_legacy_resume_content(resume)` which migrates older resumes with string descriptions to `List[str]` format on read.

### 11.3 `resumes/services/resume_generator.py` (959 lines)

`generate_resume_content(profile, job, gap_analysis) -> dict` matching `ResumeContentResult`.

Three-stage process:

1. **Domain detection** (no LLM) — keyword classifier on job title + description. Domains: `software_engineering, data, design, product, marketing, sales, finance, general`. Each emits a domain-specific prompt section.
2. **Evidence-grounded LLM tailoring** — slim CV (signal blobs surfaced separately) + GitHub/Scholar/Kaggle blocks + gap analysis matched/missing skills. Config: `temperature=0.7, max_tokens=8192, task='resume_gen'`.
3. **Fallback + data preservation** — if LLM fails, `_build_offline_fallback()` emits a deterministic resume (no fabrication). Either way, `_ensure_profile_data_preserved()` patches missing supplemental fields from master profile.

Critical prompt rules (verbatim core):

```
=== EVIDENCE-GROUNDED ENRICHMENT RULE ===
Every concrete claim (number, tool, scale, metric) must be supported by AT LEAST ONE source:
  (a) CV bullets/skills/education/projects
  (b) GITHUB ACTIVITY block
  (c) GOOGLE SCHOLAR block
  (d) KAGGLE block
  (e) GAP ANALYSIS block

When JD emphasises a skill the candidate has, enrich the bullet using corroborating evidence.
Example: "Built a model" → "Modelled churn across 2M events" (if CV mentions 2M users elsewhere)
                       OR "across 12 production repos" (if GitHub shows that)

If NO source supports a claim, keep it qualitative. Never fabricate numbers, team sizes, companies, outcomes, or tools.

=== REMOVE FROM RESUMES ===
- Street/home address
- Objective statements
- Graduation year if >10 years old
- Experience older than 15 years (20 max for execs)
- High school (unless no university degree)
- GPA/grades (unless top 10% AND <3 yrs out)
- Headshot
- Salary expectations
- First-person "I" statements

=== LANGUAGE & STYLE ===
[HUMAN_VOICE_RULE appended at end via prompt_guards.append_human_voice]
```

Recovery: `_recover_resume_from_failed_generation(exc, profile, job, gap_analysis)` — on Groq `tool_use_failed`, parse `exc.body.error.failed_generation`, validate against `ResumeContentResult` schema. If still invalid, fall back to offline.

### 11.4 `resumes/services/cover_letter_generator.py`

Single LLM call returning a 3-paragraph cover letter as plain text (no schema). Config: `temperature=0.7, max_tokens=2048, task='cover_letter'`. Banned words enforced via `HUMAN_VOICE_RULE`. Anti-hallucination rule.

### 11.5 `resumes/services/scoring.py` — ATS scoring

`compute_ats_breakdown(resume_content, job) -> {score: int 0-100, ...}`:

Algorithm (deterministic, no LLM):

1. **Required-keyword overlap** — what fraction of `job.extracted_skills` appears in the resume content (case-insensitive). Heavy weight.
2. **Action-verb diversity** — count unique strong verbs at the start of bullets (the inverse of "every bullet starts with 'Built'").
3. **Length penalty** — too short (<400 words) or too long (>700 words) loses points.
4. **Section coverage** — missing summary/skills/experience/education each costs points.
5. **Stuffing penalty** — `STUFFING_THRESHOLD=4` (per-keyword count above which to flag), `STUFFING_PENALTY_PER_SKILL=-5` (deduction). Flat penalty per stuffed skill.

Tested by `benchmarks/ats_eval.py` (synthetic; verifies determinism σ=0, matched-vs-mismatched Cohen's d, stuffing fires on 6× repetition).

### 11.6 `resumes/services/pdf_exporter.py` and `pdf_generator.py`

xhtml2pdf renders `templates/resumes/pdf_template_<name>.html`. Templates:

| Template | Style |
|---|---|
| `pdf_template.html` | Standard — single-column, classic |
| `pdf_template_compact.html` | Tighter spacing, two-column header |
| `pdf_template_minimalist.html` | Whitespace-heavy, serif name, thin dividers |
| `pdf_template_executive.html` | Header accent bar, sidebar for key info |
| `pdf_template_danette.html` | Designer template with blue accents |
| `pdf_template_zeyad.html` | Bold sans-serif, modern |

All use `@page` CSS for A4 sizing. Section-by-section rendering with `{% for section in section_order %}`. PDF-safe fonts.

### 11.7 `resumes/services/docx_exporter.py`

python-docx layout per template. Programmatic styling: paragraphs, runs, fonts, indents.

### 11.8 Tests

`resumes/tests.py` (~1869 lines, very thorough). Covers:
- List ↔ string conversion (the textarea bug)
- Section order whitelist enforcement
- ATS scoring determinism + scoring formula
- Resume migration of legacy string-description format
- Auto-sync logic (regenerate vs merge)
- Per-section regeneration

---

## 12. Frontend & design system

### 12.1 Tailwind v4 CSS-first

`static/src/input.css` (140 lines):

```css
@import "tailwindcss";

@source "../../templates/**/*.html";
@source "../../**/*.py";

@custom-variant dark (&:where(.dark, .dark *));

@theme {
  /* Typography scale (xs–6xl) */
  --text-xs:  0.75rem;
  --text-6xl: 4.5rem;
  /* ...full scale... */

  /* Brand (blue) */
  --color-brand-50:  #eff6ff;
  --color-brand-500: #3b82f6;
  --color-brand-700: #1d4ed8;
  /* ...full 50–950... */

  /* Accent (purple) */
  --color-accent-500: #8b5cf6;
  /* ...full 50–950... */

  /* Semantic */
  --color-success-500: #16a34a;
  --color-warning-500: #d97706;
  --color-danger-500:  #dc2626;

  /* Neutrals — cool slate-tinted (not pure white/black) */
  --color-neutral-50:  #f1f5f9;     /* page bg */
  --color-neutral-100: #e8edf3;     /* tile bg */
  --color-neutral-200: #cbd5e1;     /* hairline rules */
  /* ...rest follow Tailwind default... */

  /* Legacy rn-* tokens — preserved during phased redesign */
  --color-rn-blue:    #2f5cf8;
  --color-rn-navy:    #15255c;
  --color-rn-gold:    #ffcd7d;
  --radius-rn-pill:   58px;

  /* Fonts */
  --font-sans:    "Inter Variable", "Inter", ui-sans-serif, ...;
  --font-display: "Fraunces Variable", "Fraunces", Georgia, ...;
  --font-mono:    "IBM Plex Mono", ui-monospace, ...;
}

/* Card elevation on light-mode rings (shadow boost for cool-gray bg) */
@layer base { .ring-1.bg-white { box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.07), 0 2px 4px -2px rgb(0 0 0 / 0.05); } }
```

Compiled to `static/css/output.css` (~87 KB). Built file is committed so `python manage.py runserver` works without running npm; rebuild with `npm run build:css` after template changes.

Dark mode: opt-in via `dark` class on `<html>`. Toggle in `base.html` reads localStorage / system preference, sets `dark` class, updates `<meta name="color-scheme">`. FOUC prevention by inline script in `<head>`.

Display elements opt into Fraunces with `font-display` utility + `style="font-variation-settings: 'opsz' 80;"` to control optical sizing.

### 12.2 `templates/base.html` (769 lines) — page chrome

Structure:

- `<head>`: dark-mode FOUC script, fonts (Inter, Fraunces, IBM Plex Mono), Tailwind output.css, Alpine v3 + alpinejs/collapse plugin defer-loaded, Shepherd v11.2 defer-loaded.
- Sticky nav: brand wordmark (Fraunces italic 's' in rotated brand square + "SmartCV"), centre links (Dashboard / Applications / Insights / Ask agent / Profile, uppercase 11px), theme toggle, user dropdown OR Login + Get Started, mobile hamburger.
- Toast messages (`messages` framework): per-message `x-data="{ show: true }"`, success auto-dismisses 2s, error/warning persist; emerald/red/amber/brand colour-coded.
- Loading overlay (Tier 3) — fixed full-screen backdrop with three modes: idle (single-line spinner), substep (title + steps with status dots), failure (error + retry/dismiss). `LoadingOps` registry maps op keys (`'cv-upload', 'job-scrape', 'resume-gen', 'gap-analysis', 'outreach'`) to `{title, steps[{label, ms}], tail, timeoutMs}`. Steps auto-advance on elapsed time (no real progress signal). Backward-compat: legacy `showLoading('plain string')` still works.
- Footer: logo + tagline + links (auth-conditional).
- Help button (bottom-right, fixed, authenticated only): triggers Shepherd tour for current page or alerts "no tour for this page".
- Shepherd tour registry (lines 601–723): tours for `dashboard`, `resume-edit`, `gap-analysis`. Auto-trigger on first visit gated by `should_run_tour` flag from view. Help button bypasses gate via `{force: true}`.
- Theme toggle script: localStorage `theme` (or system pref) → `<html>.classList`, SVG icons swap.

### 12.3 Component library (`templates/components/`)

| Component | Params | Purpose |
|---|---|---|
| `section_label.html` | `text, tone?` | Small-caps eyebrow above section heading |
| `badge.html` | `label, tone?, small_caps?, dot?, size?` | Status pill |
| `button.html` | `label, variant?, size?, href?, type?, arrow?, block?, disabled?, form_id?, extra?` | Reusable button or anchor (variant: primary/secondary/ghost/danger; arrow adds animated → on hover) |
| `card.html` | `body, eyebrow?, tone?, padded?, extra?` | Slab card with top accent rule |
| `score.html` | `value, label, suffix=%, size?, tone?` | Big Fraunces italic score + label; auto-tones by value (≥80→brand, ≥50→accent, <50→danger) |
| `profile_strength_ring.html` | `profile_strength` | SVG circular progress ring linking to /insights/ |
| `profile_strength_breakdown.html` | components dict | Detailed score breakdown |
| `github_signals.html`, `kaggle_signals.html`, `linkedin_signals.html`, `scholar_signals.html` | profile | Per-platform signal card with edit/refresh/disconnect |
| `input.html` | `name, label?, type?, value?, error?, required?, ...` | Form input wrapper |

### 12.4 Key pages

| Page | Template | Notable |
|---|---|---|
| Landing | `core/home.html` | Hero (Fraunces 5xl), proof card (mock match=91%), calibration strip |
| Dashboard | `profiles/dashboard.html` (664 lines) | Editorial header (career stage label + "Welcome back, X"), onboarding banner (dismissible AJAX), profile sidebar (sticky), kanban board (Saved / Applied / Interviewing — Alpine drag-drop), tools sidebar, **Recommended jobs panel** with scan-now button + progress modal + scan-failure banner + per-rec save/dismiss/view buttons |
| Upload CV | `profiles/upload_cv.html` (147 lines) | Section label, dropzone, `showLoading({op: 'cv-upload'})` on submit |
| Manual form | `profiles/manual_form.html` | Repeating sections for skills/experience/etc |
| Chatbot | `profiles/chatbot.html` (543 lines) | Split layout (chat left, summary right), bot/user message bubbles, primer panel before first reply, typing indicator, skip/retry buttons per message, real-time response (no SSE; full POST per turn) |
| Connect accounts | `profiles/connect_accounts.html` | Signal cards for GitHub/LinkedIn/Scholar/Kaggle |
| Job preferences | `profiles/job_preferences.html` | Form with Auto-fill button (calls `/preferences/jobs/suggest/`); after suggestion, chip row of `keyword_candidates` rendered with "why" subtitle |
| Job sources setup | `profiles/job_sources_setup.html` | Read-only table of sources (Connected/Not connected based on `has_saved_state(slug)`) + copy-paste login command |
| Job input | `jobs/input.html` | Tabs: URL / Manual; source hint dropdown |
| Job detail | `jobs/detail.html` | Status update form, kanban context |
| Recommended detail | `jobs/recommended_detail.html` | Full description + Save/Dismiss/Open Source buttons |
| Gap analysis | `analysis/gap_analysis.html` (400+ lines) | Two states: computing (substep panel) or results (drag-drop reclassification, large score, three columns matched/soft/missing) |
| Learning path | `analysis/learning_path.html` | Top 5 missing skills aggregated across user's gaps; "Mark complete" toggles |
| Salary negotiator | `analysis/salary_negotiator.html` | Form for current/target offer; LLM script output |
| Generate resume | `resumes/generate.html` | Trigger view |
| Edit resume | `resumes/edit.html` (1396 lines) | Two-column: left sidebar (numbered nav + ATS score), main editor (drag-to-reorder section chips, template picker with thumbnails, repeating section editors with per-section "↻ Regenerate"), right live PDF preview (debounced fetch) |
| Resume list | `resumes/list.html` | Grid of saved resumes |
| Cover letter | `resumes/generate_cover_letter.html`, `cover_letter_preview.html` | Generate + preview |
| Outreach | `profiles/outreach.html`, `outreach_campaign.html`, `outreach_pair.html` | Discovery list, drafts list, campaign progress, extension pairing UI |
| Agent chat | `core/agent_chat.html` | Standalone chat with optional job context |
| Insights | `core/insights.html` | Profile strength breakdown, top skills, recent gaps, recent resumes |
| Applications | `core/applications.html` | Full kanban board (alternative entry to dashboard's mini-kanban) |
| Welcome | `core/welcome.html` | First-run picker (upload / form / skip) |

### 12.5 Alpine.js patterns

The whole codebase uses Alpine for state — no React/Vue. Common shapes:

- **Modal/overlay**: `x-data="{ open: false }"`, `x-show="open"`, transitions.
- **Dropdown**: `@click.away="open = false"`, `@click="open = !open"`.
- **Form state**: `x-data="{ loading: false, errors: {} }"`, `@submit.prevent="submit()"`.
- **Loading overlay** (defined in base.html): `showLoading({op: 'cv-upload'})`, `succeedLoading()`, `failLoading(reason)`.
- **Polling**: `setTimeout(() => this.poll(), 2000)` in scrape progress modal.
- **Drag-drop**: HTML5 DnD events (`dragstart`, `dragover`, `drop`) for kanban + gap-analysis chips + resume section reordering.
- **Live preview**: `@input="updateLivePreview()"` debounced fetch returning HTML, replacing iframe `srcdoc`.
- **Chip pickers** (added May 2026): `keywordCandidates()` Alpine component listens for `prefs-suggested` event, renders `<template x-for>` chips.

---

## 13. End-to-end flows

### 13.1 Onboarding — first-time user

```
GET /                          → home_view → render core/home.html
[Click "Get started"]          → register flow (accounts/views)
GET /welcome/                  → welcome_view sets session.in_onboarding=True
[Pick "Upload CV"]             → /profiles/setup/upload/
GET /profiles/setup/upload/    → upload_master_profile (GET)
POST /profiles/setup/upload/   → upload_master_profile (POST):
  parse_cv(file) → llm_validator.validate_and_map_cv_data() → profile.data_content saved
  redirect /profiles/setup/review/
GET /profiles/setup/review/    → review_master_profile → render manual_form.html with parsed data
POST /profiles/setup/review/   → review_master_profile (POST):
  if onboarding: redirect /profiles/setup/connect/
  else: redirect dashboard
GET /profiles/setup/connect/   → connect_accounts_view → render connect_accounts.html
POST /profiles/setup/connect/  → connect_accounts_view:
  fetch_*_snapshot for each connected handle (GitHub, LinkedIn, Scholar, Kaggle)
  data_content[<service>_signals] = result
  if any signal data: project_dedupe.auto_apply_enriched_projects(profile)  # silent merge
  if onboarding: redirect /profiles/setup/review/
  else: redirect job input or dashboard
```

`session.in_onboarding` controls whether the "Skip" button shows (see `core.context_processors.onboarding`).

### 13.2 Adding a job + gap analysis

```
GET /jobs/input/                       → render jobs/input.html
POST /jobs/input/?input_method=url     → job_input_view:
  scrape_job(url) → scrapers/dispatcher → linkedin/indeed/greenhouse/lever/generic
  extract_skills(description) → LLM + JD-anchoring + denylist
  Job.objects.create(...)
  redirect /jobs/review/<id>/
GET /jobs/review/<id>/                 → review_extracted_job → render review_job.html
POST /jobs/review/<id>/                → if description changed: bust embedding + re-extract skills; save
  redirect /analysis/gap/<id>/
GET /analysis/gap/<id>/                → gap_analysis_view:
  if cached GapAnalysis: render results
  else: render computing template
[Front-end JS POSTs to /analysis/api/compute/<id>/]
POST /analysis/api/compute/<id>/       → compute_gap_api → analysis.tasks.compute_gap_analysis_task:
  compute_gap_analysis(profile, job) [LLM phase + reconciliation]
  GapAnalysis.objects.update_or_create(...)
  return {success: True}
[Front-end re-fetches /analysis/gap/<id>/]
[User drags chip from missing → matched]
PATCH /analysis/api/update-skills/<id>/ → update_gap_skills:
  update lists, recompute similarity_score = (matched + 0.5*soft) / total
  save
```

### 13.3 Resume generation + tailoring

```
[From gap analysis page, click "Generate tailored résumé"]
GET /resumes/generate/<job_id>/                → generate_resume_view (GET) → render generate.html
POST /resumes/generate/<job_id>/               → generate_resume_view (POST) → render generating template
[Front-end POSTs to /resumes/api/trigger-resume/<job_id>/]
POST /resumes/api/trigger-resume/<job_id>/     → trigger_resume_generation_api:
  generate_resume_task(job_id, user_id):
    profile, job, gap = ...
    content = resume_generator.generate_resume_content(profile, job, gap)
    ats = scoring.compute_ats_breakdown(content, job)
    GeneratedResume.objects.create(gap_analysis=gap, content=content, ats_score=ats['score'])
  return {success: True, resume_id: ...}
GET /resumes/edit/<resume_id>/                 → resume_edit_view (GET):
  if profile.updated_at > resume.created_at: regenerate (auto-sync)
  else: _ensure_profile_data_preserved(resume, profile)
  description fields: List[str] → '\n'-joined for textareas
  render edit.html
[User edits a section, clicks Save]
POST /resumes/edit/<resume_id>/                → resume_edit_view (POST):
  for each form field, _description_text_to_list (textarea) → List[str]
  save changes
  redirect to list (or stay)
[User clicks Export PDF]
GET /resumes/export/<resume_id>/               → export_pdf_view → pdf_exporter.generate_pdf:
  render templates/resumes/pdf_template_<chosen>.html with resume context
  xhtml2pdf converts HTML to PDF
  return FileResponse with attachment filename
```

### 13.4 Outreach campaign

```
[From job detail, "Run an outreach campaign"]
GET /profiles/outreach/<job_id>/campaign/      → outreach_campaign_view (GET):
  find_hiring_team(job.url) [public LinkedIn job page; no auth]
  find_peers_via_google(company, role_keywords) [Google SERP, soft-fail]
  for each target: generate_outreach_for_target(target) [LLM]
  render outreach_campaign.html with discovered + drafts
[User clicks "Open LinkedIn" — extension scrapes targets]
[Extension POSTs to /profiles/api/outreach/discovery/push/ with {linkedin_job_id, targets}]
[Web UI polls /profiles/api/outreach/discovery/<job_id>/ every 3 seconds]
[User reviews drafts, edits if needed, clicks "Start campaign"]
POST /profiles/api/outreach/campaigns/         → create_campaign:
  campaign = OutreachCampaign.objects.create(user, job, status='running', daily_invite_cap=10)
  for each selected target:
    OutreachAction.objects.create(campaign, target_handle, target_name, kind='linkedin_connect',
                                   connect_message=draft.body_linkedin, status='queued')

[Extension polls server every 90s ± 20s jitter]
GET /profiles/api/outreach/next                → outreach_next:
  if invites_sent_today(user) >= cap: 204 (queue empty)
  else: claim_next_action(user) → atomic SELECT FOR UPDATE on oldest queued, mark in_flight, attempts++
  return action JSON {id, target_handle, target_name, profile_url, payload}
[Extension opens LinkedIn tab, runs content_linkedin.js click flow]
POST /profiles/api/outreach/result/<action_id>/ → outreach_result:
  record_action_result(action, status='sent'|'failed'|'skipped', reason='weekly_cap'|...)
  write OutreachActionEvent
  _maybe_finish_campaign(campaign) [if no queued/in_flight remain → 'done' or 'failed']
```

### 13.5 Job-board discovery (the new May 2026 flow)

```
GET /profiles/preferences/jobs/                → job_preferences_view (GET):
  prefs = JobPreferences.objects.get_or_create(user)
  if first GET: seed_defaults_from_profile(prefs, profile)
  render job_preferences.html
[User clicks "Auto-fill from my profile"]
POST /profiles/preferences/jobs/suggest/       → suggest_job_preferences_view:
  suggestion = preference_suggester.suggest_job_preferences(profile)
  return JSON {keyword, keyword_candidates, locations, experience_levels, workplace_types, rationale}
[Front-end: Alpine pre-fills inputs + renders chip row of candidates]
[User picks a chip → fills keyword input]
POST /profiles/preferences/jobs/               → job_preferences_view (POST) → save → redirect dashboard

GET /profiles/dashboard/                       → dashboard view:
  has_job_preferences = bool(prefs and prefs.keyword and prefs.locations and prefs.sources)
  active_scrape_job_id = next pending/running ScrapeJob.id
  scan_failure_banner = (computed from prefs.last_scan_failed_at + scan_failure_count)
  if has_prefs and not active_scrape and prefs.last_scan_failed_at and elapsed >= backoff:
    auto-launch retry: ScrapeJob.objects.create + runner.start_in_thread

[User clicks "Scan now"]
POST /jobs/recommend/scan/                     → scan_recommended_jobs:
  if no prefs: 400 with {error, redirect}
  if existing pending/running: 202 with {scrape_job_id, already_running: true}
  else: ScrapeJob.objects.create(user, params_json=prefs.to_params(), status='pending')
        runner.start_in_thread(scrape_job.id)
        return 202 with {scrape_job_id, status='pending'}

[Front-end opens progress modal, polls every 2s]
GET /jobs/recommend/scrape/<id>/status/        → scrape_status:
  return {id, status, progress_pct, completed_steps, total_steps, current_step, message, is_terminal}

[runner thread, in parallel:]
runner.run(scrape_job_id):
  for source in sources:
    for location in locations:
      asyncio.run(scrape_<source>(keyword, location, ..., reporter=...))
        → JobRecord[]
      for rec in records:
        _save_listing(scrape_job, rec) → JobListing.upsert (sha1 unique_hash dedup)
  score_listings_for_user(user_id, scrape_job_id):
    listings = JobListing.objects.filter(scrape_job=...)
    survivors = _prefilter(profile, listings)  [rapidfuzz, top 20, score≥40]
    for li in survivors:
      candidate = _CandidateJob(title, company, description, extract_skills(...))
      result = compute_gap_analysis(profile, candidate)
    sort by score desc
    for top K:
      _upsert_recommendation(user_id, listing, score)  [preserves saved/dismissed status]
  ScrapeJob.status = 'done', message = "Saved N listings · M recommendations"
  prefs.last_scan_at = now(); prefs.last_scan_failed_at = None on success
  
[is_terminal=true → front-end reloads page → dashboard shows populated panel]

[User clicks "Save" on a recommendation]
POST /jobs/recommend/<rec_id>/save/            → recommended_save:
  Job.objects.create or get_or_create from rec
  best-effort extract_skills(rec.description)
  rec.status = 'saved'
  return {saved_job_id}
  → row appears in Saved kanban column on next reload

[User clicks "Dismiss"]
POST /jobs/recommend/<rec_id>/dismiss/         → recommended_dismiss:
  rec.status = 'dismissed'
  → next scan does NOT re-suggest this URL
```

### 13.6 Failure-mode backoff (auto-retry on dashboard load)

When a scrape fails, `JobPreferences.last_scan_failed_at = now()` and `scan_failure_count += 1`. Subsequent dashboard loads:

| `scan_failure_count` | Backoff window |
|---|---|
| 0 or 1 | 30 minutes |
| 2 | 2 hours |
| 3 | 12 hours |
| 4+ | manual (no auto-retry) |

If elapsed ≥ window, dashboard view auto-launches a retry; the scan-failure banner says "We'll retry automatically when you reload after HH:MM UTC". After 4 consecutive failures, banner says "We've stopped retrying automatically — try again manually, or run `python manage.py login_<source>` to refresh credentials".

---

## 14. Browser extension (`extension-outreach/`)

Chrome MV3 extension that automates LinkedIn outreach actions and discovery. Not Web Store packaged (Store rejects LinkedIn automation); installed via `chrome://extensions → Load unpacked`.

### 14.1 Manifest

```json
{
  "manifest_version": 3,
  "name": "SmartCV Outreach",
  "version": "0.3.0",
  "permissions": ["storage", "alarms", "scripting", "tabs"],
  "host_permissions": ["https://www.linkedin.com/*", "http://127.0.0.1/*", "http://localhost/*"],
  "background": {"service_worker": "background.js", "type": "module"},
  "action": {"default_popup": "popup.html"},
  "options_page": "options.html",
  "content_scripts": [
    {"matches": ["https://www.linkedin.com/jobs/view/*"], "js": ["content_discover.js"], "run_at": "document_idle"}
  ]
}
```

### 14.2 `background.js` — service worker

Storage keys (`chrome.storage.local`):

- `smartcv_host` — base URL ("http://localhost:8000")
- `smartcv_token` — UUID outreach_token
- `smartcv_hard_paused_until` — ms epoch (LinkedIn weekly cap pause, 24h)
- `smartcv_rate_limit_until` — ms epoch (server 429 backoff)
- `smartcv_status` — `{state, detail, at}`
- `smartcv_history` — last 10 action outcomes

Status states: `STATUS_OK | STATUS_NOT_PAIRED | STATUS_AUTH_FAILED | STATUS_RATE_LIMITED | STATUS_SERVER_ERROR | STATUS_OFFLINE | STATUS_PAUSED_CAP`.

Polling: `chrome.alarms` schedules `pollOnce()` every **90 s ± 20 s jitter** (default). On error, schedules with `err.backoffMinutes` if set.

`pollOnce()`:

1. GET `/profiles/api/outreach/next` with `Authorization: Token <token>`.
2. Status handling:
   - 200 → `dispatchAction(action)`
   - 204 → reschedule default
   - 401/403 → STATUS_AUTH_FAILED, back off 30 min
   - 429 → STATUS_RATE_LIMITED, honour `Retry-After` (default 30 min, cap 60 min)
   - 5xx → STATUS_SERVER_ERROR, back off 5 min
   - Network error → STATUS_OFFLINE, back off 5 min

`dispatchAction(action)`:

1. Find or create LinkedIn tab on `action.profile_url`.
2. Wait for tab to fully load (timeout 15 s).
3. Inject `content_linkedin.js`.
4. `window.smartcvOutreach.run(action)` returns `{status, error?, detail?, trace?}`.
5. If `error == 'weekly_cap'`: set `smartcv_hard_paused_until = now + 24h`.
6. POST `/profiles/api/outreach/result/<action_id>/` with the outcome.
7. Append to history.

Discovery bridge: `content_discover.js` can't reach `127.0.0.1` directly (Private Network Access blocks it). Instead it sends `{type: 'pushDiscovery', linkedinJobId, targets}` via `chrome.runtime.sendMessage`, and the background worker relays to `/profiles/api/outreach/discovery/push/`.

### 14.3 `content_linkedin.js` — click flow driver

Drives the "Connect → Add note → Send" flow with humanised pacing.

`sleep(ms)`, `jitter(lo, hi)`, `waitForAnySelector(candidates, timeoutMs)`, `waitForButton(label, predicate, timeoutMs)` — all use MutationObserver.

State checks:
- `isProfileMissing()` — regex `not available|Page not found|doesn't exist`.
- `isWeeklyCapModal()` — regex `weekly invitation limit|too many invitations`.
- `waitForProfileReady()` — wait for h1 or action button.
- `isAlreadyConnected()` — button labels `Message`, `Pending`, `Withdraw`.

Click flow:

```
1. jitter(1200, 2400)                       // initial delay
2. waitForProfileReady(8000)
3. Check isWeeklyCapModal() → fail 'weekly_cap'
4. Check isAlreadyConnected() → skip 'already_connected'
5. findConnectButton():
   a) aria-label /^Connect\b|^Invite .* to connect/i
   b) "More actions" overflow → menu → "Connect"
   c) Last-ditch: button with text "Connect" in main
6. connect.click(); jitter(900, 1800)
7. Recheck isWeeklyCapModal()
8. waitForButton('add_note', /add a note|add note/i, 6000)
9. addNote.click(); jitter(500, 1100)
10. waitForAnySelector([
      textarea[name="message"],
      textarea#custom-message,
      div[role="dialog"] textarea
    ], 6000)
11. focus textarea; type in 8-char chunks with jitter(40, 120) per chunk
12. waitForButton('send', inside dialog, 6000)
13. send.click(); jitter(1500, 2500)
14. Recheck isWeeklyCapModal()
15. return { status: 'sent', trace }
```

Outcome statuses: `sent | skipped[already_connected] | failed[weekly_cap | not_found | profile_not_ready | selector_drift:connect | selector_drift:add_note | selector_drift:note_field | selector_drift:send | timeout]`.

### 14.4 `content_discover.js` — job-page scraper

Runs on every `linkedin.com/jobs/view/*`. Selectors:

- Hiring team: `.hirer-card__hirer-information a[href*="/in/"]`, `[data-test-modal-id="hirer-modal"] a[href*="/in/"]`.
- People you know: section heading regex `/reach out|people you/i`, `/in/` links inside.

Extracts `{handle, name, role, source: 'hiring_team'|'people_you_know'}`. Polls every 500 ms up to 6 s for hydration. Sends to background worker via `chrome.runtime.sendMessage({type: 'pushDiscovery', linkedinJobId, targets})`.

### 14.5 `popup.html` / `popup.js`

Status indicators (green/red/amber dot), banners (per state), recent activity table (last 10 actions with status + target + relative time). "Poll now" button creates an alarm with `delayInMinutes: 0.05`.

### 14.6 `options.html` / `options.js`

Pairing UI. Stores `smartcv_host` and `smartcv_token` in `chrome.storage.local`. Token retrieved from `/profiles/extension/pair/`.

---

## 15. Benchmarks (`benchmarks/`)

Reproducible eval suite. 5 phases (B + D1–D5) plus E orchestrator. Results land in `benchmarks/results/<YYYY-MM-DD>/<phase>.json` and the orchestrator writes `run_all.md` + syncs to `docs/benchmarks.md` between markers.

### 15.1 Module map

| Phase | Module | Feature | Metric |
|---|---|---|---|
| B | `latency_runner.py` | Endpoint latency | p50/p95/p99 ms per route (cold vs warm split) |
| D1 | `parser_eval.py` | CV parser | PI accuracy, section presence, skills F1/Jaccard |
| D2 | `skill_extractor_eval.py` | Skill extraction | F1, precision, recall, hallucination rate |
| D3 | `gap_eval.py` | Gap analyzer | Cohen's d (strong vs weak), 100% coverage |
| D4 | `ats_eval.py` | ATS scoring | σ=0 determinism, matched-vs-mismatched Cohen's d, stuffing |
| D5 | `tailoring_eval.py` + `llm_judge.py` | Resume tailoring | LLM-judged factuality / relevance / ats_fit / human_voice (1–10) |

### 15.2 `_io.py` — bootstrap + shared stats

- `write_section(name, payload) -> Path` — write `benchmarks/results/<YYYY-MM-DD>/<name>.json`.
- `summary(values) -> {n, mean, std, min, p50, p95, max}`.
- `percentile(values, p)` — linear interpolation, matches `core/metrics.py`.
- `cohens_d(a, b) -> float | None`.
- `precision_recall_f1(predicted, labeled) -> {precision, recall, f1, tp, fp, fn}`.

### 15.3 Fixtures (`benchmarks/fixtures/`)

`manifest.json` (v2, 2026-05-06) lists 25 CVs (real + synthetic) and 30 JDs (5 hand-curated + 25 auto-paired diagonally to a CV):

CVs cover roles: backend (Rust), frontend (junior React, mid React, senior React/Vue, jQuery legacy, diploma React, entry no-role), devops junior, mobile (Flutter intern), 2nd senior React/Vue variant.

JDs: senior frontend React, backend Python/Node, devops AWS/K8s, Flutter mobile, junior web dev.

Each pair has hand-labeled `expected_match_strength ∈ {strong, partial, weak}`.

Per-CV `<cv_id>.json` label file: `personal_info`, `section_presence`, `skills_canonical`, `experience_count`, `education_count`.

### 15.4 What each phase actually measures

#### parser_eval.py (D1)

For each CV (×repeats):
- `parse_cv(path)` → `{personal_info, section_presence, skills, experiences, education, ...}`.
- Per-field PI accuracy (case-insensitive exact; null labels skipped).
- Section presence binary check.
- Skills overlap: lowercased exact + difflib SequenceMatcher ratio≥0.85 fallback for synonyms.

Output per CV: `{personal_info_accuracy, skills_jaccard, skills_f1, latency_ms, ...}`. Aggregates: mean/std/p95 across all CVs.

#### skill_extractor_eval.py (D2)

For each JD (×repeats=3):
- `extract_skills(jd.description)` → list.
- precision = `|extracted ∩ labeled| / |extracted|`, similar for recall, F1.
- Hallucination rate = `|extracted \ labeled| / |extracted|`.

Headline metric (2026-05-07, n=30 JDs, repeats=1): F1 ≈ 0.853 (P=0.887, R=0.828, hallucination 0.113).

#### gap_eval.py (D3)

For each (CV, JD) pair (10×5=50 by default):
- Parse CV (cached), wrap in duck-typed stubs (`_profile_from_parsed`, `_job_stub`).
- `compute_gap_analysis(profile, job)` (×repeats).
- Bucket by `expected_match_strength`. Cohen's d between strong and weak.
- Coverage = % of JD skills landing in matched/missing/partial ≈ 1.0 (target: 100%).

Headline (current): Cohen's d ≈ 1.658 strong vs weak; coverage 99.7%.

#### ats_eval.py (D4)

Synthetic, in-process (no fixtures):
- 3 matched resumes (backend, frontend, data) score against their own JD: σ over 10 runs = 0 (determinism).
- 6 mismatched (cross-paired) score against wrong JD: matched mean > mismatched mean; Cohen's d ≈ 6.27.
- Stuffing test: resume repeats "Python" 6× (above STUFFING_THRESHOLD=4) → penalty = `len(stuffed) * STUFFING_PENALTY_PER_SKILL = -5`.

#### llm_judge.py (D5 helper)

Four axes scored 1–10 by Groq llama-4-scout (`task='judge'`, `temperature=0.0`):

- **factuality** — does every concrete claim appear in source CV? (fabricated employer = auto-fail ≤3)
- **relevance** — bullets address JD requirements (generic = ≤5)
- **ats_fit** — uses job keywords without stuffing, varies action verbs (missing 50%+ keywords = ≤5)
- **human_voice** — penalises AI-tell phrases per local `_BANNED_VOICE_TOKENS` (mirrors `prompt_guards.HUMAN_VOICE_RULE`)

Schema `JudgeVerdict(factuality: AxisScore, relevance, ats_fit, human_voice, overall_summary)` where `AxisScore = {score: int 1-10, rationale: str max 800}`.

Programmatic checks alongside:

- `factuality_check(generated_resume, source_text, confirmed_projects)` → `{n_entities, n_grounded, ratio, ungrounded[]}`. Extracts company / school / project names from generated, checks substring presence in source.
- `banned_phrase_hits(generated_resume) -> list[str]` — flatten to lowercase, check against `_BANNED_VOICE_TOKENS`.

#### tailoring_eval.py (D5)

For each (CV, JD) pair in selected `--buckets`:
1. Parse CV.
2. `compute_gap_analysis(...)`.
3. `generate_resume_content(...)`.
4. `factuality_check + banned_phrase_hits`.
5. `judge(...)`.

Headline (2026-05-06 snapshot, n=34 strong pairs): factuality ≈ 4.97, relevance ≈ 5.06, ats_fit ≈ 5.24, human_voice ≈ 3.24. Entity grounding ≈ 0.887. (Not re-run on 2026-05-07.)

#### latency_runner.py (B)

Hits 5 fixture-free routes (`/`, `/healthz/`, `/healthz/deep/`, `/accounts/login/`, `/accounts/register/`) with `django.test.Client` (no network hop). 100 reqs/route. Splits cold (first 5) vs warm (rest).

Headline (current): warm p95 ≈ 12.58 ms. Cold first-request can be 22 ms+.

### 15.5 `run_all.py` — orchestrator

Plan: `[ats_eval, latency_runner, parser_eval, skill_extractor_eval, gap_eval]` plus optional `tailoring_eval` if `--with-tailoring`.

For each phase: import its module, run its `main()` or equivalent, capture exceptions. Extract headlines via `_headlines()`. Render markdown via `_format_md()`. Write `benchmarks/results/<date>/run_all.json` and `run_all.md`. Sync to `docs/benchmarks.md` between `<!-- benchmarks:autogen:start -->` and `<!-- benchmarks:autogen:end -->`.

Flags: `--skip <names>`, `--gap-repeats N`, `--sx-repeats N`, `--parser-repeats N`, `--latency-requests N`, `--with-tailoring`, `--tailoring-buckets strong partial weak`.

`benchmarks/CHANGELOG.md` is the narrative log of each delta (Phase D1–D5 + E). Latest entry 2026-05-07.

---

## 16. Background work, threading, async

`CLAUDE.md` is canonical here. Quote:

> All LLM calls are synchronous (django-q was removed). Typical latency is 2-3 seconds.
>
> The one exception is the job-discovery scraper at `jobs/services/job_sources/runner.py`, which spawns a daemon thread per scrape (Playwright requires its own asyncio loop on Windows). It owns its own DB connection via `close_old_connections` and is the only place `DJANGO_ALLOW_ASYNC_UNSAFE` is set. Don't generalise this pattern — for any other long-running work, keep it synchronous in the request thread or fan out via a management command.

This is deliberate. Adding a general queue (Celery, django-q, RQ) would:
1. Add a redis/rabbitmq dependency.
2. Force everything to be serialisable (currently profile/job objects are passed as-is to services).
3. Distort the latency story (the dashboard's 3-LLM-call profile is a known performance ceiling, but it's bounded; an async queue would hide it).
4. Introduce eventual-consistency bugs the simple model doesn't have.

The synchronous model holds up because:
- Groq LLM calls are 700 ms typical (timeouts at 20 s).
- PgBouncer + persistent connections keep DB latency at <50 ms after warmup.
- The dashboard's ~5 s profile is acceptable; it's logged as VERY_SLOW so we can see it but it's not a bug.
- The few genuinely long operations (LinkedIn profile scrape, job-board scan) are *user-initiated* and the UX is built around it (progress modal, status polling).

If a future task genuinely needs async, the cleanest path is a management command run by cron/Task Scheduler — same pattern as `discover_jobs --all-users`.

---

## 17. Database, PgBouncer, performance

The whole stack is tuned for Supabase PgBouncer (transaction pooling, port 6543) accessed from a non-co-located client (typical: dev machine in another region from the Supabase project's eu-west-1).

Cold TCP+TLS handshake = 2–11 s per fresh connection. Without `conn_max_age=60`, every request paid this. With it, the first request after 60 s pays it; the next 59 s of requests reuse the warmed connection.

Failure modes seen in the wild and fixed:
- **Stale-connection error**: PgBouncer / Supabase silently drops idle connections at ~5–10 min. Reusing throws `InterfaceError: connection already closed`. Fixed by `conn_health_checks=True`.
- **runserver hang on boot**: If a previous Python process was force-killed, its PgBouncer client slots aren't immediately reaped; new connections wait. Fixed by `connect_timeout=10` (raises `OperationalError` instead of hanging).
- **Test suite blocked by CREATE DATABASE**: Supabase's PgBouncer cannot CREATE DATABASE. Fixed by SQLite in-memory fallback when `'test' in sys.argv`.
- **Server-side cursor errors**: PgBouncer transaction pooling can't handle them. Fixed by `DISABLE_SERVER_SIDE_CURSORS=True`.

Observability:
- `RequestObservabilityMiddleware` logs SLOW (≥1500 ms) at INFO and VERY_SLOW (≥3000 ms) at WARNING.
- `metrics.snapshot()` exposes per-route p50/p95/p99 at `/healthz/metrics/`.
- Live numbers track the benchmark `latency_runner` numbers because both use the same `_percentile()` formula.

---

## 18. Testing & CI signals

Test count (May 5, 2026): **281 tests**, all passing. Distribution:

- `profiles/tests*.py`: ~150 (CV parsing, profile strength, IMAP autofill, outreach state, prompt guards)
- `jobs/tests.py`: ~30 (skill extractor, URL normalizer, job scoring, preference suggester, JobPreferences seed)
- `resumes/tests.py`: ~80 (list/string conversion, ATS scoring, regen flow)
- `analysis/tests.py`: ~10 (gap analyzer, learning path, salary)
- `core/tests.py`: ~8 (career stage, action planner, agent chat)
- `accounts/tests.py`: ~3 (User model, outreach token rotation)

Test database: in-memory SQLite (no PgBouncer involvement). Suite runs in ~130 s including the few selenium-based tests. Heavy LLM-driven tests mock `get_structured_llm` / `get_llm` so no external calls in CI.

Conventions:
- `SimpleTestCase` for pure-function tests (no DB).
- `TestCase` when DB writes are needed.
- `User.objects.create_user(username='x@y', email='x@y', password='z')` — custom user model requires `username` even though `USERNAME_FIELD='email'`.
- Mock LLM calls; never hit live LinkedIn/Google/Indeed/Glassdoor.

---

## 19. Known issues, anti-patterns avoided, conventions

### Conventions

- **Service modules are pure** (where possible) — no DB writes inside `_compute_*` helpers; the caller persists.
- **JSONB for the win** — `data_content` is the single source of truth for the parsed CV. Adding a new section means adding a key, not a migration.
- **One LLM hub** — `profiles/services/llm_engine.py` is the only file that imports `langchain_groq`. Anything else routes through it.
- **One prompt-guard rule set** — `prompt_guards.HUMAN_VOICE_RULE` is appended at the *end* of every prose-generating prompt.
- **Single source of truth for status enums** — `Job.STATUS_CHOICES`, `RecommendedJob.status` (`new|saved|dismissed`), `OutreachAction.status`, `OutreachCampaign.status`. Each defined once in `models.py`.
- **Soft-fail on external services** — GitHub, Scholar, Kaggle, Google SERP all log warnings and return empty rather than raising. The UI handles "no data" gracefully.

### Anti-patterns avoided

- **No general task queue** — keeps the operational footprint small. The one worker thread is documented as an exception.
- **No vector-only matching** — pure-LLM gap analysis with fuzzy reconciliation. Vectors exist as legacy.
- **No per-section CV tables** — JSONB blob lets the parser keep arbitrary sections.
- **No silent state changes** — outreach state machine writes an event for every transition.
- **No template literals for selectors** — single-page scrapers use selector lists with fallbacks; if LinkedIn/Indeed change their DOM, the scraper logs which selector matched and we can update one constant.
- **No fabricated bullets** — resume_generator's evidence-grounded enrichment rule, the factuality check in benchmarks, and the LLM judge all enforce "every concrete claim must trace to a source".

### Known issues / tradeoffs

- **Dashboard is heavy**: 3+ synchronous calls (`compute_profile_strength`, `get_recommended_actions`, `detect_stage_for_dashboard`) plus optional retry-launch and DB queries for kanban + recommended_jobs. Logged as VERY_SLOW occasionally. No memoisation. Acceptable trade-off — adding caching here is the obvious next perf win.
- **Outreach campaign generation blocks the request** — 10 LLM calls per target sequentially. ~30–60 s typical. Mitigated by the loading overlay UX; not a true bug but a latency ceiling.
- **LinkedIn detail-page rate-limiting**: even with stealth, the guest-endpoint detail fetch in `scrape_linkedin` rate-limits at ~30 jobs. `DETAIL_CONCURRENCY=3` and the `30s` `NAV_TIMEOUT` are the conservative defaults.
- **Glassdoor is fragile** — requires a saved session, and Glassdoor rotates DOM frequently. The selectors have multiple fallbacks; failed scrapes write debug HTML to `debug_dumps/` for triage.
- **`max_tokens=1024` on outreach generator** — sometimes truncates multi-paragraph emails; mitigated by the three-tier recovery (parse failed_generation, retry plain JSON).
- **`embeddings_multi` is mostly unused** — multi-vector design exists in the schema but no flow reads it.
- **No retry logic on agent_chat LLM failure** — returns a generic error string. Acceptable: Groq is fast and rare to fail.

---

## 20. Glossary of model names and the data they own

| Model | App | Owns |
|---|---|---|
| `User` | accounts | Auth, UUID PK, email login, outreach_token |
| `UserProfile` | profiles | Master profile blob (`data_content`), enrichment signals, learning path state, onboarding flags |
| `JobProfileSnapshot` | profiles | Per-job profile variant when user customises chatbot answers for a specific job |
| `OutreachCampaign` | profiles | Outreach automation (per job), state machine, daily cap |
| `OutreachAction` | profiles | One target + connect/follow-up message + status |
| `OutreachActionEvent` | profiles | Append-only audit log of action transitions |
| `DiscoveredTarget` | profiles | Lightweight pre-queue from extension scrapes |
| `JobPreferences` | profiles | Per-user job-discovery search query + scan failure tracking |
| `Job` | jobs | User's saved job postings (manual or scraped) |
| `RecommendedJob` | jobs | Scored, user-facing recommendations from background scrape |
| `ScrapeJob` | jobs | One run of the job-board scraper (pending/running/done/error/cancelled) |
| `JobListing` | jobs | Raw scraped row from one source; transient (converted to RecommendedJob via scoring) |
| `GapAnalysis` | analysis | Per (user, job) skill match: matched/missing/partial + similarity_score |
| `GeneratedResume` | resumes | Tailored resume content (JSONB), ATS score, version |
| `CoverLetter` | resumes | Cover letter text per (user, job) |

---

## 21. Quick-lookup reference: where do I change X?

| Change | Files |
|---|---|
| Add a new LLM-generated section type | `profiles/services/schemas.py` (new Pydantic model), call site, possibly `prompt_guards.append_human_voice` |
| Add a new job-board source | `jobs/services/job_sources/<source>.py` (async scrape function), `jobs/services/job_sources/runner.py` (`SOURCE_FUNCS`), `jobs/management/commands/login_<source>.py` |
| Change scan failure backoff | `profiles/views.py:dashboard` (the `from datetime import timedelta` block) |
| Change resume PDF template | `templates/resumes/pdf_template_<name>.html` |
| Change dashboard hero copy | `core/services/career_stage.py` (the per-stage `CareerStage` dicts) |
| Change recommended-actions logic | `core/services/action_planner.py` |
| Tighten anti-AI-tell rules | `profiles/services/prompt_guards.py:HUMAN_VOICE_RULE` |
| Add a new chat question | `profiles/services/interviewer.py` (`ChatNextQuestion` curriculum) |
| Add a new Pydantic schema | `profiles/services/schemas.py` |
| Add a new agent-chat context block | `core/services/agent_chat.py:_build_job_context_block` (or a new `_*_summary` helper) |
| Add per-task LLM key routing | use `task='<name>'` in `get_llm`/`get_structured_llm`; set `GROQ_API_KEY_<NAME>` in `.env` |

---

## 22. File-level call graph (high-impact edges)

```
profiles/services/llm_engine.py                 ← every LLM caller
   ↑
   ├─ analysis/services/gap_analyzer.py           ← compute_gap_analysis
   │     ↑
   │     ├─ analysis/views.py
   │     └─ jobs/services/job_scoring.py          ← scores RecommendedJob
   ├─ analysis/services/learning_path_generator.py
   ├─ analysis/services/salary_negotiator.py
   ├─ resumes/services/resume_generator.py        ← appends prompt_guards.HUMAN_VOICE_RULE
   ├─ resumes/services/cover_letter_generator.py  ← appends HUMAN_VOICE_RULE
   ├─ profiles/services/outreach_generator.py     ← appends HUMAN_VOICE_RULE
   ├─ profiles/services/preference_suggester.py
   ├─ profiles/services/llm_validator.py
   ├─ profiles/services/interviewer.py            → semantic_validator → llm_engine
   ├─ profiles/services/project_enricher.py
   ├─ profiles/services/project_dedupe.py
   ├─ profiles/services/profile_auditor.py
   ├─ jobs/services/skill_extractor.py            ← called by every job ingest path
   ├─ core/services/agent_chat.py
   └─ benchmarks/llm_judge.py

jobs/services/job_sources/runner.py
   → jobs/services/job_sources/{linkedin,indeed,glassdoor}.py (async Playwright)
   → jobs/services/job_scoring.py
       → analysis/services/gap_analyzer.py
       → jobs/services/skill_extractor.py
       → jobs/services/url_normalizer.py
   → updates jobs.models.{ScrapeJob, JobListing, RecommendedJob}
   → updates profiles.models.JobPreferences (last_scan_*)

profiles/services/linkedin_scraper.py            ← profile (not job) scraper
   → profiles/services/email_verification.py     ← IMAP autofill on email-verification page

profiles/services/outreach_dispatcher.py
   ← profiles/views_outreach_api.py (extension-facing)
   → profiles.models.{OutreachCampaign, OutreachAction, OutreachActionEvent}

core/middleware.py
   → core/metrics.py
   ← core/health.py (snapshot endpoint)

core/services/agent_chat.py
   ← core/views.py (agent_chat_view, agent_chat_api)
   → builds context from profiles, jobs, analysis, resumes models

core/services/action_planner.py + career_stage.py
   ← profiles/views.py:dashboard

core/services/profile_strength.py (in profiles app despite name)
   ← profiles/views.py:dashboard, core/views.py:insights_view
```

---

*End of context document. Total: ~1,000 lines of canonical project knowledge.*
*Generated by reading every Python file, template, and config in the repo across 6 deep-read agents + 3 first-person reads.*
