# SmartCV — Complete Context Document

> **A line-by-line, commit-by-commit, file-by-file documentation of the SmartCV codebase.**
> Generated: 2026-04-26.
> Scope: Every Django app, every service, every model, every template, every commit.
> Branch: `main` (up to date with `origin/main`, 168 commits ahead of empty history).

---

## TABLE OF CONTENTS

**PART 1 — Project Overview**
1. Mission, Vision, and Audience
2. The Problem SmartCV Solves
3. High-Level Architecture
4. Technology Choices and Rationale
5. Project Maturity and Current State

**PART 2 — Repository Structure**
6. Top-Level Directory Tree
7. Root Configuration Files
8. Per-App Layouts
9. Asset and Documentation Directories
10. Generated and Ignored Artifacts

**PART 3 — Technology Stack**
11. Languages and Their Use
12. Backend Stack — Django 5.2, DRF, JWT
13. Database — PostgreSQL via Supabase + pgvector
14. LLM Stack — Groq + LangChain + Pydantic
15. Document Processing — pdfplumber, PyMuPDF, python-docx, xhtml2pdf
16. Frontend Stack — Tailwind CSS v4 + Alpine.js
17. Build Tools and Workflow
18. Testing and Coverage

**PART 4 — Django Project Configuration**
19. `smartcv/settings.py` — Annotated
20. `smartcv/urls.py` — Root URL Routing
21. ASGI / WSGI Entry Points
22. Environment Variables (`.env.example`)
23. Logging Configuration
24. Middleware Stack
25. CSRF, CORS, Static, Media

**PART 5 — Django Apps Deep Dive**
26. `accounts/` — Custom User and Authentication
27. `profiles/` — CV Parsing, Profiles, Outreach
28. `jobs/` — Job Scraping and Skill Extraction
29. `analysis/` — Gap Analyzer, Learning Paths, Salary Tools
30. `resumes/` — Tailored Resume Generation, Cover Letters, PDF Export
31. `core/` — Landing, Health, Observability, Agent Chat

**PART 6 — LLM Integration and AI Architecture**
32. Central LLM Engine (`llm_engine.py`)
33. Pydantic Schema Catalog (`schemas.py`)
34. CV Parsing Prompt Architecture
35. Skill Extraction Prompt + JD Anchoring
36. Two-Phase Gap Analysis Prompt
37. Domain-Aware Resume Generation
38. Anti-Hallucination Strategies
39. Prompt Guards and Human-Voice Filters

**PART 7 — Database Schema and Models**
40. Core Models — User, UserProfile, Job, GapAnalysis, GeneratedResume
41. Outreach Models — Campaign, Action, DiscoveredTarget
42. Snapshot Models — JobProfileSnapshot
43. CoverLetter, RecommendedJob
44. Migrations (every migration, in order)
45. pgvector Usage and Multi-Vector Architecture
46. JSONB `data_content` Pattern

**PART 8 — Services Module Catalog**
47. `profiles/services/` — 17 modules
48. `jobs/services/` — Scraping framework + skill extraction
49. `analysis/services/` — Gap analyzer, learning paths, salary, skill score
50. `resumes/services/` — Generator, scoring, PDF, cover letters
51. `core/services/` — Action planner, agent chat, career stage

**PART 9 — Templates and Frontend**
52. `templates/base.html` and the Layout System
53. Component Library (`templates/components/`)
54. Per-App Templates
55. Alpine.js Patterns
56. PDF Templates (6 styles)

**PART 10 — Static Assets and Styling**
57. Tailwind CSS v4 — CSS-First Configuration
58. Color Palette (Brand, Accent, Semantic)
59. Typography — Inter, Fraunces, IBM Plex Mono
60. Compiled Output (`static/css/output.css`)

**PART 11 — Testing**
61. Test Structure (337 tests)
62. Per-App Test Inventories
63. Coverage (53% overall, 76.9% in core/)
64. Test Database Strategy (in-memory SQLite)

**PART 12 — Benchmarks Suite**
65. Methodology Overview
66. Phase B — Latency
67. Phase D1 — CV Parser Accuracy
68. Phase D2 — Skill Extractor F1
69. Phase D3 — Gap Analyzer Coverage and Separation
70. Phase D4 — ATS Scoring Determinism
71. Phase D5 — LLM-Judged Resume Tailoring
72. Fixtures (10 CVs × 5 JDs = 50 pairs)
73. Latest Results (2026-04-27)

**PART 13 — Chrome Extension (`extension-outreach/`)**
74. Manifest V3
75. Background Service Worker
76. Content Scripts (Discover + LinkedIn)
77. Popup and Options
78. API Integration with Backend

**PART 14 — Documentation**
79. README.md
80. CLAUDE.md
81. `docs/` Folder Contents
82. QA Test Plans

**PART 15 — Build, Setup, and Deployment**
83. Local Development Setup
84. Tailwind Build Workflow
85. Database Migrations
86. Production Considerations
87. Known Limitations

**PART 16 — Complete Git History**
88. Repository Statistics
89. All 168 Commits, Annotated
90. Branches and Tags
91. Top 20 Most-Changed Files
92. Contributor Statistics

**PART 17 — Key Data Flows (End-to-End)**
93. CV Upload → Parse → Validate → Embed → Profile Save
94. Job Input (URL/Text) → Scrape → Skill Extract → Save
95. Gap Analysis → LLM Categorize → Reconcile → Persist
96. Resume Generation → Domain Detect → Tailor → Score → Render
97. Outreach Campaign → Discover → Queue → Extension Drains → Audit

**PART 18 — Security and Performance**
98. Authentication (UUID + Email + JWT)
99. CSRF Protection and Custom Failure Page
100. Database Connection (PgBouncer + SELECT 1 ping)
101. Latency SLOs and Observability
102. Anti-Abuse (Outreach Weekly Cap, Rate Limiting)

**PART 19 — Appendices**
103. Full File Index
104. Glossary of Terms
105. Statistics Summary
106. Notes on Future Work

---

# PART 1 — Project Overview

## 1. Mission, Vision, and Audience

SmartCV is positioned as an **AI-powered career assistant** rather than a resume formatter. The README is explicit about this distinction: it's framed as "AI-powered career assistant for job seekers" that helps users tailor CVs, analyze skill gaps, and generate ATS-optimized resumes — backed by an LLM pipeline that reuses the same services everywhere. The product exists to compress the work of applying for a role from a multi-hour effort (rewrite resume, hand-tailor cover letter, find LinkedIn contacts, draft messages) into a guided pipeline that takes minutes per job.

The repository's positioning has explicitly evolved: commit `97b3427` ("feat(positioning): Reframe SmartCV as career agent, not CV maker") reframed the product on 2026-04-14 from a CV-tailoring tool into a career agent that lives at `/agent/`, ingests external signals (GitHub, Scholar, Kaggle), maintains a profile-strength score, and proactively recommends next actions. Users still produce ATS-optimized resumes, but those resumes are an artifact of an ongoing relationship, not the entire product surface.

The audience is job seekers in technical and adjacent roles — software engineering, data, design, product, marketing, sales, finance — for whom domain-specific resume bullets matter (see `resumes/services/resume_generator.py`'s `_DOMAIN_KEYWORDS` table). The tooling assumes a literate user who can paste job URLs or text, upload a PDF/DOCX CV, and review LLM output before clicking "Generate." It's intentionally not "drag-and-drop, click once, get a resume" — every meaningful AI output is gated by a review step.

## 2. The Problem SmartCV Solves

Career platforms in 2026 generally fall into four buckets, each with a flaw that SmartCV is built to address:

1. **Resume builders** (Canva, Resume.io) — produce visually attractive resumes but don't actually reason about job fit. They rearrange content; they don't pick which content matters.
2. **ATS keyword stuffers** (Jobscan, ResyMatch) — give a numeric score but no honest signal about *evidence* (a keyword in a skills list ≠ a keyword in a shipped project). They reward stuffing, which makes resumes worse.
3. **AI cover letter generators** (Kickresume AI, Teal) — generate generic, voice-less drafts that recruiters can pattern-match within a sentence.
4. **Outreach automation tools** (LinkedIn Sales Navigator, Lemlist) — automate sending but don't actually find good targets, write personalized notes, or respect connection caps.

SmartCV's response to each:

1. **Two-phase gap analysis** (`analysis/services/gap_analyzer.py`) doesn't just count keywords. The LLM categorizes every JD skill against the candidate's full profile — skills, experience highlights, project descriptions, certifications, GitHub languages, Kaggle medals, Scholar publications. Then a programmatic Phase 2 fuzzy-reconciliation pass guarantees that *every* JD skill lands in matched / partial / missing — no silent drops.
2. **Deterministic ATS scoring** (`resumes/services/scoring.py`) penalizes keyword stuffing (>4 occurrences of the same word knocks 5 points off) and rewards in-context use (keywords appearing in experience descriptions get +2 points each, capped at +10). It's open-source, deterministic (σ=0 across 10 runs), and the breakdown is exposed in the UI so the user can see *why* the score moved.
3. **Voice rules** (`profiles/services/prompt_guards.py`) actively ban LLM-isms ("Spearheaded," "Leveraged," "Synergized," "Robust") in the resume and cover-letter prompts — replaced via a `HUMAN_VOICE_RULE` constant. Combined with `SPECIFICITY` and `opener-variation` rules added in commit `25082a0`, the output reads less like template-LLM and more like a real resume.
4. **Outreach Chrome extension** (`extension-outreach/`) discovers targets from inside the user's own LinkedIn tab (no scraping a logged-out LinkedIn from a server), drafts personalized notes server-side, and queues actions. The user reviews each draft before sending. The extension respects a `weekly_cap` and pauses for 24h on a cap hit.

## 3. High-Level Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                       USER (Browser, LinkedIn)                     │
└────────────────────────────────────────────────────────────────────┘
            ▲                                          ▲
            │ HTTPS                                    │ Extension API
            ▼                                          ▼
┌────────────────────────────┐          ┌──────────────────────────┐
│  Django 5.2 Server         │          │  Chrome Extension MV3    │
│  (Whitenoise, debug-tb)    │          │  (background SW + CS)    │
├────────────────────────────┤          ├──────────────────────────┤
│  6 Apps:                   │          │  - background.js (alarms)│
│   • accounts               │          │  - content_discover.js   │
│   • profiles               │◄────────►│  - content_linkedin.js   │
│   • jobs                   │  /api/   │  - popup, options        │
│   • analysis               │          └──────────────────────────┘
│   • resumes                │
│   • core                   │
├────────────────────────────┤
│  Services Layer            │
│   • llm_engine (Groq)      │ ◄─────► Groq API (LPU, Llama-4-Scout)
│   • cv_parser (PDF/DOCX)   │
│   • gap_analyzer (2-phase) │
│   • resume_generator       │
│   • skill_extractor        │
│   • outreach_generator     │
│   • github/scholar/kaggle  │ ◄─────► GitHub, Scholar, Kaggle APIs
│   • profile_strength       │
└────────────────────────────┘
            │
            ▼
┌────────────────────────────┐
│  PostgreSQL (Supabase)     │
│  - PgBouncer (port 6543)   │
│  - pgvector (384-dim)      │
│  - JSONB data_content      │
│  - GIN index on JSONB      │
└────────────────────────────┘
```

The architecture has a few deliberate constraints worth calling out:

- **Synchronous LLM calls.** `django-q` was introduced and then removed (commits `c8d8e03`, `4143d27`). All AI work runs on the request thread. Latency is typically 2–3 seconds per Groq call; the UI compensates with optimistic states ("Computing your gap analysis…") and explicit retry buttons. The original `django-q2` plan (see `docs/implementation_plan.md` Phase 6) calls for re-introducing background workers for embedding pre-computation, but that's not on the current branch.
- **No JS framework on the frontend.** Tailwind v4 + Alpine.js via CDN. This keeps the bundle small and means the dev server works without `npm install`. Django renders HTML; Alpine handles interactivity (drag-and-drop, live form updates, modal state).
- **Centralized LLM access.** Every LLM call goes through `profiles/services/llm_engine.py`. There are exactly two functions: `get_llm()` for plain text and `get_structured_llm(Schema)` for Pydantic-validated output. There's also a legacy shim `get_llm_client()` that mimics the old OpenAI client API for files that haven't been migrated yet.
- **JSONB profile storage.** `UserProfile.data_content` is a single JSONB field that stores the entire parsed CV (skills, experiences, education, projects, certifications, plus dynamic sections like `github_signals`, `scholar_signals`, `kaggle_signals`, `has_seen_welcome`). Property accessors on the model (`profile.skills`, `profile.experiences`) provide ergonomic access. This was a deliberate move (migrations `0005`–`0008`) away from rigid per-section tables — it preserves arbitrary CV structure and makes it cheap to add new sections without a migration.
- **Tests use SQLite.** Settings auto-detect `'test' in sys.argv` and swap the DB to in-memory SQLite. The PgBouncer connection holds connections that block `CREATE DATABASE test_...` from completing, which is why this trick is necessary.

## 4. Technology Choices and Rationale

| Layer | Choice | Why |
|---|---|---|
| Web framework | **Django 5.2** | Mature ORM, admin, auth, CSRF protection, templates, migrations. The team is single-developer; battery-included framework minimizes bikeshedding. |
| API layer | **Django REST Framework + simple-JWT** | DRF for `@api_view` decorators on the small surface that's actually JSON (extension endpoints, gap-analysis recompute, agent chat); JWT for stateless extension auth. |
| Database | **PostgreSQL via Supabase** (PgBouncer transaction pooling, port 6543) | Supabase ships pgvector pre-installed, has generous free tier, includes a connection pooler that's required for serverless deploys. |
| Vector store | **pgvector 0.4.2** | 384-dimensional vectors (sentence-transformers `all-MiniLM-L6-v2`). Multi-vector schema: skills, experience, education vectors per profile. The vectors are increasingly *deprecated* — gap analysis went pure-LLM in commit `b8632a4` ("remove SentenceTransformer, go full LLM for gap analysis"). |
| LLM provider | **Groq** (`meta-llama/llama-4-scout-17b-16e-instruct`) | Sub-3-second responses on the LPU. Rate limits accommodate solo-developer throughput. Configurable via `GROQ_MODEL` env var. |
| LLM orchestration | **LangChain `ChatGroq` + `with_structured_output()`** | Pydantic schemas guarantee well-formed output. No manual JSON parsing in services. |
| Validation | **Pydantic 2.5.2** | Used for both LLM output schemas and CV data structure. Permits extra fields (`model_config = ConfigDict(extra='allow')`) so dynamic CV sections (publications, patents, awards) don't blow up validation. |
| PDF extraction | **pdfplumber 0.10.3** + optional PyMuPDF | pdfplumber is pure-Python (no system dependencies). PyMuPDF is preferred when available (better at letter-spacing and embedded-link extraction) — see `cv_parser.py` lines 15–25. |
| DOCX extraction | **python-docx 1.1.0** | Reads paragraphs, tables, and hyperlink relationships. |
| PDF generation | **xhtml2pdf 0.2.11** | HTML-to-PDF in pure Python. Avoids weasyprint's heavy native dependencies. |
| Static serving | **WhiteNoise 6.6.0** | Production static-file serving with gzip and ETags, no separate nginx required. |
| Frontend | **Tailwind CSS v4** (CSS-first config) + **Alpine.js** via CDN | v4 ships a standalone CLI; no PostCSS chain. Alpine is small, declarative, and works with Django templates without JS bundling. |
| Auth | **Custom UUID `User` model**, email-as-username | UUID primary keys avoid sequential-id leaking. Email auth matches user expectation. |

Some libraries that appear in `requirements.txt` deserve specific attention:

- **`django-debug-toolbar==6.3.0`** — Auto-disabled when `DEBUG=False` and during the test runner. Adds the `__debug__/` URL prefix only when active. See `smartcv/settings.py` lines 83–98.
- **`django-cors-headers==4.3.1`** — Configured for `localhost:3000` only (used during the brief period a separate frontend was being prototyped).
- **`coverage==7.13.5`** — Used by `coverage run manage.py test` and by `benchmarks/run_all.py`. Configuration in `.coveragerc`.
- **`huggingface_hub>=0.36.0`** — Pinned for embedding model downloads. `sentence-transformers` itself is no longer in requirements; embedding generation goes through `pgvector` and HuggingFace Inference API in legacy paths.

## 5. Project Maturity and Current State

As of 2026-04-27 (current commit `e86ca4e`):

| Indicator | Value |
|---|---|
| Total commits on `main` | 193 |
| Active development window | 2026-03-10 → 2026-04-27 (~7 weeks) |
| Total lines added (history) | ~52,000 |
| Total lines deleted | ~31,000 |
| Net codebase size | ~21,000 lines (Python + HTML + CSS, excl. compiled output.css) |
| Python LOC (services + views, excluding migrations and tests) | ~6,850 |
| HTML templates | 48 |
| Test files | 9 (containing 398 test cases) |
| Test coverage (overall) | 53% |
| Test coverage (core/) | 76.9% |
| Public dependencies | 21 (Python) + 2 (npm) |
| LLM model | Groq Llama-4-Scout (17B, 16 expert) |
| Latest benchmark date | 2026-04-27 |
| Production status | Public-release prep complete; not yet deployed at a public URL |

Pre-launch hardening commits (commit `9068ae0` onward) added:
- Per-route latency middleware (`core/middleware.py`)
- `/healthz/` (cheap), `/healthz/deep/` (DB ping cached 15s), `/healthz/metrics` (JSON p50/p95/p99)
- Banned LLM-isms in resume and cover-letter prompts
- A custom CSRF failure page (`templates/403_csrf.html`) to replace Django's raw 403
- The benchmark suite under `benchmarks/` with reproducible JSON artifacts
- A `coverage` configuration (`.coveragerc`) that excludes migrations and venv

The repository explicitly does not have:
- GitHub Actions or any other CI
- A production deployment target (no `Procfile`, no `vercel.json`, no `Dockerfile`)
- A linter (`flake8`, `ruff`, `black`) or formatter
- Type hints enforced (some files are typed, most are not)
- A separate frontend build (Tailwind is the only npm dependency)

These omissions are deliberate; the current focus is feature/quality, not deployment.

---

# PART 2 — Repository Structure

## 6. Top-Level Directory Tree

```
G:\New folder\SmartCV\
├── .claude/                          # Claude Code workspace settings (gitignored via .gitignore)
│   ├── settings.json
│   └── settings.local.json
├── .git/                             # Git history (168 commits)
├── .github/                          # (currently empty — no Actions configured)
├── .venv/                            # Project-local Python venv (gitignored)
├── accounts/                         # Custom UUID User + auth flows
├── analysis/                         # Gap analyzer + learning paths + salary
├── benchmarks/                       # Reproducible evaluation suite
│   ├── fixtures/
│   │   ├── jobs/                     # 5 hand-crafted JDs
│   │   └── labels/                   # 10 CV gold-label files
│   ├── results/2026-04-2{5,6,7}/     # JSON + markdown artifacts (latest: 2026-04-27)
│   └── CHANGELOG.md                   # Cross-run delta log
├── core/                             # Landing, observability, agent chat
├── docs/                             # Public-facing documentation
│   ├── images/                       # Screenshots for README
│   └── qa/                           # QA manual test plans
├── extension-outreach/               # Chrome extension MV3
├── jobs/                             # Job scraping + skill extraction
├── media/                            # User-uploaded CVs (gitignored)
├── node_modules/                     # npm deps (gitignored)
├── profiles/                         # CV parsing, profiles, outreach API
├── resumes/                          # Resume gen, cover letters, PDF export
├── smartcv/                          # Django project settings + URLs
├── static/                           # Tailwind input + compiled output
├── staticfiles/                      # collectstatic output (gitignored)
├── templates/                        # Project-level templates
│   ├── components/                   # Reusable HTML primitives
│   ├── accounts/, analysis/, core/, jobs/, profiles/, resumes/
│   ├── 403_csrf.html, 404.html, 500.html
│   └── base.html
├── test cvs/, test cvs2/             # Local fixture dirs (gitignored)
├── .coveragerc                       # Coverage config
├── .env, .env.example                # Environment variables (.env gitignored)
├── .gitignore
├── .mcp.json                         # MCP server config (gitignored)
├── CLAUDE.md                         # Guidance for Claude Code
├── LICENSE                           # MIT
├── README.md
├── create_superuser.py               # Helper script
├── db.sqlite3                        # Local dev DB (gitignored)
├── manage.py                         # Django entry
├── package.json, package-lock.json   # Tailwind CLI only
├── requirements.txt                  # Python deps
├── run_dev.ps1                       # Windows dev runner
└── ux_changelog.md                   # User-facing UX changes log
```

Directory size (excluding `node_modules`, `.venv`, `media`):
- **Source code**: ~85% Python, ~10% Django HTML, ~4% JS (Chrome extension), ~1% CSS source
- **Tests**: 9 test files, 337 test cases
- **Templates**: 48 HTML files
- **Migrations**: ~25 Django migration files

## 7. Root Configuration Files

### `manage.py`
Standard Django entry. No customization beyond default `os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')`.

### `requirements.txt`
21 dependencies. Production:
```
Django>=5.2.7
psycopg2-binary==2.9.9
python-decouple==3.8
dj-database-url==2.1.0
python-dotenv==1.0.0
djangorestframework==3.14.0
djangorestframework-simplejwt==5.3.0
rapidfuzz==3.5.2
pdfplumber==0.10.3
python-docx==1.1.0
numpy==1.26.2
xhtml2pdf==0.2.11
requests==2.31.0
beautifulsoup4==4.12.2
django-cors-headers==4.3.1
whitenoise==6.6.0
pgvector==0.4.2
pydantic==2.5.2
huggingface_hub>=0.36.0
```
Dev-only (auto-disabled appropriately):
```
django-debug-toolbar==6.3.0
coverage==7.13.5
```

### `package.json`
```json
{
  "name": "smartcv",
  "version": "1.0.0",
  "description": "SmartCV frontend build toolchain.",
  "private": true,
  "scripts": {
    "build:css": "tailwindcss -i ./static/src/input.css -o ./static/css/output.css --minify",
    "dev:css": "tailwindcss -i ./static/src/input.css -o ./static/css/output.css --watch"
  },
  "devDependencies": {
    "@tailwindcss/cli": "^4.2.2",
    "tailwindcss": "^4.2.1"
  }
}
```
Two scripts only (`build:css`, `dev:css`). No bundler, no PostCSS chain — Tailwind v4's standalone CLI does the entire job.

### `.env.example`
```
DATABASE_URL=postgresql://postgres.<project>:<password>@<region>.pooler.supabase.com:6543/postgres?sslmode=require
GROQ_API_KEY=gsk_...
# GROQ_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
SECRET_KEY=change-me-in-production
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost
# HF_API_KEY=hf_...
```
Required keys: `DATABASE_URL`, `GROQ_API_KEY`. Optional: `GROQ_MODEL`, `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `HF_API_KEY`.

### `.gitignore`
```
node_modules/
__pycache__/
*.py[cod]
*$py.class
*.sqlite3
db.sqlite3
.env
.venv/
env/
venv/
ENV/
*.log
media/
test_cvs2/
test cvs2/
test cvs/
batch_results.json
.coverage
htmlcov/
.vscode/
.idea/
.claude/
.mcp.json

# Personal / test artifacts
screenshots/
image.png
*.pdf
!resumes/templates/**/*.pdf
```
Excludes the usual Python/Django/IDE artifacts plus user-specific test fixtures (`test cvs/`, `test cvs2/`), `.env`, `media/`. Permits PDFs only inside `resumes/templates/` (legacy template fixtures).

### `.coveragerc`
Excludes migrations, tests, venvs from coverage measurement. Used by `coverage run manage.py test` and `benchmarks/run_all.py`.

### `LICENSE`
MIT, added in commit `7747219` ("chore: add MIT LICENSE and .env.example") on 2026-04-25 as part of public-release prep.

### `run_dev.ps1`
Windows PowerShell script that activates the project-local `.venv` and runs `python manage.py runserver` with the Py3.13 WMI-hang workaround (set `PYTHONDONTWRITEBYTECODE=1` and `PYTHONIOENCODING=utf-8`). Added in commits `9e2c20a` and `cc608b7`.

### `create_superuser.py`
Idempotent helper. Reads `DJANGO_SUPERUSER_*` env vars and creates a superuser if one with that email doesn't exist. Designed for first-deploy scripting.

### `CLAUDE.md`
Reproduced verbatim in PART 14 below. Provides architecture and command guidance for Claude Code working in this repo.

## 8. Per-App Layouts

Every Django app follows the same skeleton:
```
<app>/
├── migrations/
│   ├── 0001_initial.py
│   ├── 0002_*.py, 0003_*.py, ...
│   └── __init__.py
├── services/                # Business logic (LLM, scrapers, scorers, etc.)
│   └── *.py
├── __init__.py
├── admin.py                 # Django admin (often minimal, 3 lines)
├── apps.py                  # AppConfig
├── models.py
├── tests.py                 # Or split: tests_*.py
├── urls.py
└── views.py
```

Detailed inventories per app appear in **PART 5**.

## 9. Asset and Documentation Directories

### `static/`
```
static/
├── css/output.css           # Compiled Tailwind v4 (3722 lines, committed)
└── src/input.css            # Tailwind CSS-first config (140 lines)
```
The compiled `output.css` is **committed** so the dev server works without `npm install`.

### `templates/`
```
templates/
├── components/              # Reusable primitives — badge, button, card, input, etc.
├── accounts/                # Login, register, password reset, settings
├── analysis/                # Gap analysis, learning path, salary negotiator
├── core/                    # Home, dashboard, applications, insights, agent chat, welcome
├── jobs/                    # Job input, detail, review
├── profiles/                # Dashboard, upload, manual form, chatbot, outreach pages
├── resumes/                 # List, generate, edit, preview, PDF templates (6)
├── 403_csrf.html, 404.html, 500.html
└── base.html
```

### `docs/`
```
docs/
├── images/
│   ├── dashboard.png
│   ├── gap-analysis.png
│   ├── outreach-campaign.png
│   └── resume-editor.png
├── qa/
│   ├── manual-test-plan.md
│   └── outreach-automation-test-plan.md
├── benchmarks.md
├── gap_analysis_system.md
└── implementation_plan.md
```

### `media/`
User-uploaded CVs land in `media/cvs/`. Gitignored. ~40 test PDFs/DOCXs accumulated locally during dev.

### `benchmarks/`
```
benchmarks/
├── fixtures/
│   ├── jobs/                # 5 JDs
│   ├── labels/              # 10 CV gold-label files
│   └── manifest.json        # CV × JD pair labels
├── results/
│   ├── 2026-04-25/                # day-zero baseline
│   ├── 2026-04-26/                # post per-task GROQ keys + judge schema fix; D5 captured fallback regime
│   └── 2026-04-27/                # latest — D5 refresh after TPD reset; LLM-available numbers
│       ├── REPORT.md
│       ├── ats_eval.json
│       ├── gap_eval.json
│       ├── latency_runner.json
│       ├── parser_eval.json
│       ├── run_all.json
│       ├── run_all.md
│       ├── skill_extractor_eval.json
│       └── tailoring_eval.json
├── CHANGELOG.md                   # Cross-run delta log
├── __init__.py
├── _io.py                   # Test harness + stats helpers
├── ats_eval.py              # Phase D4
├── gap_eval.py              # Phase D3
├── latency_runner.py        # Phase B
├── llm_judge.py             # 4-axis scorer for D5
├── parser_eval.py           # Phase D1
├── skill_extractor_eval.py  # Phase D2
├── tailoring_eval.py        # Phase D5
└── run_all.py               # Orchestrator
```

## 10. Generated and Ignored Artifacts

These appear locally but are gitignored:

- `db.sqlite3` — Created when running tests (`'test' in sys.argv` triggers SQLite mode).
- `staticfiles/` — Output of `python manage.py collectstatic`.
- `media/` — User uploads.
- `node_modules/` — `npm install` for Tailwind CLI.
- `.venv/` — Project-local Python virtualenv.
- `.env` — Real secrets (the example `.env.example` is committed).
- `.coverage`, `htmlcov/` — `coverage` artifacts.
- `__pycache__/`, `*.pyc` — Python bytecode.
- Personal scratch artifacts in commit `e35f5b1` were removed before public release: `Dashboard — SmartCV.pdf`, `SmartCV.pdf`, `ZeyadAhmedElsayed_CV.pdf`, `Moustafa Ahmed_Resume.pdf`, `poster.pdf`, `poster_layout_sketch.pdf`, `question2_reflection.pdf`, `image.png`, `batch_results.json`, `check.log`.

---

# PART 3 — Technology Stack

## 11. Languages and Their Use

The stack is intentionally narrow:

- **Python 3.12+** — All backend, all services, all tests, all benchmarks. ~85% of the codebase.
- **Django Template Language** — Server-rendered HTML. ~10%.
- **JavaScript (vanilla, ES6+)** — Chrome extension only. Three content scripts (`background.js`, `content_discover.js`, `content_linkedin.js`), one popup script, one options script. Total ~600 LOC. ~4%.
- **CSS (Tailwind v4 source)** — `static/src/input.css`, 140 lines. ~1%.
- **PowerShell** — `run_dev.ps1`, ~30 lines.

There is intentionally **no TypeScript, no React, no Vue, no Webpack, no Vite, no Babel**.

## 12. Backend Stack — Django 5.2, DRF, JWT

### Django 5.2

Django 5.2 was chosen for its mature ORM, admin, auth, and template rendering. Specific Django features SmartCV uses:

- **Custom user model** (`AUTH_USER_MODEL = 'accounts.User'`) — UUID primary keys, email login.
- **Migrations** — ~25 generated migration files. The `profiles` app has 15 alone (with significant schema evolution from per-section tables to JSONB).
- **Class-based password reset views** — Imported wholesale in `accounts/urls.py` with custom templates.
- **`@login_required`, `@require_POST`** — Used throughout views.
- **`JSONField`** — Heavy use for `data_content`, `extracted_skills`, `matched/missing/partial_skills`, `content` (resumes).
- **Admin** — Minimal usage; `admin.py` files are mostly placeholders with `admin.site.register()` calls.
- **`@transaction.atomic`** — `analysis/views.py` wraps gap analysis in a transaction.
- **Context processors** — `core.context_processors.onboarding` injects `in_onboarding` flag globally.
- **CSRF** — Default middleware + custom `CSRF_FAILURE_VIEW = 'core.views.csrf_failure'`.

### Django REST Framework

DRF is used minimally:
- `@api_view(['POST'])` + `@permission_classes([IsAuthenticated])` for `jobs/views.py:save_job_extension_view`.
- The extension API endpoints in `profiles/views_outreach_api.py` use a token-based auth scheme via `request.headers.get('Authorization')` matching `User.outreach_token`.
- Standard JSON `Response` / `Request` are used for the few JSON endpoints.
- Most endpoints return `JsonResponse` directly without DRF.

### simple-JWT

Configured in `REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']`:
```python
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    )
}
```
Currently used only by the extension save-job endpoint.

## 13. Database — PostgreSQL via Supabase + pgvector

### Connection

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

The annotated comment in `settings.py` is critical: PgBouncer in transaction mode kills idle client connections, so server-side cursors must be disabled. `conn_max_age=60` + `conn_health_checks=True` validates the connection at request start with a cheap `SELECT 1` instead of blindly reusing a dead one (which would throw `InterfaceError: connection already closed`). Without `conn_max_age`, the cold TCP+TLS handshake makes every request 2–11 seconds on first hit.

`connect_timeout=10` ensures a saturated pool raises `OperationalError` instead of hanging server boot indefinitely.

### Tests

```python
if 'test' in sys.argv:
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
```

Tests get an in-memory SQLite DB. Supabase's PgBouncer holds connections that block `CREATE DATABASE test_...` from completing.

### pgvector

Used in two models:
- `UserProfile` — `embedding`, `embedding_skills`, `embedding_experience`, `embedding_education` (4 vector fields, 384 dims each).
- `Job` — `embedding` (single 384-dim vector).

Migrations `profiles/0004_setup_vector.py` and `jobs/0002_setup_vector.py` install the extension and add the columns.

The 384-dimensional choice corresponds to `sentence-transformers/all-MiniLM-L6-v2`. As of commit `b8632a4` the embeddings are largely deprecated for gap analysis (which now goes through pure-LLM evaluation), but they remain in the schema for potential future use (semantic job recommendations).

## 14. LLM Stack — Groq + LangChain + Pydantic

### Why Groq

Groq's LPU inference architecture serves Llama-4-Scout (17B parameters, 16 experts, instruction-tuned) at ~2-second response time. For interactive UX (gap analysis, resume tailoring) this is the difference between feeling responsive and feeling broken.

### Why LangChain

LangChain provides the `with_structured_output(pydantic_schema)` glue that converts the LLM's tool-calling response into a validated Pydantic instance. Without it, every service would need to handle JSON parsing, validation, and retry logic. With it, the entire LLM call is one line:

```python
result = structured_llm.invoke(prompt)  # type: PydanticSchema
```

### Why Pydantic

Two reasons:
1. The LLM's structured output is reliable but not perfect. Pydantic's coercion (e.g., `Optional[Union[str, List[str]]]` for descriptions) tolerates the LLM returning either format and validates the rest.
2. The same schemas drive both LLM output and internal data flow — `ResumeSchema` is what the parser produces, what the LLM validates, what the form renders, and what the PDF templates consume.

### Schema catalog

`profiles/services/schemas.py` defines:

**CV / Profile schemas:**
- `Skill(name, proficiency?, years?)`
- `Experience(title, company, start_date?, end_date?, description?, highlights[], industry?, location?, achievements[])`
- `Education(degree, institution, graduation_year?, field?, gpa?, honors[], location?)`
- `Project(name, description?, role?, highlights[], technologies[], url?)`
- `Certification(name, issuer?, date?, duration?, url?)`
- `ItemDetailed(title, organization?, date?, description?, url?)`  (used for awards, volunteer, publications, patents, etc.)
- `ResumeSchema` — the master profile shape, allows extra fields.

**LLM output schemas:**
- `GapAnalysisResult(critical_missing_skills[], soft_skill_gaps[], matched_skills[], similarity_score)`
- `SkillListResult(skills[])`
- `ExtractedExperienceBullet(company_or_project_name, bullet_point)`
- `ChatReplyAnalysis(is_valid, quality_score, clarification_prompt, skills_to_add[], all_technologies_mentioned[], new_experience_bullets[])`
- `ChatNextQuestion(question, topic_skill)`
- `ChatTurnResult(reply_analysis, next_question_generation)`
- `SemanticValidationResult(makes_sense, clarification_question)`
- `GuardrailResult(valid, reason)`
- `OutreachCampaignResult(linkedin_message, cold_email_subject, cold_email_body)`
- `ResumeExperience`, `ResumeProject`, `ResumeCertification`, `ResumeEducation` (with `model_validator` to normalize description string→list)
- `ResumeContentResult(professional_title, professional_summary, skills[], experience[], education[], projects[], certifications[], languages[])`
- `SectionFilterResult(include_sections[], exclude_sections[], reasoning)`
- `LearningPathItem(skill, importance, resources[], project_idea)`
- `LearningPathResult(items[])`

The full schema definitions are reproduced verbatim in **PART 6**.

## 15. Document Processing — pdfplumber, PyMuPDF, python-docx, xhtml2pdf

### PDF extraction (`profiles/services/cv_parser.py`)

Two paths, with PyMuPDF preferred when available:

```python
USE_PYMUPDF = False
try:
    import fitz  # PyMuPDF
    PDF_AVAILABLE = True
    USE_PYMUPDF = True
except ImportError:
    try:
        import pdfplumber
        PDF_AVAILABLE = True
    except ImportError:
        PDF_AVAILABLE = False
```

**PyMuPDF** wins on:
- Letter-spacing (PDF kerning artifacts: `B ACH ELOR` → `Bachelor`).
- Embedded link extraction with `from` rectangle that yields the link text.
- Cleaner Unicode handling.

**pdfplumber** wins on:
- Pure-Python install (no system deps).
- Page hyperlinks API.

The parser handles a malformed-URI case specifically (`github:%20https://...` → `https://...`) seen in real CVs.

### DOCX extraction

`python-docx` reads paragraphs, tables (candidates often put skills in tables), and walks `doc.part.rels` to find hyperlinks (LinkedIn URL is often a clickable link, not text).

### Sanitization (`_sanitize_text`)

A pre-LLM scrubber:
- Letter-spaced word repair via regex hit-list (`B\s*ACH\s*ELOR`, `IN\s*FORM\s*ATION`, etc.) with case-preserving collapse.
- Header/footer noise removal (`Page \d+ of \d+`, `Confidential`, `Curriculum Vitae`).
- Newline collapsing (3+ → 2), whitespace normalization.

### PDF generation (`resumes/services/pdf_generator.py`)

xhtml2pdf converts `templates/resumes/pdf_template_*.html` to PDF. Six templates:
- `pdf_template.html` — Default
- `pdf_template_compact.html` — Tight spacing
- `pdf_template_danette.html` — Sidebar layout
- `pdf_template_executive.html` — Two-column
- `pdf_template_minimalist.html` — Single-column, sans-serif
- `pdf_template_zeyad.html` — Personal style with accent color

Each uses inline styles (xhtml2pdf has limited CSS support — no flex, no grid, no modern selectors).

## 16. Frontend Stack — Tailwind CSS v4 + Alpine.js

### Tailwind CSS v4

Version 4 uses CSS-first configuration. There is **no `tailwind.config.js`**. The configuration lives entirely inside `static/src/input.css`:

```css
@import "tailwindcss";

@theme {
  --color-brand-50: ...;
  --color-brand-500: ...;
  /* ... */
  --font-display: "Fraunces", serif;
  --font-sans: "Inter", sans-serif;
  --font-mono: "IBM Plex Mono", monospace;
}

@layer base { ... }
@layer components { ... }
```

The CLI (`@tailwindcss/cli`) scans content paths declared in `input.css` (`@content "templates/**/*.html"`) and emits `output.css`. The compiled CSS is **committed** so `python manage.py runserver` works without a build step.

### Alpine.js

Loaded via CDN in `templates/base.html`:
```html
<script defer src="https://cdn.jsdelivr.net/npm/@alpinejs/collapse@3.x.x/dist/cdn.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
```

Used for:
- Drag-and-drop skill reclassification on the gap-analysis page (`x-data` + `dragstart`/`drop` handlers).
- Live form updates (typing in the resume editor recomputes ATS score in-browser).
- Modal state for confirmation dialogs.
- Auto-dismiss success toasts after 2 seconds (commit `c935e12`).
- Animate + autofocus newly-added form rows (commit `a167b71`).

### Fonts

Loaded from Google Fonts CDN:
- **Inter Variable** (UI sans-serif)
- **Fraunces Variable** (display serif — "Editorial AI" direction, see commit `a526cab`)
- **IBM Plex Mono** (code blocks)

## 17. Build Tools and Workflow

### Tailwind build

```bash
npm install                # First time
npm run build:css          # One-shot
npm run dev:css            # Watch mode for template work
```

Output: `static/css/output.css` (3722 lines after Tailwind v4's heuristics).

### Django commands

```bash
python manage.py runserver
python manage.py makemigrations
python manage.py migrate
python manage.py test
python manage.py test profiles
python manage.py test profiles.tests.CVParserTest.test_pdf_extraction
python manage.py shell
python manage.py createsuperuser
python manage.py collectstatic
```

### Benchmarks

```bash
python -m benchmarks.run_all                    # all phases except D5
python -m benchmarks.run_all --with-tailoring   # also runs LLM-judged tailoring
```

### Coverage

```bash
coverage run manage.py test
coverage report -m
coverage html              # Generates htmlcov/
```

`.coveragerc` excludes `migrations/`, `tests.py`, `tests_*.py`, and venvs.

## 18. Testing and Coverage

337 tests across 9 test files. Distribution:

| App | Test files | Tests | Notes |
|---|---|---|---|
| accounts | 1 | 6 | Auth flows |
| analysis | 1 | 34 | Gap analysis (LLM categorization, fuzzy reconciliation, fallback path) |
| core | 1 | 67 | Latency middleware, health checks, agent chat, error handlers |
| jobs | 1 | 15 | Scrapers, skill extractor |
| profiles | 4 | 180 | CV parser, chatbot (interviewer), outreach, prompt guards |
| resumes | 1 | 35 | Resume gen, ATS scoring, list↔string conversion |
| **Total** | **9** | **337** | All passing |

Overall coverage is **53%**. `core/` has **76.9%** coverage (the highest, due to deliberate test additions for observability and agent chat). Migrations and admin files are excluded.

Test database: in-memory SQLite (per `'test' in sys.argv` check in `settings.py`).

---

# PART 4 — Django Project Configuration

## 19. `smartcv/settings.py` — Annotated

The settings file is 244 lines. Every block is meaningful; here's the annotated walkthrough.

### Lines 1–13 — Imports and `.env` load

```python
from pathlib import Path
from decouple import config
import os
import sys
import dj_database_url
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

load_dotenv()
```

Both `python-decouple` and `python-dotenv` are loaded. `decouple` provides the typed `config()` helper (`config('DEBUG', default=True, cast=bool)`); `dotenv` ensures the `.env` file is loaded into `os.environ` before `decouple` reads it.

### Lines 15–39 — `BASE_DIR`, `SECRET_KEY` guard

```python
BASE_DIR = Path(__file__).resolve().parent.parent

_DEFAULT_SECRET = 'django-insecure-default-key'
SECRET_KEY = config('SECRET_KEY', default=_DEFAULT_SECRET)

DEBUG = config('DEBUG', default=True, cast=bool)

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
```

The guard was tightened in commit `b6e84bd`. The previous version only fired when `DEBUG=False`; now it fires on any non-test invocation. Tests get a default key; everything else hard-fails. This prevents silently using the placeholder key when `manage.py runserver` is run with `DEBUG=True` and a missing `.env`.

### Lines 41 — Allowed hosts

```python
ALLOWED_HOSTS = config('ALLOWED_HOSTS',
    default='localhost,127.0.0.1',
    cast=lambda v: [s.strip() for s in v.split(',')])
```

Comma-separated env var → list, parsed inline.

### Lines 46–66 — Installed apps

```python
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
```

Order is deliberate: `accounts` first among local apps so the custom `User` model resolves before any model that references it.

### Lines 68–98 — Middleware and debug toolbar

```python
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    "whitenoise.middleware.WhiteNoiseMiddleware",
    'django.contrib.sessions.middleware.SessionMiddleware',
    "corsheaders.middleware.CorsMiddleware",
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.RequestObservabilityMiddleware',  # Last so it sees the final status
]

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
            'SHOW_TEMPLATE_CONTEXT': True,
            'RESULTS_CACHE_SIZE': 10,
        }
```

The `RequestObservabilityMiddleware` is intentionally last so it observes the final response. Its `__call__` wraps `start = time.monotonic(); response = self.get_response(request); duration = time.monotonic() - start` and pushes to a per-route accumulator. Failures inside it are swallowed.

The debug-toolbar block is auto-disabled when:
- `DEBUG=False`
- The test runner is active (`'test' in sys.argv` or `'pytest' in sys.argv[0]`)

### Lines 100–116 — Templates

```python
ROOT_URLCONF = 'smartcv.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
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
```

Project-level templates resolve from `templates/`. `APP_DIRS=True` also loads per-app templates, which is used for app-specific overrides (rare).

`core.context_processors.onboarding` injects:
- `in_onboarding`: True if `request.session.get('in_onboarding')`
- (other onboarding flags as needed)

### Lines 118–151 — Database

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

if 'test' in sys.argv:
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
```

Annotation:
- `conn_max_age=60` — keep connections alive 60s.
- `conn_health_checks=True` — Django sends a `SELECT 1` on connection reuse.
- `DISABLE_SERVER_SIDE_CURSORS=True` — required for PgBouncer transaction mode.
- `sslmode=require` — Supabase enforces TLS.
- `connect_timeout=10` — saturated pool fails fast.

The SQLite swap happens for tests because PgBouncer holds connections that block test-DB creation.

### Lines 153–167 — Password validators

Standard Django validators (`MinimumLengthValidator`, `CommonPasswordValidator`, etc.).

### Lines 169–174 — i18n

```python
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True
```

### Lines 176–187 — Static and media

```python
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

if not DEBUG:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
```

`CompressedManifestStaticFilesStorage` provides versioned static files with gzip in production. Dev uses the default (no compression, no manifest).

### Lines 189–200 — Auth, DRF

```python
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'accounts.User'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    )
}
```

### Lines 202–212 — CORS, email, CSRF

```python
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
]

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = 'SmartCV <noreply@smartcv.local>'

CSRF_FAILURE_VIEW = 'core.views.csrf_failure'
```

Email goes to console (development). Production would swap this to an SMTP backend or transactional email provider.

### Lines 214–243 — Logging

```python
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
```

Console-only logging. `django` itself is at WARNING (suppress noisy DB SQL), root logger is at INFO. Services log via `logger = logging.getLogger(__name__)`.

## 20. `smartcv/urls.py` — Root URL Routing

```python
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('jobs/', include('jobs.urls')),
    path('profiles/', include('profiles.urls')),
    path('analysis/', include('analysis.urls')),
    path('resumes/', include('resumes.urls')),
    path('', include('core.urls')),  # Homepage and dashboard
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    if 'debug_toolbar' in settings.INSTALLED_APPS:
        import debug_toolbar
        urlpatterns = [
            path('__debug__/', include(debug_toolbar.urls)),
        ] + urlpatterns

handler404 = 'core.views.custom_404'
handler500 = 'core.views.custom_500'
```

App-prefixed URLs (`/accounts/`, `/jobs/`, etc.) include each app's `urls.py`. The root path `''` includes `core.urls` (home, dashboard, agent chat, applications, insights, welcome).

In DEBUG mode, media files are served (production uses WhiteNoise/nginx) and the debug-toolbar's `__debug__/` prefix is added.

Custom 404/500 handlers render `404.html` and `500.html` templates with `core/views.py:custom_404` / `custom_500`.

## 21. ASGI / WSGI Entry Points

`smartcv/wsgi.py` — Standard `get_wsgi_application()` Django boilerplate. WhiteNoise sits in the middleware stack; no extra wrapping needed.

`smartcv/asgi.py` — Standard `get_asgi_application()`. Currently unused; SmartCV is fully sync.

## 22. Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | Yes (prod) | Empty | Postgres URL with PgBouncer port 6543 + sslmode=require |
| `GROQ_API_KEY` | Yes | Empty | Groq inference API key |
| `GROQ_MODEL` | No | `meta-llama/llama-4-scout-17b-16e-instruct` | Override Groq model |
| `SECRET_KEY` | Yes (prod) | `django-insecure-default-key` | Django secret. Hard-fails on prod boot if not set. |
| `DEBUG` | No | `True` | Django debug mode |
| `ALLOWED_HOSTS` | No | `localhost,127.0.0.1` | Comma-separated list |
| `HF_API_KEY` | No | None | HuggingFace API (legacy paths only) |

## 23. Logging Configuration

Console-only handler. Root logger at INFO; `django` at WARNING. Services log via `logger = logging.getLogger(__name__)`.

The middleware that adds structured request traces is `core/middleware.py:RequestObservabilityMiddleware`. It records:
- Request method
- Path
- Status code
- Duration (monotonic)
- Per-route p50/p95/p99 over a rolling window

Exposed at `/healthz/metrics` (JSON snapshot) and `/healthz/deep/` (DB ping cached 15s).

## 24. Middleware Stack

In order of execution (request inbound → outbound):

1. `SecurityMiddleware` — HTTPS redirect, HSTS, X-Frame-Options.
2. `WhiteNoiseMiddleware` — Static file serving.
3. *(debug_toolbar inserted here in DEBUG mode)*
4. `SessionMiddleware` — Cookie-based sessions.
5. `CorsMiddleware` — CORS headers (configured for `localhost:3000`).
6. `CommonMiddleware` — `URL` normalization.
7. `CsrfViewMiddleware` — CSRF token verification. Custom failure view points to `core.views.csrf_failure`.
8. `AuthenticationMiddleware` — `request.user`.
9. `MessageMiddleware` — Flash messages (`messages.success(request, ...)`).
10. `XFrameOptionsMiddleware` — `X-Frame-Options: DENY`.
11. `RequestObservabilityMiddleware` — Last; observes final status + duration.

## 25. CSRF, CORS, Static, Media

**CSRF**: Default Django middleware. `CSRF_FAILURE_VIEW = 'core.views.csrf_failure'` renders `templates/403_csrf.html` (commit `10e3268` introduced this — replaces Django's bare 403 page with a friendly "session expired, please refresh" page. Logs the technical reason but doesn't show it).

**CORS**: `CORS_ALLOWED_ORIGINS = ["http://localhost:3000"]`. Used during a brief experiment with a separate frontend; mostly inert now.

**Static**: `STATIC_URL = '/static/'`, served by WhiteNoise. Production uses `CompressedManifestStaticFilesStorage`.

**Media**: `MEDIA_URL = '/media/'`, served from `media/` in DEBUG mode. Production would proxy to S3/CDN.

---

# PART 5 — Django Apps Deep Dive

## 26. `accounts/` — Custom User and Authentication

### Files

```
accounts/
├── migrations/
│   ├── 0001_initial.py
│   ├── 0002_user_outreach_token.py
│   └── __init__.py
├── __init__.py
├── admin.py        (3 lines)
├── apps.py         (6 lines)
├── models.py       (22 lines)
├── tests.py        (106 lines, 6 tests)
├── urls.py         (42 lines)
└── views.py        (91 lines)
```

### `models.py`

```python
from django.contrib.auth.models import AbstractUser
from django.db import models
import uuid

class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    outreach_token = models.UUIDField(null=True, blank=True, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    class Meta:
        db_table = 'users'

    def rotate_outreach_token(self) -> uuid.UUID:
        self.outreach_token = uuid.uuid4()
        self.save(update_fields=['outreach_token', 'updated_at'])
        return self.outreach_token
```

Fields:
- `id` — UUID primary key, immutable
- `email` — Unique email; doubles as `USERNAME_FIELD`
- `outreach_token` — UUID used by the Chrome extension to authenticate API calls. `unique=True, db_index=True`. Nullable so existing users can opt-in.
- `created_at`, `updated_at` — Standard timestamps

Methods:
- `rotate_outreach_token()` — Generates a new UUID, saves only the affected fields, returns the token. Used when a user wants to revoke a stolen extension token.

`USERNAME_FIELD = 'email'` makes email the login. `REQUIRED_FIELDS = ['username']` keeps Django's `createsuperuser` interactive prompt happy.

The `db_table = 'users'` override keeps the SQL table name human-readable (default would be `accounts_user`).

### `views.py`

Four functions:

**`register_view`** — Email + password + confirm. If passwords don't match or email is taken, re-render the form with an error. On success, create the user and `login()` them, then redirect to `welcome` (the first-run orchestrator at `/welcome/`).

**`login_view`** — Email + password. Authenticate via Django's `authenticate()`. On success, redirect to `dashboard`. On failure, re-render with the email pre-filled.

**`logout_view`** — Standard `logout()` then redirect to `home`.

**`account_settings_view`** (`@login_required`) — Currently handles only password change. Reads `current_password`, `new_password`, `confirm_new_password` from POST, validates, calls `set_password()` and `update_session_auth_hash(request, request.user)` to prevent logout after the change.

### `urls.py`

Routes:
- `register/` → `register_view`
- `login/` → `login_view`
- `logout/` → `logout_view`
- `password-reset/` → Django's `PasswordResetView` with `accounts/password_reset.html` template
- `password-reset/done/` → `PasswordResetDoneView`
- `password-reset-confirm/<uidb64>/<token>/` → `PasswordResetConfirmView`
- `password-reset/complete/` → `PasswordResetCompleteView`
- `settings/` → `account_settings_view`

Password reset uses Django's built-in views with custom templates. Email goes to console in dev; a real SMTP backend would be required in production.

### `tests.py`

Six tests:
1. `test_register_creates_user` — POST to `/accounts/register/` creates a user.
2. `test_register_password_mismatch` — Mismatched passwords re-render with error.
3. `test_register_duplicate_email` — Existing email returns error.
4. `test_login_with_valid_credentials` — Sets session.
5. `test_logout_clears_session` — `request.user.is_authenticated` becomes False.
6. `test_authenticated_user_redirected_from_login` — `/accounts/login/` redirects to `/dashboard/` if already logged in (commit `f21f398`).

### Templates (`templates/accounts/`)

- `login.html` — Email + password form. Top nav hidden via `{% block nav %}{% endblock %}` (commit `d9c0c85`).
- `register.html` — Email + password + confirm form. Same nav-hidden pattern.
- `settings.html` — Password change form.
- `password_reset.html`, `password_reset_done.html`, `password_reset_confirm.html`, `password_reset_complete.html`, `password_reset_email.html` — Password reset templates added in commit `f7c744a`.

## 27. `profiles/` — CV Parsing, Profiles, Outreach

### Files

```
profiles/
├── migrations/
│   ├── 0001_initial.py
│   ├── 0002_userprofile_github_url.py
│   ├── 0003_userprofile_raw_cv_data.py
│   ├── 0004_setup_vector.py
│   ├── 0005_remove_userprofile_certifications_and_more.py
│   ├── 0006_migrate_data.py
│   ├── 0007_remove_old_columns.py
│   ├── 0008_remove_raw_cv_data.py
│   ├── 0009_interviewsession.py
│   ├── 0010_delete_interviewsession.py
│   ├── 0011_jobprofilesnapshot.py
│   ├── 0012_alter_userprofile_embedding.py
│   ├── 0013_add_multi_vector_embeddings.py
│   ├── 0014_outreachcampaign_outreachaction.py
│   ├── 0015_discoveredtarget.py
│   └── __init__.py
├── services/
│   ├── cv_parser.py (~1000 lines)
│   ├── embeddings.py
│   ├── experience_math.py
│   ├── github_aggregator.py
│   ├── interviewer.py
│   ├── kaggle_aggregator.py
│   ├── linkedin_aggregator.py
│   ├── llm_engine.py (87 lines)
│   ├── llm_validator.py
│   ├── outreach_dispatcher.py
│   ├── outreach_generator.py
│   ├── profile_auditor.py
│   ├── profile_strength.py
│   ├── prompt_guards.py
│   ├── schemas.py (223 lines)
│   ├── scholar_aggregator.py
│   └── semantic_validator.py
├── __init__.py
├── admin.py
├── apps.py
├── models.py (213 lines)
├── tests.py (133 tests)
├── tests_interviewer.py (24 tests)
├── tests_outreach.py (15 tests)
├── tests_prompt_guards.py (8 tests)
├── urls.py (43 lines)
├── views.py (849 lines)
└── views_outreach_api.py (337 lines)
```

This is the largest app by every measure: most models, most services, most tests, most lines. It's the heart of SmartCV.

### `models.py` Walkthrough

**`UserProfile`** — One per user.
- `id: UUID` — PK.
- `user: OneToOneField(User)` — `related_name='profile'`.
- `input_method: CharField` — `'upload' | 'form' | 'chatbot'`.
- `full_name, email, phone, location` — Contact fields.
- `linkedin_url, github_url` — External profile URLs.
- `data_content: JSONField` — **The big one.** Stores skills, experiences, education, projects, certifications + dynamic sections (publications, awards, volunteer, github_signals, scholar_signals, kaggle_signals, has_seen_welcome).
- `embedding, embedding_skills, embedding_experience, embedding_education: VectorField(384)` — pgvector fields.
- `uploaded_cv: FileField(upload_to='cvs/')` — Original CV.
- `created_at, updated_at` — Timestamps.

Property accessors for backward compatibility:
- `profile.skills` ↔ `data_content['skills']`
- `profile.experiences` ↔ `data_content['experiences']`
- `profile.education` ↔ `data_content['education']`
- `profile.projects` ↔ `data_content['projects']`
- `profile.certifications` ↔ `data_content['certifications']`

GIN index on `data_content` (`jsonb_path_ops`) for fast JSONB lookup.

`db_table = 'user_profiles'`.

**`JobProfileSnapshot`** — Per-job profile variant.
- `id: UUID`, `profile: ForeignKey(UserProfile)`, `job: OneToOneField(Job)`.
- `data_content: JSONField` — Snapshot at chatbot-update moment.
- `pre_chatbot_data: JSONField` — Pre-chatbot state for rollback.
- `created_at`.

When the chatbot updates the profile for a specific job, the user can choose "this job only" — that creates a snapshot here and reverts the master profile. The snapshot is consulted on resume generation.

**`OutreachCampaign`** — One campaign per (user, job) pair.
- `id: UUID`, `user, job: ForeignKey`.
- `status: CharField` — `'draft' | 'running' | 'paused' | 'done' | 'failed'`.
- `daily_invite_cap: PositiveSmallIntegerField(default=15)`.
- `created_at, updated_at`.
- `db_table = 'outreach_campaigns'`, ordered by `-created_at`.

**`DiscoveredTarget`** — A LinkedIn profile the extension scraped.
- `id: UUID`, `user, job: ForeignKey`.
- `handle, name, role: CharField`.
- `source: CharField` — `'hiring_team' | 'people_you_know' | 'company_people'`.
- `discovered_at: DateTime`.
- Unique constraint: `(user, job, handle)`.

These are *candidate* targets the user hasn't queued yet. They survive until manually discarded.

**`OutreachAction`** — A queued message to send.
- `id: UUID`, `campaign: ForeignKey`.
- `target_handle, target_name, target_role: CharField`.
- `kind: CharField` — `'connect' | 'message'`.
- `payload: TextField` — The message body.
- `status: CharField` — `'queued' | 'in_flight' | 'sent' | 'accepted' | 'failed' | 'skipped'`.
- `attempts: PositiveSmallIntegerField`.
- `last_error: TextField`.
- `queued_at, completed_at: DateTime`.
- Unique constraint: `(campaign, target_handle, kind)`.
- Index: `(campaign, status)` for the queue-drain query.

The extension polls `/profiles/api/outreach/next` to dequeue the next `'queued'` action. On success it transitions to `'sent'`; on failure, increments `attempts` and writes `last_error`.

### `views.py` (849 lines)

Top-level routes:
- `/profiles/dashboard/` — Master dashboard (profile-strength ring, recent activity, pinned jobs).
- `/profiles/upload/` — CV upload UI (`upload_master_profile`).
- `/profiles/manual/` — Build profile by form (`build_profile_form`).
- `/profiles/chatbot/` — Conversational profile builder (`profile_chatbot_view`).
- `/profiles/review/` — Review parsed profile before save (`review_master_profile`).
- `/profiles/connect-accounts/` — GitHub/LinkedIn/Scholar/Kaggle connect step.
- `/profiles/refresh-signals/` — Re-fetch external signal snapshots.
- `/profiles/outreach/` — Per-user campaign list.
- `/profiles/outreach/<job_id>/` — Per-job campaign builder.

Key view: `upload_master_profile` (CV upload):
1. POST a PDF/DOCX file.
2. Save to `media/cvs/`.
3. Call `cv_parser.parse_cv(file_path)` → returns dict.
4. Validate via `llm_validator` (LLM consistency check).
5. Save into `UserProfile.data_content`.
6. Generate embeddings (synchronous; takes ~10–20s).
7. Redirect to `review_master_profile`.

Key view: `profile_chatbot_view`:
- Maintains a chat history in `request.session`.
- Each turn, calls `interviewer.next_turn(history, profile)` which returns a `ChatTurnResult` (analysis of user's reply + next question).
- If the user describes an experience/skill, the analysis includes `new_experience_bullets` (extracted as STAR-format) which get appended to `profile.experiences`.
- Cache-backed (commit `ae89394`) to recover from refresh.
- Loop detection (commit `9a5d127`) — if the last 3 questions are the same skill, force a topic change.

### `views_outreach_api.py` (337 lines)

Token-authenticated extension API. Each endpoint validates `Authorization: Bearer <token>` against `User.outreach_token`.

Endpoints:
- `POST /profiles/api/outreach/next` — Returns the next queued action for any of the user's running campaigns. Marks it `in_flight`, returns target + payload + selectors.
- `POST /profiles/api/outreach/result` — Extension reports back. Body: `{action_id, status, error?, evidence?}`. Updates `OutreachAction.status, last_error, completed_at`.
- `POST /profiles/api/outreach/discover` — Extension pushes scraped targets. Body: `{job_id, targets: [{handle, name, role, source}]}`. Creates `DiscoveredTarget` rows (unique-constraint dedupes).
- `POST /profiles/api/outreach/check-cap` — Extension checks if it should pause. Returns `{paused: bool, weekly_cap_hit: bool}`.
- `GET /profiles/api/outreach/status` — Status panel polling. Returns counts by status.

### Tests

`tests.py` — 133 tests covering:
- CV parsing (PDF and DOCX paths)
- Personal-info extraction (name, email, phone, location, LinkedIn, GitHub)
- Skill flattening + deduplication
- Education and experience parsing
- Letter-spaced word repair
- Embedded link extraction

`tests_interviewer.py` — 24 tests for the chatbot:
- Turn-by-turn flow
- Skill extraction from conversational text
- Loop detection
- Quality threshold

`tests_outreach.py` — 15 tests for outreach:
- Campaign creation
- Action queueing
- API auth
- Discovery dedup

`tests_prompt_guards.py` — 8 tests for human-voice filters:
- Banned word detection
- Specificity rule
- Opener variation rule

## 28. `jobs/` — Job Scraping and Skill Extraction

### Files

```
jobs/
├── migrations/
│   ├── 0001_initial.py
│   ├── 0002_setup_vector.py
│   ├── 0003_job_embedding.py
│   ├── 0004_job_application_status_recommendedjob.py
│   ├── 0005_alter_job_embedding.py
│   ├── 0006_alter_job_url_alter_recommendedjob_url.py
│   └── __init__.py
├── services/
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── base.py            # Abstract scraper, ScrapeError
│   │   ├── dispatcher.py       # URL host → scraper class
│   │   ├── generic.py          # JSON-LD fallback
│   │   ├── greenhouse.py
│   │   ├── indeed.py           # Playwright-based
│   │   ├── lever.py
│   │   └── linkedin.py
│   ├── __init__.py
│   ├── linkedin_scraper.py     # Legacy single-source scraper
│   ├── people_finder.py        # Company → emails/contacts
│   └── skill_extractor.py      # 193 lines, LLM + JD anchoring
├── __init__.py
├── admin.py
├── apps.py
├── models.py (48 lines)
├── tests.py (15 tests)
├── urls.py (11 lines)
└── views.py (284 lines)
```

### `models.py`

**`Job`**:
- `id: UUID`, `user: ForeignKey`.
- `url: URLField(max_length=2000, null=True, blank=True)` — bumped from default 200 in commit `bbc2524` because LinkedIn URLs (with tracking tokens) often exceed 200 chars.
- `title: CharField(200)`.
- `company: CharField(200, null=True, blank=True)`.
- `description: TextField`.
- `raw_html: TextField` — Original scraped HTML (for debugging and re-extraction).
- `extracted_skills: JSONField(default=list)` — List of skill name strings.
- `embedding: VectorField(384)` — pgvector.
- `application_status: CharField` — `'saved' | 'applied' | 'interviewing' | 'offer' | 'rejected'`.
- `created_at`.
- `db_table = 'jobs'`, ordered by `-created_at`.

**`RecommendedJob`** — Auto-generated.
- Plus `match_score: IntegerField(0-100)`.
- `status: CharField` — `'new' | 'saved' | 'dismissed'`.
- Ordered by `-match_score, -created_at`.

### Scraper Framework

`scrapers/base.py` defines:
```python
class ScrapeError(Exception): pass

class BaseScraper:
    def can_handle(self, url: str) -> bool: ...
    def scrape(self, url: str) -> dict: ...  # {title, company, description, raw_html, cleaned_url, source}
```

`scrapers/dispatcher.py` exports:
```python
def scrape_job(url: str) -> dict:
    for scraper_class in [LinkedInScraper, GreenhouseScraper, LeverScraper, IndeedScraper, GenericJSONLDScraper]:
        s = scraper_class()
        if s.can_handle(url):
            return s.scrape(url)
    raise ScrapeError("No scraper for that URL")
```

Each scraper's `can_handle` checks the URL host. `LinkedInScraper` matches `linkedin.com`; `GreenhouseScraper` matches `boards.greenhouse.io`; etc. `GenericJSONLDScraper` is the fallback — looks for `<script type="application/ld+json">` with a `JobPosting` schema.

`IndeedScraper` is Playwright-based (commit `9609e00`) because Indeed serves rendering-only-after-JS pages. Uses `page.inner_text()` (commit `2ddf64a`) to exclude inline `style` tag content that was leaking into the description.

### `skill_extractor.py`

Documented earlier. Pipeline:
1. Build prompt with JD text + strict rules ("explicitly mentioned only").
2. Call `get_structured_llm(SkillListResult)`.
3. Filter result through `_GENERIC_SOFT_SKILL_DENYLIST` (drop "Technical Leadership," "Problem Solving," etc. unless they appear verbatim in the JD).
4. Filter through `_is_jd_anchored()` — three passes (substring, trimmed-suffix substring, all words present).

This was tightened in commit `a80de9e` ("skill-extractor: cut hallucination 0.31 → 0.24 via prompt + JD anchoring") with 15 tests added.

### `views.py`

`job_input_view` — Unified input. POST with `input_method=url|text`. URL path goes through dispatcher; text path is a direct field-grab. Both paths run skill extraction synchronously.

`review_extracted_job` — User confirms title/company/description. If description is changed, embeddings are busted (`_bust_job_embedding`) and skills re-extracted.

`job_detail_view` — Display + status update. Shows CTAs based on profile + resume state (no profile → "Upload CV"; no resume → "Generate Resume"; existing resume → "View Resume").

`save_job_extension_view` — DRF endpoint for the Chrome extension to push scraped jobs.

`update_job_status_api` — Kanban drag-and-drop status update.

## 29. `analysis/` — Gap Analyzer, Learning Paths, Salary Tools

### Files

```
analysis/
├── migrations/
│   ├── 0001_initial.py
│   ├── 0002_gapanalysis_user_alter_gapanalysis_job_and_more.py
│   └── __init__.py
├── services/
│   ├── gap_analyzer.py (424 lines)
│   ├── learning_path_generator.py
│   ├── salary_negotiator.py
│   └── skill_score.py
├── __init__.py
├── admin.py
├── apps.py
├── models.py (22 lines)
├── tasks.py
├── tests.py (34 tests)
├── urls.py (11 lines)
└── views.py (305 lines)
```

### `models.py`

**`GapAnalysis`**:
- `id: UUID`, `job: ForeignKey`, `user: ForeignKey(null=True)`.
- `matched_skills, missing_skills, partial_skills: JSONField`.
- `similarity_score: FloatField(default=0.0)`.
- `created_at`.
- `unique_together = ('job', 'user')` — One analysis per (job, user) pair. Re-running overwrites.

### `gap_analyzer.py` — 424 lines

Already reproduced earlier. Key functions:

- `_enrich_skill_payload(skills)` — Converts dict-shaped skills (`{name, years, proficiency}`) into strings like `"Python - 3 years (Advanced)"`.
- `_build_full_candidate_context(profile)` — Builds the multi-section context string the LLM sees: skills, work experience (top 5), projects (top 5), certifications (top 10), education (top 3), plus GitHub/Scholar/Kaggle blocks.
- `_format_github_activity(profile)` — Produces a "GITHUB ACTIVITY (public, evidence-corroborates skills)" block listing username, repos, stars, recent commits, top 8 languages with repo counts, top 5 repos with stars + descriptions.
- `_format_scholar_activity(profile)` — Lists publications, citations, h-index, top 5 papers.
- `_format_kaggle_activity(profile)` — Tier, competition/dataset/notebook counts with medal emoji.
- `_signals(profile, key)` — Safe accessor for `profile.data_content[key]`.

The main function `compute_gap_analysis(profile, job)`:

**Early exits**:
- No `job.extracted_skills` → returns zero-score result with `analysis_method='no_job_skills'`.
- Empty profile → returns all-missing result.

**LLM call**: Prompt includes 5 critical rules:
- **RULE 1 — HOLISTIC EVIDENCE**: A skill is matched if demonstrated *anywhere* (skills list, experience highlights, projects, certifications, GitHub languages, Scholar publications, Kaggle medals). Foundational prerequisites count too.
- **RULE 2 — DIRECTIONAL SPECIFICITY**: Specific tool satisfies broad category (`MySQL` → `SQL` ✓), but broad doesn't satisfy specific (`Data Visualization` → `Tableau` ✗).
- **RULE 3 — NO DUPLICATES**: Each required skill in exactly one of `matched_skills` / `critical_missing_skills`.
- **RULE 4 — CASE-INSENSITIVE**: `PySpark` = `pyspark`.
- **RULE 5 — SENIORITY & CAREER-SWITCH SIGNALS** → `soft_skill_gaps`.

**Phase 2 reconciliation** (commit `4459b11`):
- Build `matched_set` and `missing_set` (lowercased).
- Drop anything that appears in both (LLM duplication).
- For every job skill, check if it's accounted for. If not, fuzzy-match (`difflib.get_close_matches`, cutoff 0.85) against `matched_set`. If a close match exists, count as matched. Otherwise, conservatively add to missing.

**Fallback** (no LLM): set difference with `difflib` cutoff 0.8.

The output dict includes `analysis_method: 'llm' | 'fallback' | 'no_job_skills' | 'empty_profile'` for telemetry.

### `views.py`

`gap_analysis_view(job_id)` — Renders gap analysis page. Loads cached `GapAnalysis` row first; if `?refresh=1` is passed, recomputes. The view performs a lot of derived calculations:
- `match_percentage = int(score * 100)`
- `gauge_fill = round(score * 364.4, 1)` (SVG circle circumference)
- `gauge_color` — Conditional `#639922` / `#BA7517` / `#E24B4A`.
- `matched_pct, missing_pct, soft_pct` — For the stacked bar.
- `primary_action` — `'generate_resume'` if >80%, `'chat_fill_gaps'` if 50–80%, `'learning_path'` if <50%.
- `evidence` — Calls `compute_evidence_confidence(profile)`.

`compute_gap_api(job_id)` — POST endpoint that triggers `tasks.compute_gap_analysis_task` synchronously. Returns success or detailed error JSON for the frontend.

`update_gap_skills(job_id)` — POST endpoint for the drag-and-drop UI. Accepts `{matched_skills, missing_skills, soft_skill_gaps}`. Persists, recomputes `similarity_score` via `skill_score.compute_match_score`. Live-updates added in commit `1cccf00`.

`generate_learning_path_view(job_id?)` — Aggregates missing skills across all user's jobs (or one specific job), takes top 5, calls `generate_learning_path()`.

`negotiate_salary_view(job_id)` — Generates a negotiation script via `generate_negotiation_script(profile, job, current_offer, target_salary)`.

## 30. `resumes/` — Tailored Resume Generation, Cover Letters, PDF Export

### Files

```
resumes/
├── migrations/
│   ├── 0001_initial.py
│   ├── 0002_generatedresume_name_coverletter.py
│   └── __init__.py
├── services/
│   ├── cover_letter_generator.py
│   ├── pdf_exporter.py (28 lines)
│   ├── pdf_generator.py (79 lines)
│   ├── resume_generator.py (375 lines)
│   └── scoring.py (188 lines)
├── templates/resumes/resume_template.html
├── __init__.py
├── admin.py
├── apps.py
├── models.py (30 lines)
├── tests.py (35 tests)
├── urls.py (21 lines)
└── views.py (445 lines)
```

### `models.py`

**`GeneratedResume`**:
- `id: UUID`, `gap_analysis: ForeignKey(GapAnalysis)`.
- `name: CharField(default='Tailored Resume')`.
- `content: JSONField` — Structured sections (matches `ResumeContentResult` schema).
- `html_content: TextField` — Rendered HTML.
- `ats_score: FloatField`.
- `version: IntegerField(default=1)`.
- `created_at`, ordered by `-created_at`.

**`CoverLetter`**:
- `id: UUID`, `job: ForeignKey`, `profile: ForeignKey`.
- `content: TextField`.
- `created_at`, ordered by `-created_at`.

### `services/resume_generator.py` — 375 lines

The `generate_resume_content(profile, job, gap_analysis)` function:

1. **Domain detection** via `_detect_job_domain(job)`. Keyword-based classifier (no LLM call). Categories: `software_engineering`, `data`, `design`, `product`, `marketing`, `sales`, `finance`, `general`. Each has a keyword list (~10–20 entries).

2. **Domain prompt addendum** via `_DOMAIN_PROMPTS`. Each domain gets ~5 rules tailored to that field. Examples:
   - `software_engineering` — "Lead bullets with shipped systems, scale, and tech stack."
   - `data` — "Lead bullets with business impact first, method second."
   - `design` — "Lead bullets with user outcomes, not deliverables."
   - `product` — "Lead bullets with metrics moved and strategic scope."
   - `marketing` — "Lead with the channel, the outcome, and the budget or reach."
   - `sales` — "Lead every bullet with a number: quota attainment, deal size, cycle length."
   - `finance` — "Specify the models, deal sizes, and frameworks."

3. **Slim CV preparation** — Drop `raw_text`, empty fields, `normalized_summary`, `objective`. Saves tokens.

4. **Prompt assembly**:
   - JOB DETAILS (title, company, required skills, description[:1000])
   - COMPLETE CV DATA (slim_cv as JSON)
   - MATCHED SKILLS (from gap analysis)
   - FIELD MAPPING table — explicit "CV `experiences[].highlights` → output `experience[].description`" mappings to prevent LLM mismapping.
   - STRICT ANTI-HALLUCINATION RULE
   - REMOVE FROM RESUMES list (street address, objective, GPA, photo, etc.)
   - LANGUAGE & STYLE (replace banned verbs)
   - BULLET POINT STANDARDS (3–5 per role, 1–2 lines, 15–25 words, STAR structure, different verbs each bullet)
   - LENGTH & DENSITY
   - REWRITE & STRUCTURING
   - ATS OPTIMIZATION
   - THEME MIRRORING
   - {domain_section}
   - {HUMAN_VOICE_RULE}

5. **LLM call**: `get_structured_llm(ResumeContentResult, temperature=0.7, max_tokens=8192)`.

6. **`_ensure_profile_data_preserved`** — Fills sections the LLM dropped. The LLM regularly returns empty `experience` or `certifications`, or uses profile field names (`graduation_year` instead of `year`). This function is the safety net.

### `services/scoring.py` — 188 lines

Two functions:

**`compute_ats_breakdown(content, job_skills) → AtsBreakdown`**:
1. Lowercase the entire resume JSON.
2. For each `job_skill`, count occurrences in the full text and in experience descriptions specifically.
3. `raw_score = matched / total * 100`.
4. `in_context_bonus = min(in_context_count * 2, 10)`.
5. `stuffing_penalty = stuffed_count * 5` where `stuffed = count > 4`.
6. `final = raw + bonus - penalty`, clamp to `[0, 100]`.

Constants:
- `STUFFING_THRESHOLD = 4`
- `STUFFING_PENALTY_PER_SKILL = 5.0`
- `IN_CONTEXT_BONUS_PER_SKILL = 2.0`

**`compute_evidence_confidence(profile) → EvidenceConfidence`**:
- 0–3 stars based on connected sources.
- GitHub counts if ≥1 public repo.
- Scholar counts if ≥1 publication or any citations.
- Kaggle counts if any non-zero category count.
- Returns label (`Untested` / `Limited` / `Moderate` / `Strong`) + sources list + detail.

Wrapper `calculate_ats_score(content, skills)` returns just the float for legacy callers.

### `views.py` — 445 lines

Major routes:
- `/resumes/list/` — All user's resumes.
- `/resumes/generate/<gap_id>/` — Generate page with template picker.
- `/resumes/generate/<gap_id>/run/` — POST trigger; calls `generate_resume_content` synchronously.
- `/resumes/edit/<resume_id>/` — Live editor with textareas + live ATS score.
- `/resumes/preview/<resume_id>/` — HTML preview.
- `/resumes/pdf/<resume_id>/` — PDF download via xhtml2pdf.
- `/resumes/cover-letter/<job_id>/` — Cover letter generator.
- `/resumes/delete/<resume_id>/` — Soft delete.

Edit view handles textarea↔list conversion via `_description_text_to_list` and `_description_list_to_text` helpers (commit `e9deb11` fixed bracket-notation corruption when raw lists were rendered as Python repr).

## 31. `core/` — Landing, Health, Observability, Agent Chat

### Files

```
core/
├── migrations/__init__.py    # No migrations needed (no models)
├── services/
│   ├── __init__.py
│   ├── action_planner.py (175 lines)
│   ├── agent_chat.py (313 lines)
│   └── career_stage.py (233 lines)
├── __init__.py
├── admin.py
├── apps.py
├── context_processors.py
├── health.py
├── metrics.py
├── middleware.py
├── models.py
├── tests.py (67 tests)
├── urls.py (26 lines)
└── views.py (257 lines)
```

### `views.py`

Routes (already reproduced):
- `home_view` → redirects authenticated users to dashboard, otherwise renders home.
- `dashboard_view` → legacy redirect to `dashboard` (the canonical dashboard now lives in `profiles`).
- `custom_404`, `custom_500`, `csrf_failure`.
- `design_system_view` → `/design/` styleguide showing every component primitive.
- `agent_chat_view` → `/agent/` global chat. Validates `?job=<uuid>` param, passes `job` to template if owned by user.
- `agent_chat_api` → POST endpoint. Validates `job_id` ownership. Calls `chat(user, history, message, job=job)`.
- `welcome_view` → `/welcome/` first-run orchestrator. Records `has_seen_welcome` on profile.
- `skip_onboarding_view` → POST-only. Clears `request.session['in_onboarding']`, redirects to dashboard.
- `applications_view` → `/applications/` Kanban board.
- `insights_view` → `/insights/` external signal tiles + top skills + recent gaps + recent resumes.

### `services/agent_chat.py` — 313 lines

The chat function `chat(user, history, message, job=None)`:
1. `_build_system_prompt(user, job=job)` — Builds the system prompt. If `job` is passed, includes a JOB CONTEXT block (gap analysis, snapshot variant, artifacts).
2. Constructs LangChain `[SystemMessage, HumanMessage, ...]`.
3. Calls `get_llm()` and invokes.
4. Returns `{reply, error?}`.

`_build_job_context_block(job)` — Renders:
- Job title, company, status.
- Latest `GapAnalysis` (matched/missing/partial counts, similarity score).
- `JobProfileSnapshot` if it exists ("snapshot variant").
- Linked artifacts (`GeneratedResume`, `CoverLetter`).

This was added in commit chain `758e2d1`–`bd89d44` (job-aware agent context, 11 commits).

### `services/action_planner.py` — 175 lines

Generates "next action" recommendations for the dashboard. Looks at:
- Profile completeness (do you have skills, experience, education?).
- Connected signals (GitHub, Scholar, Kaggle).
- Recent jobs (any with no gap analysis?).
- Recent gap analyses (any without a generated resume?).

Returns ordered actions like:
- "Upload your CV" (if no profile)
- "Connect GitHub for evidence" (if profile but no signals)
- "Run gap analysis on Acme Corp - SWE" (if job exists without gap analysis)
- "Generate tailored resume" (if gap exists without resume)

### `services/career_stage.py` — 233 lines

Classifies user's career stage from their profile:
- `entry` — <2 years experience, internships only
- `early_career` — 2–4 years
- `mid` — 4–8 years
- `senior` — 8+ years or "Senior" in job titles
- `lead_or_principal` — "Lead" / "Principal" in titles

The dashboard uses this to:
- Show stage-appropriate primary CTAs (commit `a5e6db3`).
- Surface "Ask agent about this role" chip for `interviewing` stage (commit `46b57c1`).

### `health.py`, `metrics.py`, `middleware.py`

`middleware.py` defines `RequestObservabilityMiddleware`:
```python
class RequestObservabilityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        try:
            response = self.get_response(request)
        finally:
            duration = time.monotonic() - start
            try:
                metrics.record(request.method, request.path, response.status_code, duration)
            except Exception:
                logger.exception("RequestObservabilityMiddleware: record failed")
        return response
```

`metrics.py` keeps an in-memory rolling window of latencies per `(method, path_pattern, status)` tuple. Computes p50, p95, p99, max on demand.

`health.py` exposes:
- `/healthz/` — Returns 200 if Python is alive.
- `/healthz/deep/` — Cached 15s `SELECT 1` to verify DB.
- `/healthz/metrics` — JSON snapshot of all per-route stats.

### `tests.py` — 67 tests

Largest single test file in the project. Covers:
- Latency middleware (no-op when response succeeds, accumulator updates correctly, swallows failures).
- Health endpoints (cheap and deep).
- Metrics serialization.
- 404/500 handlers render templates.
- CSRF failure renders custom page.
- Agent chat (job ownership, scope pill, system prompt assembly).
- Welcome orchestrator (records `has_seen_welcome`, short-circuits on repeat visits).
- Applications Kanban grouping.
- Insights view aggregation.

---


# PART 6 — LLM Integration and AI Architecture

## 32. Central LLM Engine (`profiles/services/llm_engine.py`)

The entire LLM surface of SmartCV is gated by this 87-line file. There are three public functions:

### `get_llm(temperature=0.3, max_tokens=4096) -> ChatGroq`

Returns a raw `ChatGroq` instance for plain-text generation (cover letters, salary scripts, agent chat, anything not requiring structured output).

```python
def get_llm(temperature: float = 0.3, max_tokens: int = 4096) -> ChatGroq:
    return ChatGroq(
        model=LLM_MODEL,
        api_key=GROQ_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=1,
        timeout=20,
    )
```

`max_retries=1` and `timeout=20` are deliberate. Retries are kept low because the typical failure (API outage, rate limit) won't be cured by retrying immediately, and the user has explicit retry buttons in the UI for the cases where they want to try again. Timeout is 20s because Groq usually responds in 2 to 3 seconds and anything taking 20s probably won't return at all.

### `get_structured_llm(pydantic_schema, temperature=0.1, max_tokens=8000)`

Returns a ChatGroq instance bound to a Pydantic schema via `with_structured_output()`. The output is **guaranteed** to be a valid instance of `pydantic_schema` — no manual JSON parsing needed.

```python
def get_structured_llm(pydantic_schema, temperature: float = 0.1, max_tokens: int = 8000):
    llm = ChatGroq(
        model=LLM_MODEL,
        api_key=GROQ_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=1,
        timeout=20,
    )
    return llm.with_structured_output(pydantic_schema)
```

Default temperature is 0.1 because structured output is for categorization/extraction (low-creativity) tasks. Resume generation overrides to 0.7 in `resume_generator.py`.

### `get_llm_client()` — Legacy shim

Mimics the old OpenAI client API (`client.chat.completions.create(model, messages, ...)`). Lets pre-LangChain code keep working.

The shim is used by `cv_parser.py:_refine_data_with_llm` (which still references the old API). This is the only remaining caller and is documented in CLAUDE.md as deprecated.

### Configuration

```python
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
LLM_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
```

Read at import time. Setting `GROQ_MODEL` to e.g. `meta-llama/llama-4-maverick-17b-128e-instruct` lets you swap the model without code changes.

## 33. Pydantic Schema Catalog (`profiles/services/schemas.py`)

223 lines, 25 distinct schema classes. Every Pydantic model used by the LLM pipeline is defined here. Changes here propagate everywhere — the schemas drive both LLM output validation and the shape of `UserProfile.data_content`.

### CV / Profile schemas

`Skill(name, proficiency?, years?)` — A single skill entry. Proficiency is `"Beginner" | "Intermediate" | "Advanced" | "Expert"`. Years is a float (allowing 1.5 years, etc.).

`Experience(title, company, start_date?, end_date?, description?, highlights[], industry?, location?, achievements[])` — A work experience entry. `description` is `Optional[Union[str, List[str]]]` because the LLM sometimes returns a single multiline string and sometimes a list of bullets. Pydantic accepts both. `model_config = ConfigDict(extra='allow')` permits the LLM to add fields like `team_size` or `tech_stack` without validation failure.

`Education(degree, institution, graduation_year?, field?, gpa?, honors[], location?)` — An education entry. Similar `extra='allow'` policy.

`Project(name, description?, role?, highlights[], technologies[], url?)` — A portfolio project.

`Certification(name, issuer?, date?, duration?, url?)` — A certification.

`ItemDetailed(title, organization?, date?, description?, url?)` — Generic structure used for awards, volunteer experience, publications, patents, speaking engagements, military experience, references, courses.

### Master `ResumeSchema`

The top-level shape that `UserProfile.data_content` follows. Includes:
- Contact info: `full_name`, `email?`, `phone?`, `location?`, `linkedin_url?`, `github_url?`, `portfolio_url?`, `other_urls[]`
- Generated text: `normalized_summary?`, `objective?`
- Required structured lists: `skills[]`, `experiences[]`, `education[]`
- Optional structured lists: `projects[]`, `certifications[]`
- Extended fields: `languages[]`, `volunteer_experience[]`, `awards[]`, `publications[]`, `speaking_engagements[]`, `patents[]`, `military_experience[]`, `hobbies[]`, `references[]`, `courses[]`
- `model_config = {"extra": "allow"}` — Permits dynamic CV sections (`github_signals`, `scholar_signals`, `kaggle_signals`, `has_seen_welcome`, etc.) to live alongside the structured fields.

### LLM output schemas

`GapAnalysisResult(critical_missing_skills[], soft_skill_gaps[], matched_skills[], similarity_score: float)` — Output of `gap_analyzer.compute_gap_analysis`. Used with `get_structured_llm(GapAnalysisResult)`.

`SkillListResult(skills[])` — Output of `skill_extractor.extract_skills`. Single-field schema for clean LLM output.

`ExtractedExperienceBullet(company_or_project_name, bullet_point)` — Used by the chatbot when it parses a user's free-text reply describing an achievement. The LLM identifies which company/project the bullet applies to (or "General") and extracts a STAR-format bullet (max 120 chars).

`ChatReplyAnalysis(is_valid, quality_score, clarification_prompt, skills_to_add[], all_technologies_mentioned[], new_experience_bullets[])` — Sub-schema for the chatbot's per-turn analysis of the user's reply.

`ChatNextQuestion(question, topic_skill)` — Sub-schema for generating the next chatbot question. Includes `topic_skill` for loop detection.

`ChatTurnResult(reply_analysis, next_question_generation)` — The full per-turn output. Composes the two sub-schemas above.

`SemanticValidationResult(makes_sense, clarification_question)` — Used by `semantic_validator.py` to LLM-check whether parsed CV data is internally consistent.

`GuardrailResult(valid, reason)` — Used by `interviewer.py` to validate the user's chatbot reply isn't hostile/off-topic before processing.

`OutreachCampaignResult(linkedin_message, cold_email_subject, cold_email_body)` — Output of `outreach_generator.generate_campaign`.

### Resume output schemas

`ResumeExperience(title, company, duration, description: Union[str, List[str]])` — With `@model_validator(mode='before')` that splits a multiline string into a list.

`ResumeProject(name, description: Union[str, List[str]], url)` — Same validator.

`ResumeCertification(name, issuer, date, url)`

`ResumeEducation(degree, institution, year)`

`ResumeContentResult(professional_title, professional_summary, skills[], experience[], education[], projects[], certifications[], languages[])` — The output of `resume_generator.generate_resume_content`. Drives the resume editor UI and PDF templates.

`SectionFilterResult(include_sections[], exclude_sections[], reasoning)` — Used in early experiments with section-filtering before resume generation. Currently unused in the production path.

`LearningPathItem(skill, importance, resources[], project_idea)` — Single learning path item.

`LearningPathResult(items[])` — Output of `learning_path_generator.generate_learning_path`.

The `@model_validator(mode='before')` on `ResumeExperience.description` and `ResumeProject.description` runs before validation: if the LLM returned a multiline string, it splits on newlines into a list. This is the canonical fix that survived from the bug fix in commit `e9deb11`.

## 34. CV Parsing Prompt Architecture

CV parsing happens in two stages:

**Stage 1 — Regex/heuristic extraction** (`cv_parser.py:CVExtractor`):
- PDF/DOCX text extraction with embedded link recovery.
- `_sanitize_text` — letter-spaced word repair, header/footer noise removal.
- Section header detection via fuzzy regex matching.
- Per-section parsers (`extract_experience`, `extract_education`, `extract_skills`, etc.) that split sections by date pattern or all-caps headers.
- Personal info extraction (`extract_personal_info`) — strict regex for emails, phones, URLs; conservative name detection that prefers null over wrong; location detection that requires "City, State/Country" comma format or known-city list.

**Stage 2 — LLM refinement** (`_refine_data_with_llm`):
- Optional, called only when `use_llm_refinement=True`.
- The current default in `parse_cv()` is `use_llm_refinement=False` because LLM refinement is handled downstream by `llm_validator.py` instead.

The wrapper `parse_cv(file_path)`:
1. Initialize `CVExtractor(use_llm=True)`.
2. Call `extractor.parse(file_path, use_llm_refinement=False)`.
3. Flatten skills dict (`{technical_skills, soft_skills, tools, frameworks}`) into a single list of `{name, proficiency, category}` dicts.
4. Filter through `_is_plausible_skill_name()` — drops `"Increased sales by 40%."` (sentence fragment), `"[Embedded Link: ..."` (extraction artifact), `"www.enhancv.com"` (URL fragment), etc.
5. Collect `other_urls` (Kaggle, etc.).
6. Return a flat dict matching `ResumeSchema` field names.

The `_is_plausible_skill_name` filter was added in commit `1b41469` ("parser: drop PDF-noise skills + scope skills-F1 metric to in-scope CVs (#2)") with 8 tests. It rejects:
- Empty strings
- Strings <2 chars or >40 chars
- Sentence fragments ending in `.`
- Strings starting with non-alpha (`(React)`, digit-leading)
- Strings with >4 words (bullet body, not a skill)
- Strings containing `[embedded`, `www.`, `http`, or `\\u` (Unicode escape)
- Strings containing `\\d+\\s*%` (statistics, not skills)

The letter-spaced word repair in `_sanitize_text` deserves special mention. PDFs with wide character spacing produce text like `B ACH ELOR` instead of `Bachelor`. The regex hit-list covers ~20 common words seen in real CVs:

```
B\\s*ACH\\s*ELOR, M\\s*AST\\s*ER, IN\\s*FR\\s*OM\\s*ATION, IN\\s*FORM\\s*ATION,
TECH\\s*N\\s*OL\\s*O\\s*G\\s*Y, COM\\s*PUTER, SCIEN\\s*CE, DIG\\s*ITAL,
PION\\s*E\\s*E\\s*R\\s*S, IN\\s*ITIATIVE, COUR\\s*SER\\s*A, DATA\\s*CAM\\s*P,
ENG\\s*IN\\s*EER, MAN\\s*AGE\\s*MENT, CERT\\s*IF\\s*IC\\s*AT,
PROF\\s*ESS\\s*ION\\s*AL, EXP\\s*ER\\s*IENCE, ED\\s*UC\\s*ATION,
FOR\\s*ENS\\s*ICS, FOR\\s*ENS\\s*IC
```

Each match goes through `_case_preserving_collapse` which detects whether the original text was all-caps, title-case, or lowercase and applies the same casing to the collapsed result. Commit `620c8f1` added the case preservation after the original implementation lost casing.

## 35. Skill Extraction Prompt + JD Anchoring

`jobs/services/skill_extractor.py:extract_skills(text)` returns a list of skill name strings. The function is 50 lines plus a 100-entry skill knowledge base kept for reference.

**The prompt** (verbatim):

```
You are an expert AI recruiter system.
Extract key professional skills, tools, frameworks, programming languages,
and technologies from the following job description text.

Guidelines:
1. Extract ONLY technical skills, tools, frameworks, languages, platforms,
   and named technologies.
2. Try to map extracted skills to canonical names if appropriate (e.g.
   "aws" -> "AWS", "gen ai" -> "Generative AI", "k8s" -> "Kubernetes").
3. Return unique skill names.

=== STRICT ANTI-HALLUCINATION RULES (CRITICAL) ===
- Only list skills explicitly mentioned in the job description text.
  Do not invent skills.
- A skill is "explicitly mentioned" only if its name (or a well-known alias)
  appears verbatim in the text.
- DO NOT include generic soft skills. The following are BANNED unless the
  exact phrase appears verbatim in the JD:
  Technical Leadership, Problem Solving, Communication, Teamwork, Collaboration,
  Code Review, Pair Programming, Pairing Sessions, Mentorship, Leadership.
- DO NOT infer skills from job seniority, company type, or industry.
  If it isn't in the text, do not list it.
```

**Post-LLM filtering** — Defense in depth.

`_GENERIC_SOFT_SKILL_DENYLIST` is checked first. If a returned skill is in the denylist *and* doesn't appear in the JD text (case-insensitive substring), drop it.

Then `_is_jd_anchored(skill, jd_lower)`:
1. Full skill name appears as substring of JD.
2. Strip common suffixes (` pipelines`, ` apis`, ` workflows`, ` testing`, ` clients`, ` sessions`) — match if trimmed appears.
3. All alphabetic words >2 chars in skill name appear in JD.

Skills failing all three are dropped. The benchmark shows this cut hallucination rate from 0.31 to 0.24 (commit `a80de9e`).

The function uses `temperature=0.0` and `max_tokens=512` — extraction is deterministic and the output is always small (a list of skill names).

## 36. Two-Phase Gap Analysis Prompt

The full LLM prompt in `analysis/services/gap_analyzer.py:compute_gap_analysis` is the most elaborate prompt in the codebase. It runs every time a user clicks "Run gap analysis" on a job page (unless cached).

The candidate context (`_build_full_candidate_context`) interleaves multiple data sources:

```
CANDIDATE SKILLS: Python, SQL, Pandas, scikit-learn, ...

WORK EXPERIENCE:
- Junior Data Scientist at Acme Corp: Built churn model that ...
  | Highlights: Reduced churn 12%; Deployed via Sagemaker; ...
- Data Analyst Intern at BetaCo: Built dashboards in Tableau ...

PROJECTS:
- Customer-Segmentation: K-means clustering on 2M event records
  | Used PyCaret for AutoML benchmarking | [Technologies: Python, scikit-learn, Streamlit]

CERTIFICATIONS & TRAINING:
- IBM Data Science Professional Certificate (Coursera)
- AWS Certified Cloud Practitioner

EDUCATION:
- B.Sc. in Computer Science from Cairo University

GITHUB ACTIVITY (public, evidence-corroborates skills):
- @user — 24 public repos, 8 total stars, 142 commits in last 90 days
- Primary languages by repo count: Python (12 repos), Jupyter Notebook (5 repos), TypeScript (3 repos), ...
- ml-pipeline-template [Python] — 4★: Reusable ML pipeline scaffold ...

GOOGLE SCHOLAR (academic publications + citation impact):
- Author Name (Cairo University)
- Citations: 47 total · h-index: 3 · i10: 1
- "Predictive maintenance with deep autoencoders" · IEEE Trans · 2024 — 18 citations

KAGGLE (data-science platform — competitions, notebooks, datasets):
- @username (Display Name) — overall tier: Expert
- Competitions: 14 · Expert · medals 🥈2 🥉3
- Notebooks: 31 · Expert
```

External signal blocks are conditional — if `profile.data_content[*_signals]` is missing or has an `error` key, the block is omitted.

The five matching rules (verbatim earlier in this document) cover holistic evidence, directional specificity, no duplicates, case-insensitivity, and seniority/career-switch signals.

**Phase 2 reconciliation** (post-LLM):
- Build `matched_set` and `missing_set` from the LLM result.
- Drop anything in both (LLM duplication).
- For every JD skill, check if it's accounted for. If not, fuzzy-match against `matched_set` with `cutoff=0.85`. Variant spellings are forgiven; truly absent skills go to missing.

This guarantees 100% coverage. The benchmark shows 49 of 50 pairs achieve full coverage (99.9%).

The output dict includes `analysis_method: 'llm' | 'fallback' | 'no_job_skills' | 'empty_profile'` for telemetry.

## 37. Domain-Aware Resume Generation

`resumes/services/resume_generator.py` builds a domain-aware prompt. The keyword-based domain classifier picks one of:

`software_engineering`, `data`, `design`, `product`, `marketing`, `sales`, `finance`, `general`

Each non-`general` domain has a 5-bullet addendum. Examples:

**`software_engineering`**:
```
=== DOMAIN EMPHASIS: SOFTWARE ENGINEERING ===
- Lead bullets with shipped systems, scale, and tech stack.
- Highlight: languages/frameworks used, scale metrics (QPS, users, rows, uptime),
  latency/perf improvements, system design decisions, test/deploy pipelines.
- Prefer concrete verbs: Built, Implemented, Shipped, Deployed, Refactored, Optimized, Debugged.
- Skills section: name the exact tools (Python 3, PostgreSQL, Kubernetes, Redis, AWS Lambda).
```

**`data`**:
```
=== DOMAIN EMPHASIS: DATA / ML ===
- Lead bullets with business impact first, method second.
  Example: 'Cut churn 12% by building a retention model in PyTorch trained on 2M events.'
- Name models, libraries, and datasets explicitly (XGBoost, scikit-learn, TensorFlow, pandas, Snowflake, dbt).
- Preferred verbs: Modelled, Predicted, Forecasted, Validated, Deployed, Instrumented, Analyzed.
- Keep statistical rigor: 'AUC 0.87 on held-out set' beats 'accurate model'.
- If the role is analyst-track, emphasize dashboards, SQL, stakeholder storytelling.
```

**`design`**:
```
=== DOMAIN EMPHASIS: DESIGN ===
- Lead bullets with user outcomes, not deliverables.
  Example: 'Redesigned onboarding; activation rose 24% across 3 release cycles.'
- Mention process: research method (user interviews, A/B tests, usability studies), design artifacts.
- Name tools (Figma, Sketch, Adobe XD, Framer, Principle).
- Preferred verbs: Designed, Prototyped, Researched, Iterated, Shipped, Partnered, Defined.
- Consider adding a 'Portfolio' link in the header if the candidate has one.
```

**`product`**:
```
=== DOMAIN EMPHASIS: PRODUCT MANAGEMENT ===
- Lead bullets with metrics moved and strategic scope.
  Example: 'Owned checkout rewrite; conversion +8%, cart abandonment -15% in 2 quarters.'
- Every bullet should answer: what did you ship, who benefited, what was the measurable outcome.
- Preferred verbs: Led, Owned, Launched, Prioritized, Aligned, Discovered, Defined.
- Signal cross-functional leadership without buzzwords.
- Skills section: frameworks (JTBD, OKRs, RICE), tools (Amplitude, Mixpanel, Figma, Jira).
```

**`marketing`**, **`sales`**, **`finance`** — Each with similar 5-rule templates focused on quantified outcomes.

After the LLM call, `_ensure_profile_data_preserved` fills any sections the LLM dropped from the source profile. Specifically:
- If `experience` is empty but `profile_data['experiences']` has content, rebuild from profile (mapping `start_date`/`end_date` → `duration`, `highlights` → `description`).
- If `education` entries have empty `year`, patch from profile (mapping `graduation_year` → `year`).
- If `projects` is empty but `profile_data['projects']` has content, rebuild.
- If `certifications` is empty but profile has them, rebuild.
- If `languages` is empty, copy from profile.
- If `skills` is empty, copy from profile.

This safety net was introduced in commit `334b532` ("Guarantee resume content is populated from profile even if LLM drops sections").

## 38. Anti-Hallucination Strategies

Multiple layers of defense against hallucinated content:

1. **Prompt rules** — Explicit `STRICT ANTI-HALLUCINATION RULE` block in every generative prompt (resume gen, cover letter, skill extraction).

2. **JD anchoring** (`skill_extractor._is_jd_anchored`) — Every extracted skill must appear in the JD text via substring, trimmed-suffix substring, or all-words-present check. Drops ~20% of LLM output as hallucinations.

3. **Soft-skill denylist** — Hardcoded list of LLM-favorite buzzwords that get dropped unless they appear verbatim in the JD.

4. **PDF-noise filter** (`_is_plausible_skill_name`) — Drops sentence fragments, URL fragments, percentage strings from CV-parser skill output.

5. **Phase 2 reconciliation** in gap analysis — Catches LLM duplications and drops; ensures every JD skill appears in exactly one bucket.

6. **Field mapping table** in resume gen prompt — Explicit "CV `experiences[].highlights` → output `experience[].description`" mappings to prevent the LLM from mismapping similar field names.

7. **`_ensure_profile_data_preserved`** — Post-LLM safety net. If the LLM dropped a section (e.g., empty `certifications`), it gets repopulated from the source profile.

8. **Programmatic entity grounding** (benchmark D5) — Verifies that 87.5% of generated companies/schools appear verbatim in the source CV. The 12.5% that don't are typically slight rewordings rather than fabrications.

## 39. Prompt Guards and Human-Voice Filters

`profiles/services/prompt_guards.py` defines `HUMAN_VOICE_RULE`, a string constant appended to all generative prompts. It contains:

- **BANNED WORDS** — Spearheaded, Leveraged, Utilized, Synergized, Streamlined, Robust, Demonstrated, Facilitated, Empowered, Driven, Passionate, Innovative, Cutting-edge, Game-changer, Disruptor, Visionary, Rockstar, Ninja, Guru, Self-starter, Detail-oriented, Results-driven, Highly motivated.

- **OPENER VARIATION RULE** — Never start two consecutive bullets with the same word. Never start three bullets in the same role with verbs from the same family.

- **SPECIFICITY RULE** — Generic claims are weaker than specific ones. Use specifics from the source CV; don't fabricate metrics.

Tests in `tests_prompt_guards.py` (8 tests) verify:
- The constant is non-empty and includes the banned words.
- The constant is appended to the resume gen prompt.
- Specific banned words trigger fail in a synthetic LLM-output check.

The voice rules are part of why the LLM-judged `human_voice` benchmark score sits at 5.6/10 — measurably better than vanilla output but still imperfect. The opener-variation and specificity rules account for most of the human-voice gain in the latest benchmark run.

---

# PART 7 — Database Schema and Models

## 40. Core Models — User, UserProfile, Job, GapAnalysis, GeneratedResume

The complete model graph with relationships:

```
User (accounts.User)
  ├── id: UUID (PK)
  ├── email: unique
  ├── outreach_token: UUID (unique, db_index, nullable)
  ├── created_at, updated_at
  └── (1:1) → UserProfile
              ├── id: UUID
              ├── input_method
              ├── full_name, email, phone, location
              ├── linkedin_url, github_url
              ├── data_content: JSONB    # The big one
              ├── embedding: VectorField(384)
              ├── embedding_skills, embedding_experience, embedding_education
              ├── uploaded_cv: FileField
              ├── created_at, updated_at
              └── GIN index on data_content

User (1:N) Job
        ├── id, user, url, title, company, description, raw_html
        ├── extracted_skills: JSONB
        ├── embedding: VectorField(384)
        ├── application_status: 'saved' | 'applied' | 'interviewing' | 'offer' | 'rejected'
        └── created_at

User (1:N) RecommendedJob
        └── (auto-generated, similar shape + match_score)

Job (1:N) GapAnalysis
       ├── id, job, user
       ├── matched_skills, missing_skills, partial_skills: JSONB
       ├── similarity_score: float
       ├── created_at
       └── unique_together = ('job', 'user')

GapAnalysis (1:N) GeneratedResume
       ├── id, gap_analysis
       ├── name (default 'Tailored Resume')
       ├── content: JSONB    # Matches ResumeContentResult
       ├── html_content: text
       ├── ats_score: float
       ├── version: int
       └── created_at

Job (1:N) CoverLetter
      ├── id, job, profile
      ├── content: text
      └── created_at

UserProfile (1:N) JobProfileSnapshot
       ├── id, profile, job
       ├── data_content: JSONB
       ├── pre_chatbot_data: JSONB
       └── created_at

User (1:N) OutreachCampaign (1:N) OutreachAction
       ├── Campaign: status, daily_invite_cap, created_at, updated_at
       └── Action: target_handle, kind, payload, status, attempts, last_error,
                   queued_at, completed_at
                   indexes: (campaign, status)
                   unique: (campaign, target_handle, kind)

User (1:N) DiscoveredTarget
       └── unique: (user, job, handle)
```

## 41. Outreach Models

**`OutreachCampaign`** — One per (user, job) pair. Tracks campaign-level state.

**`OutreachAction`** — A queued message to be sent. Composite-key unique on `(campaign, target_handle, kind)` so the same target can't be queued twice for the same connect-with-note action. The `(campaign, status)` index supports the queue-drain query (`SELECT * WHERE campaign=? AND status='queued' LIMIT 1 FOR UPDATE SKIP LOCKED`).

**`DiscoveredTarget`** — A LinkedIn profile the extension scraped. Pre-action staging area. `(user, job, handle)` unique constraint prevents duplicates from the same job page.

## 42. Snapshot Models — JobProfileSnapshot

When the chatbot updates the profile during a per-job conversation, the user can choose to limit changes to a single application. That triggers `JobProfileSnapshot` creation:
- `data_content` — Snapshot at the moment the chatbot updated for THIS job.
- `pre_chatbot_data` — The pre-chatbot state, used to revert the master profile.

Resume generation for this job consults the snapshot first, falling back to the master profile.

## 43. CoverLetter, RecommendedJob

**`CoverLetter`** — Generated cover letters. Linked to both `Job` and `UserProfile`. Plain text storage (`TextField`), no HTML — cover letters are short and templating overhead doesn't pay off.

**`RecommendedJob`** — Auto-generated job recommendations. Includes `match_score` (0-100). `status` cycles through `'new' | 'saved' | 'dismissed'`. Currently dormant; the recommendation engine isn't wired into the production path but the model exists for future work.

## 44. Migrations (every migration, in order)

### `accounts/migrations/`
- `0001_initial.py` — Creates `User` model with UUID PK, email unique, custom `db_table='users'`.
- `0002_user_outreach_token.py` — Adds `outreach_token: UUIDField(null, unique, db_index)`. Created when the Chrome extension was added.

### `profiles/migrations/`
The most active migration history in the codebase, reflecting the schema evolution from per-section tables to JSONB:

- `0001_initial.py` — Initial `UserProfile` with separate fields per section (skills, experiences, education, certifications). Original design.
- `0002_userprofile_github_url.py` — Adds `github_url`.
- `0003_userprofile_raw_cv_data.py` — Adds `raw_cv_data: JSONField`. Intermediate stage.
- `0004_setup_vector.py` — `CREATE EXTENSION IF NOT EXISTS vector;` + adds `embedding: VectorField(384)`.
- `0005_remove_userprofile_certifications_and_more.py` — Drops separate certifications field.
- `0006_migrate_data.py` — Data migration: copies data from old per-section fields into a new `data_content` JSONB.
- `0007_remove_old_columns.py` — Drops the old per-section columns (skills, experiences, education columns).
- `0008_remove_raw_cv_data.py` — Drops `raw_cv_data` (consolidated into `data_content`).
- `0009_interviewsession.py` — Added an `InterviewSession` model for chatbot state.
- `0010_delete_interviewsession.py` — Deleted it (state moved to cache, commit `ae89394`).
- `0011_jobprofilesnapshot.py` — Adds `JobProfileSnapshot`.
- `0012_alter_userprofile_embedding.py` — Vector column tweaks.
- `0013_add_multi_vector_embeddings.py` — Adds `embedding_skills, embedding_experience, embedding_education`.
- `0014_outreachcampaign_outreachaction.py` — Adds outreach models.
- `0015_discoveredtarget.py` — Adds `DiscoveredTarget` (extension v2, commit `6c15f64`).

### `jobs/migrations/`
- `0001_initial.py` — Initial `Job` model.
- `0002_setup_vector.py` — `CREATE EXTENSION IF NOT EXISTS vector;` + `embedding`.
- `0003_job_embedding.py` — Adjusts the embedding column.
- `0004_job_application_status_recommendedjob.py` — Adds `application_status` + `RecommendedJob`.
- `0005_alter_job_embedding.py` — Re-tweaks vector.
- `0006_alter_job_url_alter_recommendedjob_url.py` — Bumps `url.max_length` to 2000 (commit `bbc2524`).

### `analysis/migrations/`
- `0001_initial.py` — Initial `GapAnalysis`.
- `0002_gapanalysis_user_alter_gapanalysis_job_and_more.py` — Adds `user` FK + alters `job` FK.

### `resumes/migrations/`
- `0001_initial.py` — Initial `GeneratedResume`.
- `0002_generatedresume_name_coverletter.py` — Adds `name` + `CoverLetter`.

## 45. pgvector Usage and Multi-Vector Architecture

384 dimensions correspond to the `sentence-transformers/all-MiniLM-L6-v2` model. Vector fields:

**`UserProfile`**:
- `embedding` — Whole-profile embedding (single dense vector).
- `embedding_skills` — Just the skills section.
- `embedding_experience` — Just the experiences section.
- `embedding_education` — Just the education section.

**`Job`**:
- `embedding` — Whole-job embedding.

The multi-vector architecture (Phase 1 in `docs/implementation_plan.md`) was designed to allow weighted similarity:

```
total_similarity = α·sim(profile.embedding_skills, job.embedding)
                 + β·sim(profile.embedding_experience, job.embedding)
                 + γ·sim(profile.embedding_education, job.embedding)
```

In practice, after commit `b8632a4` ("remove SentenceTransformer, go full LLM for gap analysis"), the embeddings are largely deprecated. Gap analysis is now pure-LLM. The vectors remain in the schema for potential future use (job recommendations, similar-profile suggestions).

`profiles/services/embeddings.py` and `huggingface_hub` handle vector generation when needed (synchronous, ~10-20s per profile because it downloads and runs the model on CPU).

## 46. JSONB `data_content` Pattern

`UserProfile.data_content` is the single most important schema decision in the project. Instead of normalizing each CV section into its own table (`UserSkill`, `UserExperience`, etc.), the entire CV lives as a single JSONB blob. Reasons:

1. **CV structures vary wildly.** Some CVs have a `Patents` section, some have `Speaking Engagements`, some have neither. Normalizing forces every possible section into a column or a side table; JSONB just stores what's there.

2. **Display-order matters.** Users expect the order they uploaded to be preserved. With normalized tables this requires `display_order` columns. JSONB preserves array order natively.

3. **Migrations are cheap.** Adding a new section type doesn't require a migration. The CV parser just emits a new key under `data_content` and the property accessors expose it.

4. **GIN index** on `data_content` (`jsonb_path_ops`, declared in `Meta.indexes`) gives fast existence queries (`profile.data_content @> '{"skills": [{"name": "Python"}]}'`).

Property accessors on the model:
```python
@property
def skills(self):
    return self.data_content.get('skills', [])

@skills.setter
def skills(self, value):
    self.data_content['skills'] = value
```

Same for `experiences`, `education`, `projects`, `certifications`. So `profile.skills` reads/writes through to the JSONB field transparently.

The downside: full-text search across the JSONB is harder than across columns, and Django ORM querying patterns (`filter(skills__name='Python')`) don't directly work — you have to use JSONB operators. But for SmartCV's needs (read-mostly, structured access), this trade is worth it.

Dynamic sections `data_content` carries beyond the structured fields:
- `github_signals` — Cached snapshot from `github_aggregator.py`.
- `scholar_signals` — Cached snapshot from `scholar_aggregator.py`.
- `kaggle_signals` — Cached snapshot from `kaggle_aggregator.py`.
- `linkedin_snapshot` — Cached snapshot from `linkedin_aggregator.py`.
- `has_seen_welcome` — First-run flag (commit `ac4790f`).
- `profile_strength_cache` — Cached score from `profile_strength.py`.

---

# PART 8 — Services Module Catalog

## 47. `profiles/services/` — 17 modules

### `llm_engine.py` (87 lines)
Documented in PART 6. Three functions: `get_llm`, `get_structured_llm`, `get_llm_client` (legacy).

### `schemas.py` (223 lines)
Documented in PART 6. 25 Pydantic schemas.

### `cv_parser.py` (~1000 lines)
The largest service. Documented in PART 6. Key class: `CVExtractor`. Key function: `parse_cv(file_path)`.

### `llm_validator.py`
Post-parse validation. Takes a parsed CV dict and runs an LLM consistency check using `SemanticValidationResult`. If `makes_sense=False`, returns the `clarification_question` for the UI to surface.

### `embeddings.py`
Vector generation. Calls HuggingFace Inference API (or downloaded model) to produce 384-dim vectors. Used to populate `UserProfile.embedding*` fields. Synchronous.

### `experience_math.py`
Computes years-of-experience from a list of `Experience` entries. Critical algorithm: `compute_yoe(experiences)` parses `start_date`/`end_date` to `(year, month)` tuples and merges overlapping ranges (commit `8170788` "fix(profiles): Month-precision YoE with overlap merging"). Returns total months, then divides by 12 for years.

The merging is non-trivial: if a candidate had a part-time gig (Sep 2022 — Jun 2023) overlapping with a full-time role (Jan 2023 — present), naive sum double-counts the overlap. The merge sorts ranges, then walks through combining overlapping ones into a single span.

### `profile_strength.py`
Computes a 100-point profile strength score. Three components:
- `_score_completeness` (35 pts) — Has full name, contact info, ≥3 skills, ≥1 experience, ≥1 education, etc. Each item is worth a few points.
- `_score_evidence` (30 pts) — Quantifiable bullets (numbers/percentages in highlights). Specific company names. Recent activity.
- `_score_signals` (35 pts) — External signals connected. Each (GitHub, Scholar, Kaggle, LinkedIn) has a freshness component (decay over 30 days).

`_tier(score)` returns a label:
- 0-39: `Untested`
- 40-59: `Developing`
- 60-79: `Strong`
- 80-100: `Outstanding`

`_top_actions(profile, score_breakdown)` returns the 3 most-impactful actions to raise the score, with deep-link URLs (`href` map). For example:
- Score 35 because no GitHub connected → "Connect GitHub" → `/profiles/connect-accounts/`.
- Score 70 because experiences lack metrics → "Add a metric to your most recent role" → `/profiles/manual/#exp-0`.

The full implementation took 11 commits (`4dae74c` through `4686eab`) following an 11-task TDD plan documented in `docs/profile_strength_plan.md` (later removed from public release).

### `interviewer.py`
The chatbot brain. Function `next_turn(history, profile)` returns a `ChatTurnResult`:
- `reply_analysis` — Was the user's reply valid? Did they describe new skills/bullets? What's the quality (0-10)?
- `next_question_generation` — What should we ask next? What skill is being targeted (for loop detection)?

Loop detection: if the last 3 questions all targeted the same `topic_skill`, force a topic switch. Quality threshold: if `quality_score < 3` for two turns in a row, surface a clarification prompt instead of moving forward.

Cache-backed state (commit `ae89394`) — chat history persists in `request.session` plus a `cache.set(f"chatbot:{user_id}", state, timeout=3600)` for crash recovery.

### `outreach_generator.py`
Generates the campaign messages. Function `generate_campaign(profile, job, target_name, target_role)` returns `OutreachCampaignResult` with `linkedin_message`, `cold_email_subject`, `cold_email_body`. The LinkedIn message is constrained to ≤300 chars (LinkedIn's connect-with-note limit).

The prompt explicitly avoids generic phrases:
```
Do NOT open with: "I hope this finds you well", "I came across your profile",
"I am writing to express my interest", "I would love to connect".
Open with something specific to {target_role} or {job.company}.
```

### `outreach_dispatcher.py`
Queue management. Functions:
- `enqueue_action(campaign, target, kind, payload)` — Creates an `OutreachAction` if not already queued for this (campaign, target_handle, kind).
- `dequeue_next_for_user(user)` — Returns the next `'queued'` action across the user's running campaigns. Marks it `'in_flight'`.
- `mark_completed(action, status, error?)` — Updates state, records `completed_at`, increments `attempts` on failure.

### `github_aggregator.py`
Calls GitHub's REST API:
- `/users/{username}` — Profile, repo count, follower count.
- `/users/{username}/repos?per_page=100&sort=updated` — Repos.
- For each repo: stargazer count, primary language.
- `/repos/{owner}/{repo}/commits?author={username}&since={90_days_ago}` — Recent activity.

Caches results in `profile.data_content['github_signals']` with a `fetched_at` timestamp. If cached < 24h old, returns the cached snapshot. The `error` key is set on rate-limit or 404, so the gap analyzer's `_signals` helper can skip the block.

### `linkedin_aggregator.py`
LinkedIn profile snapshot. Limited (LinkedIn doesn't have a public profile API), so this scrapes a logged-out version: title, location, connection count if visible. Often `error=True`.

### `scholar_aggregator.py`
Google Scholar profile parsing. Scrapes the citations page (Scholar has no API). Returns total citations, h-index, i10-index, top 5 publications with title/venue/year/citation count.

### `kaggle_aggregator.py`
Kaggle profile from the Kaggle API (requires user's Kaggle username). Returns tier (Novice/Contributor/Expert/Master/Grandmaster), competitions/datasets/notebooks/discussion counts with medal breakdowns.

### `profile_auditor.py`
LLM-based profile health check. Looks for:
- Vague bullets without metrics ("Worked on backend systems")
- Missing seniority signals (no title with Senior/Lead/Principal but 8+ years experience)
- Inconsistencies (date ranges that don't add up)
- Missing skills implied by the experience

Returns suggestions for the user to action.

### `semantic_validator.py`
LLM check after CV parse. Catches things like:
- "Senior Engineer" title with "0-1 years experience"
- Education year of 1850
- Internal contradiction between summary and bullets

Returns `SemanticValidationResult(makes_sense, clarification_question)`. The clarification surfaces in the review screen.

### `prompt_guards.py`
The `HUMAN_VOICE_RULE` constant. Documented in PART 6.

## 48. `jobs/services/`

### `skill_extractor.py`
Documented in PART 6. LLM + JD anchoring.

### `linkedin_scraper.py`
Legacy single-source scraper. Mostly replaced by `scrapers/linkedin.py` (the dispatcher framework).

### `people_finder.py`
Given a company domain, attempts to find email addresses and contact info. Uses public sources (company "Team" page, LinkedIn). Limited reliability; mostly used as a stepping-stone for outreach campaign building before the v2 extension-driven discovery.

### `scrapers/`
Pluggable scraper framework (commit `80f5a9e`):
- `base.py` — `BaseScraper`, `ScrapeError`.
- `dispatcher.py` — `scrape_job(url)`. Tries each scraper's `can_handle(url)` in order.
- `linkedin.py` — LinkedIn jobs. Returns title, company, description, raw_html, cleaned_url (strips tracking tokens like `eBP`, `trk`, `refId`, `trackingId`), source='linkedin'.
- `greenhouse.py` — `boards.greenhouse.io/{company}/jobs/{id}`.
- `lever.py` — `jobs.lever.co/{company}/{id}`.
- `indeed.py` — Playwright-based (needs JS). Uses `inner_text()` instead of `text_content()` to exclude inline `<style>` content (commit `2ddf64a`).
- `generic.py` — JSON-LD fallback. Searches `<script type="application/ld+json">` for a `JobPosting` schema and extracts from it.

Each scraper implements:
```python
def can_handle(self, url: str) -> bool: ...
def scrape(self, url: str) -> dict:
    return {
        'title': str,
        'company': str,
        'description': str,
        'raw_html': str,
        'cleaned_url': str,
        'source': str,  # 'linkedin' | 'greenhouse' | etc.
    }
```

## 49. `analysis/services/`

### `gap_analyzer.py` (424 lines)
Documented in PART 6. Two-phase gap analysis.

### `learning_path_generator.py`
Given a list of missing skills, generates a personalized learning path. For each skill:
- Importance level (Foundation / Intermediate / Advanced).
- Resources (courses, books, articles) — list of `{title, url, type, duration_estimate}`.
- A specific project idea to demonstrate the skill.

Uses `get_structured_llm(LearningPathResult)`.

### `salary_negotiator.py`
Given (profile, job, current_offer, target_salary), generates a negotiation script:
- Opening positioning statement (lead with concrete value).
- Counter-offer phrasing.
- Walk-away threshold.
- Email follow-up template.

Plain-text generation via `get_llm()`. Output is a Markdown-formatted document the user can copy into the actual conversation.

### `skill_score.py`
Pure-Python utility. `compute_match_score(matched, missing, soft) → float`:
- If `total = matched + missing + soft` is 0, return 0.0.
- Base score: `matched / (matched + missing)`.
- Soft-skill penalty: subtract `0.05 * soft` (capped at `0.20`).

This is the formula used both server-side (after drag-and-drop reclassification) and Alpine-side (live update during drag). See commit `1cccf00`.

## 50. `resumes/services/`

### `resume_generator.py` (375 lines)
Documented in PART 6. Domain-aware prompt + `_ensure_profile_data_preserved` safety net.

### `scoring.py` (188 lines)
Documented in PART 6. `compute_ats_breakdown` (deterministic) + `compute_evidence_confidence` (0-3 stars).

### `cover_letter_generator.py`
LLM-generated cover letter. Plain text via `get_llm(temperature=0.7)`. Includes:
- Anti-hallucination rule.
- HUMAN_VOICE_RULE.
- "No 'I hope this finds you well' opener."
- Specific structure: hook (1 line), why-this-company (2-3 sentences), why-me (3-4 sentences with one quantified achievement), close (1 sentence).

### `pdf_generator.py` (79 lines)
xhtml2pdf wrapper. Function `generate_pdf(html: str, output_path: str)`. Configures `pisa.pisaDocument`, returns `True/False`.

### `pdf_exporter.py` (28 lines)
Higher-level wrapper. Picks the right template based on `template_name`, renders with Django, calls `generate_pdf`. Handles HTTP response with `Content-Type: application/pdf`.

## 51. `core/services/`

### `agent_chat.py` (313 lines)
Documented in PART 5. Job-scoped chat with full system prompt assembly.

### `action_planner.py` (175 lines)
Documented in PART 5. Generates "next action" recommendations.

### `career_stage.py` (233 lines)
Documented in PART 5. Classifies user's career stage.

---


# PART 9 — Templates and Frontend

## 52. `templates/base.html` and the Layout System

`base.html` is the master layout. Every other template extends it. Key blocks:

```html
{% load static %}
<!DOCTYPE html>
<html lang="en" class="h-full">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}SmartCV{% endblock %}</title>
  <link rel="stylesheet" href="{% static 'css/output.css' %}">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..700&family=Fraunces:opsz,wght@9..144,300..700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
  <script defer src="https://cdn.jsdelivr.net/npm/@alpinejs/collapse@3.x.x/dist/cdn.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
</head>
<body class="h-full ...">
  {% block nav %}
    {% include "components/nav.html" %}
  {% endblock %}

  {% if messages %}
    <div class="toast-area" x-data="{ visible: true }" x-show="visible">
      {% for m in messages %}
        <div class="toast toast-{{ m.tags }}">{{ m }}</div>
      {% endfor %}
    </div>
  {% endif %}

  <main class="container mx-auto px-4 py-8">
    {% block content %}{% endblock %}
  </main>

  {% block footer %}
    {% include "components/footer.html" %}
  {% endblock %}
</body>
</html>
```

The `h-full` on both `<html>` and `<body>` (commit `fa3b29d`) ensures the page actually fills the viewport, which the dashboard's vertical layout relies on.

The toast area auto-dismisses success messages after 2s (commit `c935e12`):
```html
<div x-data="{ visible: true }"
     x-init="if ($el.dataset.tags === 'success') setTimeout(() => visible = false, 2000)"
     x-show="visible" x-transition>
  ...
</div>
```

Auth pages (login, register) override `{% block nav %}{% endblock %}` to hide the navigation (commit `d9c0c85`).

## 53. Component Library (`templates/components/`)

13 reusable component templates:

### `badge.html`
```django
{% comment %}
Usage: {% include "components/badge.html" with text="Pending" tone="warning" %}
Tones: brand | accent | success | warning | danger | neutral
{% endcomment %}
<span class="badge badge-{{ tone|default:'neutral' }}">{{ text }}</span>
```

### `button.html`
```django
{% comment %}
Usage: {% include "components/button.html" with text="Generate" type="submit" form_id="resume-form" tone="brand" size="md" %}
Tones: brand | accent | ghost | outline
Sizes: sm | md | lg
{% endcomment %}
<button type="{{ type|default:'button' }}"
        {% if form_id %}form="{{ form_id }}"{% endif %}
        class="btn btn-{{ tone|default:'brand' }} btn-{{ size|default:'md' }}">
  {{ text }}
</button>
```

The `form_id` parameter (commit `133e78e`) lets a button live outside its form's `<form>` tag — useful for sticky bottom-nav action buttons.

### `card.html`
```django
{% comment %}
Usage: {% include "components/card.html" with title="..." body="..." %}
{% endcomment %}
<div class="card">
  {% if title %}<h3 class="card-title">{{ title }}</h3>{% endif %}
  <div class="card-body">{{ body|safe }}</div>
</div>
```

### `input.html`
Standardized text input with label, hint, error display.

### `score.html`
SVG ring progress indicator. Used on the dashboard for profile strength and on gap analysis for match percentage.

### `section_label.html`
Editorial-style section header (small caps, accent color underline).

### `github_signals.html`, `linkedin_signals.html`, `scholar_signals.html`, `kaggle_signals.html`
Signal-card components. Each shows a connect/edit/save state machine (commit `4b66244`):
- **Empty** state: "Connect your GitHub" CTA → opens connect-accounts page.
- **Connected** state: shows username + summary stats. Has "Edit" pencil.
- **Editing** state: input field + Save / Cancel buttons.
- **Refreshing** state: spinner.

### `profile_strength_breakdown.html`
On the insights page. Shows the three component scores (Completeness 35, Evidence 30, Signals 35) with bars and per-section CTAs.

### `profile_strength_ring.html`
On the dashboard. SVG circle with gradient stroke (color depends on tier).

### `onboarding_skip.html`
Sticky bottom-right "Skip onboarding" button. Only renders when `request.session.get('in_onboarding')` is True (commit `1cfd35f`).

## 54. Per-App Templates

### `templates/accounts/`
- `login.html` — Email + password. Hides nav.
- `register.html` — Email + password + confirm. Hides nav.
- `settings.html` — Password change.
- `password_reset.html`, `password_reset_done.html`, `password_reset_confirm.html`, `password_reset_complete.html`, `password_reset_email.html`, `password_reset_subject.txt` — Password reset flow.

### `templates/profiles/`
- `dashboard.html` — Main user dashboard. Profile strength ring (commit `02d25f4`), stage-aware primary CTA, recent activity (recent jobs, gaps, resumes).
- `chatbot.html` — Conversational profile builder. Right-sidebar shows live profile state.
- `connect_accounts.html` — GitHub / LinkedIn / Scholar / Kaggle connect step (commit `e11c7e2`).
- `input_choice.html` — Choose between upload / form / chatbot.
- `manual_form.html` — Build by form. Section-by-section (skills, experience, education, projects, certifications). Animate + autofocus newly-added rows (commit `a167b71`).
- `outreach.html` — Per-user outreach list.
- `outreach_campaign.html` — Per-job campaign builder.
- `outreach_pair.html` — Extension pairing screen (token generation).
- `upload_cv.html` — Drag-and-drop file upload with preview (commit `bfab412`) and clear button (commit `2f83b23`).

### `templates/jobs/`
- `input.html` — URL paste or manual text entry. Per-source tabs (LinkedIn, Indeed, Greenhouse, Lever, Other) — commit `9609e00`.
- `detail.html` — Job detail view. CTAs adapt to profile/resume state.
- `review_job.html` — Confirm extracted data before gap analysis.

### `templates/analysis/`
- `gap_analysis.html` — The flagship page. Three columns: matched skills, partial/soft gaps, missing skills. Drag-and-drop reclassification. Live match % update (commit `1cccf00`). Evidence confidence indicator (commit `da7d12d`).
- `learning_path.html` — Generated learning path display.
- `salary_negotiator.html` — Salary negotiation script display.

### `templates/core/`
- `home.html` — Landing page (Editorial AI direction — commit `a526cab`).
- `welcome.html` — First-run orchestrator (commit `ac4790f`).
- `applications.html` — Kanban board for job pipeline (commit `7730188`).
- `insights.html` — Career insights hub. Top skills, recent gaps, profile strength breakdown.
- `agent_chat.html` — Global agent chat (commit `7730188`). Job-scope pill if `?job=<id>`. Job-scoped seeds (commit `c63e7d7`).
- `design_system.html` — Internal styleguide. Renders every component primitive in every tone/size for visual regression spotting.

### `templates/resumes/`
- `list.html` — User's generated resumes.
- `generate.html` — Template picker + generate button. Thumbnail previews (commit `abd4320`). Big-preview swap (commit `a36c671`).
- `edit.html` — Live editor. Two-column: textareas on left, preview on right. Live ATS score recompute as user types.
- `preview.html` — Full HTML preview.
- `cover_letter_preview.html` — Cover letter preview + edit + regenerate.
- `generate_cover_letter.html` — Cover letter generation form.
- `pdf_template.html`, `pdf_template_compact.html`, `pdf_template_danette.html`, `pdf_template_executive.html`, `pdf_template_minimalist.html`, `pdf_template_zeyad.html` — Six PDF templates.
- `resume_template.html` — Default HTML template (used when not picking a specific style).

### Project-level

- `403_csrf.html` — Custom CSRF failure page (commit `10e3268`).
- `404.html` — Custom 404. Friendly copy + nav back to home/dashboard.
- `500.html` — Custom 500. Apologetic copy + retry button.
- `base.html` — Master layout.

## 55. Alpine.js Patterns

Alpine is loaded via CDN. Used in ~15 templates. Common patterns:

### Drag-and-drop in `gap_analysis.html`

```html
<div x-data="gapBoard({{ matched_skills_json|safe }},
                      {{ missing_skills_json|safe }},
                      {{ soft_skills_json|safe }})"
     x-init="recompute()">

  <div class="column"
       @dragover.prevent
       @drop="onDrop('matched', $event)">
    <template x-for="skill in matched">
      <div draggable="true"
           @dragstart="onDragStart(skill, 'matched', $event)"
           class="chip">
        <span x-text="skill"></span>
      </div>
    </template>
  </div>

  <!-- ... missing column, soft column ... -->

  <div class="match-percent">
    <span x-text="`${matchPct}%`"></span>
  </div>
</div>
```

The Alpine component definition lives in the same template's `<script>` block:
```javascript
function gapBoard(matched, missing, soft) {
  return {
    matched, missing, soft,
    matchPct: 0,
    onDragStart(skill, fromCol, e) {
      e.dataTransfer.setData('text/plain', JSON.stringify({ skill, fromCol }));
    },
    onDrop(toCol, e) {
      const { skill, fromCol } = JSON.parse(e.dataTransfer.getData('text/plain'));
      if (fromCol === toCol) return;
      this[fromCol] = this[fromCol].filter(s => s !== skill);
      this[toCol].push(skill);
      this.recompute();
      this.persist();
    },
    recompute() {
      const total = this.matched.length + this.missing.length + this.soft.length;
      if (!total) { this.matchPct = 0; return; }
      const base = this.matched.length / (this.matched.length + this.missing.length);
      const softPenalty = Math.min(0.05 * this.soft.length, 0.20);
      this.matchPct = Math.round(Math.max(0, base - softPenalty) * 100);
    },
    persist() {
      fetch(`/analysis/{{ job.id }}/skills/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': '{{ csrf_token }}',
        },
        body: JSON.stringify({
          matched_skills: this.matched,
          missing_skills: this.missing,
          soft_skill_gaps: this.soft,
        }),
      });
    },
  };
}
```

The same formula (`base - softPenalty`) runs both client-side (live during drag) and server-side (after persist), keeping them in sync.

### Live ATS score in `edit.html`

```html
<div x-data="resumeEditor({{ initial_content|safe }}, {{ job_skills|safe }})">
  <textarea x-model="content.professional_summary"
            @input="recomputeAts()"></textarea>
  <!-- ... more textareas ... -->
  <div class="ats-score">
    <span x-text="`${atsScore}/100`"></span>
  </div>
</div>
```

The `recomputeAts()` method reimplements the Python `compute_ats_breakdown` logic in JavaScript: counts keyword occurrences, applies stuffing penalty, applies in-context bonus, clamps to 0-100. Server persists on form submit; client gets the immediate feedback.

### Auto-dismiss toasts

```html
<div x-data="{ shown: true }"
     x-init="setTimeout(() => shown = false, 2000)"
     x-show="shown"
     x-transition.opacity.duration.300ms>
  {{ message }}
</div>
```

### Dropzone state in `upload_cv.html`

```html
<div x-data="{ file: null, dragOver: false }"
     @dragover.prevent="dragOver = true"
     @dragleave="dragOver = false"
     @drop.prevent="dragOver = false; file = $event.dataTransfer.files[0]"
     :class="{ 'border-brand-500 bg-brand-50': dragOver }">

  <input type="file" name="cv" accept=".pdf,.docx" hidden
         x-ref="input"
         @change="file = $event.target.files[0]">

  <template x-if="!file">
    <button type="button" @click="$refs.input.click()">Choose file</button>
  </template>

  <template x-if="file">
    <div>
      <span x-text="file.name"></span>
      <button type="button" @click="file = null; $refs.input.value = ''">Clear</button>
    </div>
  </template>
</div>
```

### Welcome screen scroll-to-top

```html
<div x-data x-init="window.scrollTo(0, 0)">...</div>
```

## 56. PDF Templates (6 styles)

Each PDF template is a standalone HTML file with inline CSS (xhtml2pdf can't handle external stylesheets or modern CSS). The six styles:

### `pdf_template.html` — Default
Two-column. Sidebar with contact info + skills + education. Main column with summary + experience + projects.

### `pdf_template_compact.html` — Compact
Single-column, tighter spacing. Smaller headings. Designed for candidates with lots of content who need to fit one page.

### `pdf_template_danette.html` — Danette
Sidebar layout with photo placeholder. Brand color accent on section headings.

### `pdf_template_executive.html` — Executive
Two-column with serif headings (using `@font-face` Inter fallback because xhtml2pdf can't load Google Fonts). Designed for senior roles.

### `pdf_template_minimalist.html` — Minimalist
Single-column, very plain. Sans-serif throughout. No decorations.

### `pdf_template_zeyad.html` — Zeyad
Personal style of the project author. Warm accent color, distinctive section headers.

All six templates take the same context:
```python
{
    'resume': GeneratedResume,
    'profile': UserProfile,
    'content': dict (resume.content),
}
```

The picker on `generate.html` renders thumbnail previews with mock content. Selecting one re-renders the live preview with the chosen style. PDF download triggers `pdf_exporter.export_pdf(resume, template_name)`.

---

# PART 10 — Static Assets and Styling

## 57. Tailwind CSS v4 — CSS-First Configuration

`static/src/input.css` is 140 lines. It's the entire Tailwind config:

```css
@import "tailwindcss";

@theme {
  /* === Brand palette === */
  --color-brand-50:  #eff6ff;
  --color-brand-100: #dbeafe;
  --color-brand-200: #bfdbfe;
  --color-brand-300: #93c5fd;
  --color-brand-400: #60a5fa;
  --color-brand-500: #3b82f6;  /* Primary */
  --color-brand-600: #2563eb;
  --color-brand-700: #1d4ed8;
  --color-brand-800: #1e40af;
  --color-brand-900: #1e3a8a;
  --color-brand-950: #172554;

  /* === Accent palette === */
  --color-accent-50:  #faf5ff;
  --color-accent-100: #f3e8ff;
  --color-accent-200: #e9d5ff;
  --color-accent-300: #d8b4fe;
  --color-accent-400: #c084fc;
  --color-accent-500: #8b5cf6;  /* Secondary */
  --color-accent-600: #7c3aed;
  --color-accent-700: #6d28d9;
  --color-accent-800: #5b21b6;
  --color-accent-900: #4c1d95;
  --color-accent-950: #2e1065;

  /* === Semantic === */
  --color-success-500: #16a34a;
  --color-warning-500: #d97706;
  --color-danger-500:  #dc2626;

  /* === Legacy rn-* tokens === */
  --color-rn-blue:  #2b4a7e;
  --color-rn-navy:  #14254a;
  --color-rn-gold:  #d4a64a;
  --color-rn-cream: #f5e6c8;

  /* === Fonts === */
  --font-sans:    "Inter", system-ui, sans-serif;
  --font-display: "Fraunces", Georgia, serif;
  --font-mono:    "IBM Plex Mono", ui-monospace, monospace;

  /* === Page bg === */
  --color-page-bg: #f8fafc;  /* slate-50ish */
}

@layer base {
  html { font-family: var(--font-sans); }
  body { background: var(--color-page-bg); color: #0f172a; }
  h1, h2, h3 { font-family: var(--font-display); font-weight: 600; }
  code, pre { font-family: var(--font-mono); }
}

@layer components {
  .btn { @apply inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 font-medium transition cursor-pointer; }
  .btn-brand { @apply bg-brand-600 text-white hover:bg-brand-700; }
  .btn-accent { @apply bg-accent-600 text-white hover:bg-accent-700; }
  .btn-ghost { @apply hover:bg-slate-100 text-slate-700; }
  .btn-outline { @apply border border-slate-300 hover:bg-slate-50 text-slate-700; }
  .btn-sm { @apply text-sm px-3 py-1.5; }
  .btn-md { @apply text-base px-4 py-2; }
  .btn-lg { @apply text-lg px-6 py-3; }

  .card { @apply rounded-xl bg-white shadow-sm border border-slate-200 p-6; }
  .card-title { @apply text-xl font-semibold mb-3; }

  .badge { @apply inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium; }
  .badge-brand { @apply bg-brand-50 text-brand-700; }
  .badge-success { @apply bg-success-500/10 text-success-500; }
  .badge-warning { @apply bg-warning-500/10 text-warning-500; }
  .badge-danger { @apply bg-danger-500/10 text-danger-500; }

  .toast { @apply rounded-md px-4 py-3 shadow-md; }
  .toast-success { @apply bg-success-500 text-white; }
  .toast-error   { @apply bg-danger-500 text-white; }
  .toast-warning { @apply bg-warning-500 text-white; }

  /* ... more component classes ... */
}

@content "templates/**/*.html";
@content "static/js/**/*.js";
```

Key features of v4 CSS-first:
- `@theme { ... }` defines design tokens (CSS variables) that Tailwind exposes as utility classes (`bg-brand-500`, `text-brand-600`, etc.).
- `@layer base/components/utilities` are standard CSS layers Tailwind respects.
- `@content "..."` declares which files to scan for utility classes (replaces `tailwind.config.js`'s `content` array).
- No JavaScript config means the entire project Tailwind setup is CSS-native and version-controllable.

## 58. Color Palette

**Brand (Blue)** — Primary brand color. Primary 500 is `#3b82f6` (Tailwind's default blue-500). Used for primary CTAs, links, focus rings.

**Accent (Purple)** — Secondary color. Primary 500 is `#8b5cf6` (violet-500). Used for highlights, accent labels, second-priority CTAs.

**Semantic**:
- `success-500: #16a34a` (green-600) — Success toasts, "Sent" status badges, completed action checkmarks.
- `warning-500: #d97706` (amber-600) — Warning toasts, "Pending" status, missing-skills indicator.
- `danger-500: #dc2626` (red-600) — Error toasts, destructive actions, low ATS score warning.

**Legacy `rn-*` tokens** — Kept alongside the new palette during the phased redesign so old templates don't break mid-flight (commit `e364dc3`):
- `rn-blue: #2b4a7e`
- `rn-navy: #14254a`
- `rn-gold: #d4a64a`
- `rn-cream: #f5e6c8`

**Page background** — `--color-page-bg: #f8fafc` (slate-50). Cool slate so white cards visibly separate (commit `2f59313`).

## 59. Typography — Inter, Fraunces, IBM Plex Mono

**Inter** — UI sans-serif. Variable-width version (`Inter:wght@300..700`). Used for body text, buttons, form fields.

**Fraunces** — Display serif. Variable optical-size + weight (`Fraunces:opsz,wght@9..144,300..700`). Used for h1/h2/h3 headings on landing pages and card titles. Editorial AI direction (commit `a526cab`).

**IBM Plex Mono** — Monospace. Used for code snippets, JSON debug output, embedded link displays.

All three loaded from Google Fonts CDN with `preconnect` for performance.

## 60. Compiled Output (`static/css/output.css`)

The compiled CSS is **3722 lines** as of the latest build (commit `fe176bd`). It includes:
- All utility classes Tailwind detected from the `@content` paths.
- All component classes from `@layer components`.
- Base styles from `@layer base`.
- CSS variables from `@theme`.
- Vendor prefixes auto-applied by Tailwind v4.

The file is committed so dev runs without `npm install`. The downside: every Tailwind change requires a re-build, and the diff is noisy (hundreds of lines change as the utility-class scanner picks up new combinations).

The `dev:css` watch script keeps it in sync during template work:
```bash
npm run dev:css
```

The `build:css --minify` script produces the production version (~250KB minified, gzipped ~40KB).

---

# PART 11 — Testing

## 61. Test Structure (337 tests)

All tests use Django's built-in `TestCase`. Test database is in-memory SQLite (auto-detected via `'test' in sys.argv` in `settings.py`). No fixtures loading via `loaddata` — tests create their own data via factories or direct ORM calls.

### Test files

```
accounts/tests.py              (6 tests)
analysis/tests.py              (34 tests)
core/tests.py                  (67 tests)
jobs/tests.py                  (15 tests)
profiles/tests.py              (133 tests)
profiles/tests_interviewer.py  (24 tests)
profiles/tests_outreach.py     (15 tests)
profiles/tests_prompt_guards.py (8 tests)
resumes/tests.py               (35 tests)
```

Total: **337 tests, all passing** as of commit `fe6ee8a`.

### LLM mocking strategy

Tests that exercise services depending on `get_llm()` or `get_structured_llm()` use `unittest.mock.patch` to replace the LLM call:

```python
@patch('analysis.services.gap_analyzer.get_structured_llm')
def test_gap_analyzer_returns_matched_and_missing(self, mock_llm):
    mock_response = Mock()
    mock_response.matched_skills = ['Python', 'SQL']
    mock_response.critical_missing_skills = ['Spark', 'AWS']
    mock_response.soft_skill_gaps = []
    mock_response.similarity_score = 0.5

    mock_llm.return_value.invoke.return_value = mock_response

    result = compute_gap_analysis(profile, job)
    self.assertEqual(set(result['matched_skills']), {'Python', 'SQL'})
```

This keeps tests deterministic and fast (~0.1s per test instead of 2-3s for a real LLM call).

### HTTP test pattern

For view tests:
```python
def test_dashboard_redirects_unauthenticated_user_to_login(self):
    response = self.client.get('/profiles/dashboard/')
    self.assertRedirects(response, '/accounts/login/?next=/profiles/dashboard/')

def test_dashboard_renders_for_logged_in_user(self):
    user = User.objects.create_user(username='u@x.com', email='u@x.com', password='pwd')
    UserProfile.objects.create(user=user, full_name='Test User')
    self.client.login(email='u@x.com', password='pwd')
    response = self.client.get('/profiles/dashboard/')
    self.assertEqual(response.status_code, 200)
    self.assertContains(response, 'Test User')
```

## 62. Per-App Test Inventories

### `accounts/tests.py` (6 tests)

1. `test_register_creates_user` — POST creates user, logs in.
2. `test_register_password_mismatch` — Mismatched passwords re-render with error message.
3. `test_register_duplicate_email` — Returns "Email already registered" error.
4. `test_login_with_valid_credentials` — Sets session.
5. `test_logout_clears_session` — User is logged out.
6. `test_authenticated_user_redirected_from_login` — `/accounts/login/` redirects to `/dashboard/` if already logged in (commit `f21f398`).

### `analysis/tests.py` (34 tests)

Covers `compute_gap_analysis`:
- LLM mocked to return clean output → matched/missing populated correctly.
- LLM duplication → Phase 2 dedupe works.
- LLM dropped a skill → Phase 2 reconciliation adds it to missing.
- LLM matched a skill under variant spelling → Phase 2 fuzzy matching keeps it matched.
- Empty `job.extracted_skills` → returns zero-score with `analysis_method='no_job_skills'`.
- Empty profile → returns all-missing with `analysis_method='empty_profile'`.
- LLM raises exception → fallback path with `analysis_method='fallback'`.
- Profile with `dict`-shaped skills → `_enrich_skill_payload` flattens correctly.
- Profile with `string`-shaped skills → `_enrich_skill_payload` passes through.

Plus 14 tests for the gap analyzer reconciliation (commit `7424652`):
- All-matched → 100%.
- All-missing → 0%.
- Half-half → 50%.
- Soft-skill-only gaps → don't drop the score below 80%.

Plus 11 tests for separation (Cohen's d analysis):
- Strong CV vs JD → expected score >0.5.
- Weak CV vs JD → expected score <0.3.

### `core/tests.py` (67 tests — the largest)

Covers a wide surface:
- `RequestObservabilityMiddleware`:
  - Records request method/path/status/duration.
  - Swallows exceptions in the metrics layer.
  - Updates per-route statistics.
- Health endpoints:
  - `/healthz/` returns 200 always.
  - `/healthz/deep/` runs `SELECT 1`.
  - `/healthz/deep/` caches result for 15s.
  - `/healthz/metrics` returns JSON snapshot.
- Error handlers:
  - 404 renders `404.html`.
  - 500 renders `500.html`.
  - CSRF failure renders `403_csrf.html` with friendly copy.
- Agent chat:
  - View renders for authenticated user.
  - View redirects to login for unauthenticated.
  - View validates `?job=<uuid>` ownership.
  - API rejects malformed `job_id`.
  - API forwards `Job` to `chat()` when `job_id` valid.
  - `_build_system_prompt(user, job=None)` produces base prompt.
  - `_build_system_prompt(user, job=...)` includes JOB CONTEXT block.
  - `_build_job_context_block(job)` renders gap analysis subsection.
  - `_build_job_context_block(job)` renders snapshot variant.
  - `_build_job_context_block(job)` renders artifacts.
- Welcome orchestrator:
  - First visit shows welcome page.
  - Repeat visit short-circuits to dashboard.
  - Skip POST clears `in_onboarding` from session.
  - Profile with content auto-skips welcome.
- Applications view:
  - Groups jobs by status.
  - Returns total count.
- Insights view:
  - Aggregates top skills across jobs.
  - Returns recent gaps and resumes.
  - Computes evidence confidence.

### `jobs/tests.py` (15 tests)

- `extract_skills` returns expected skills for a JD with explicit skills.
- `extract_skills` drops denylisted soft skills not in JD.
- `extract_skills` drops unanchored hallucinations.
- `extract_skills` returns empty list on LLM failure.
- Scraper dispatcher routes LinkedIn URL to LinkedInScraper.
- Scraper dispatcher routes Greenhouse URL correctly.
- Scraper dispatcher routes Lever URL correctly.
- Scraper dispatcher falls back to GenericJSONLDScraper.
- Scraper raises `ScrapeError` on unknown URL.
- `_bust_job_embedding` nulls all vector fields.
- `_bust_job_embedding` returns `True` if any field was non-null.
- Job creation persists `extracted_skills` as JSON.
- `job_input_view` POST creates Job with skills.
- `review_extracted_job` POST re-extracts skills if description changed.
- `update_job_status_api` updates `application_status`.

### `profiles/tests.py` (133 tests)

The largest test file. Covers:
- CV parser (`parse_cv` + `CVExtractor`):
  - PDF extraction.
  - DOCX extraction.
  - Letter-spaced word repair (8 variants).
  - Header/footer noise removal.
  - Embedded link extraction.
  - Personal info extraction (name, email, phone, location, LinkedIn, GitHub, Kaggle).
  - Section header detection (fuzzy matching).
  - Skill flattening + categorization.
  - `_is_plausible_skill_name` — drops PDF noise (8 tests, commit `1b41469`).
  - Conservative name detection — prefers null over wrong.
  - Conservative location detection — strict comma format.
  - DOCX hyperlink extraction.
- `_sanitize_text` (commit `657eecd`, 186 lines of tests):
  - Repairs `B ACH ELOR`, `IN FORM ATION`, etc.
  - Preserves casing.
  - Removes `Page 1 of 3` style noise.
  - Collapses multi-newlines.
- Profile model:
  - `data_content` JSONB roundtrip.
  - Property accessors (`profile.skills` ↔ `data_content['skills']`).
  - GIN index used for queries.

### `profiles/tests_interviewer.py` (24 tests)

Chatbot turn analysis:
- Valid reply with skill → `skills_to_add` populated.
- Valid reply with experience → `new_experience_bullets` populated.
- Invalid reply → `is_valid=False`, `clarification_prompt` set.
- Quality threshold — 2 consecutive low-quality replies trigger clarification.
- Loop detection — 3 same-skill questions in a row triggers topic switch.
- Cache-backed state recovery.

### `profiles/tests_outreach.py` (15 tests)

- Campaign creation.
- Action queueing — duplicate insertion fails on unique constraint.
- API token auth — invalid token returns 403.
- API discovery push — creates `DiscoveredTarget` rows.
- API discovery dedup — same handle twice = 1 row.
- Queue drain — `dequeue_next_for_user` returns oldest queued action.
- Mark in-flight then completed.
- Weekly cap enforcement.

### `profiles/tests_prompt_guards.py` (8 tests)

- `HUMAN_VOICE_RULE` is non-empty.
- Contains "BANNED WORDS".
- Resume gen prompt includes the rule.
- Cover letter gen prompt includes the rule.
- Banned word in synthetic LLM output triggers fail.
- Specificity rule referenced.
- Opener-variation rule referenced.

### `resumes/tests.py` (35 tests)

- `compute_ats_breakdown`:
  - All skills matched → score 100.
  - No skills matched → score 0.
  - Stuffing penalty applies (>4 occurrences = -5 each).
  - In-context bonus applies (capped at +10).
  - Empty job_skills → 0.0 with safe defaults.
- `calculate_ats_score` — backwards-compat wrapper returns the float.
- `compute_evidence_confidence`:
  - 0 sources → "Untested".
  - 1 source → "Limited".
  - 2 sources → "Moderate".
  - 3 sources → "Strong".
  - GitHub with 0 repos doesn't count.
  - Scholar with 0 publications and 0 citations doesn't count.
- `_description_text_to_list` (commit `e53e71f`, 12 tests):
  - Single line → 1-element list.
  - Multi-line → split.
  - Empty → empty list.
  - Whitespace-only lines dropped.
  - Trailing whitespace stripped.
- `_description_list_to_text` — inverse, joins with `\n`.
- Resume generator (mocked LLM):
  - Domain detection picks correct domain.
  - `_ensure_profile_data_preserved` fills empty experience.
  - `_ensure_profile_data_preserved` patches missing year.
- Resume edit view list↔string round-trip preserves data.

## 63. Coverage (53% overall, 76.9% in core/)

`coverage run manage.py test` produces:

| App | Coverage |
|---|---|
| `core/` | 76.9% |
| `accounts/` | ~62% |
| `analysis/` | ~58% |
| `resumes/` | ~52% |
| `jobs/` | ~47% |
| `profiles/` | ~42% |
| **Overall** | **53%** |

Lower coverage in `profiles/` is mostly `cv_parser.py` (which has many edge-case branches for malformed PDFs) and the aggregator services (which require external API calls to fully exercise).

`.coveragerc`:
```ini
[run]
source = .
omit =
    */migrations/*
    */tests.py
    */tests_*.py
    .venv/*
    benchmarks/*
    static/*
    staticfiles/*
    media/*
    node_modules/*
    setup.py
    manage.py

[report]
exclude_lines =
    pragma: no cover
    raise NotImplementedError
    if __name__ == .__main__.:
    if TYPE_CHECKING:
```

## 64. Test Database Strategy (in-memory SQLite)

```python
if 'test' in sys.argv:
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
```

Why SQLite for tests:
1. **Speed** — No network round-trips. Test suite runs in ~12 seconds.
2. **Isolation** — Each test method gets a fresh transaction (via Django's `TestCase`).
3. **PgBouncer compatibility** — Supabase's connection pooler holds connections that block `CREATE DATABASE test_smartcv` from completing. Using SQLite side-steps this entirely.

Limitations:
- pgvector tests skip (vector fields don't exist on SQLite). Tests that exercise vector logic mock the embedding generation.
- JSONB GIN indexes are ignored on SQLite. The tests don't verify index existence; they verify behavior at the application level.
- `array_length()` and JSONB operators don't work; tests use Python-side filtering instead.

---


# PART 12 — Benchmarks Suite

## 65. Methodology Overview

The `benchmarks/` directory ships a small but real evaluation suite. Every metric has:
- A sample size (10 CVs, 5 JDs, 50 pairs, etc.).
- A re-run command (`python -m benchmarks.run_all`).
- A JSON artifact stored under `benchmarks/results/{date}/`.
- A markdown report (`run_all.md`).

There are no fabricated numbers — every result in the README's benchmarks table comes from a real run. The `docs/benchmarks.md` file documents methodology and "what this does not measure" disclosures.

The suite has six phases. Phases B and D1-D4 run by default. D5 (LLM-judged tailoring) requires `--with-tailoring` because it's expensive (it makes one LLM judging call per generated resume × 4 axes).

```bash
python -m benchmarks.run_all                    # B + D1-D4
python -m benchmarks.run_all --with-tailoring   # also runs D5
```

The orchestrator (`benchmarks/run_all.py`) writes:
- `run_all.json` — Full results merged.
- `run_all.md` — Human-readable summary.
- One JSON file per phase (`ats_eval.json`, `gap_eval.json`, etc.).

## 66. Phase B — Latency

`benchmarks/latency_runner.py`. Tests warm-path latency for 5 endpoints:
- `/healthz/`
- `/healthz/deep/`
- `/profiles/dashboard/`
- `/jobs/<id>/` (a fixture job)
- `/analysis/<job_id>/` (cached gap analysis)

Each endpoint gets 60 requests. Timing uses `time.monotonic()`. Results are split into:
- **Cold** — First 5 samples (TCP/TLS + connection-pool warmup).
- **Warm** — Samples 6-60 (steady state).

Computed statistics:
- p50, p95, p99, max for warm path.
- Mean, std for full series.

**Latest result** (2026-04-27): warm p95 max = **14.77 ms** across the 5 anonymous routes (`/`, `/healthz/`, `/healthz/deep/`, `/accounts/login/`, `/accounts/register/`). Up +2.19 ms vs the 2026-04-25 baseline of 12.58 ms — within machine noise; no request-path code changed between the runs.

The run uses Django's `Client` (no real HTTP), so this measures view + ORM + middleware overhead, not network. Network would add ~30ms RTT to localhost.

## 67. Phase D1 — CV Parser Accuracy

`benchmarks/parser_eval.py`. Evaluates `parse_cv()` against 10 hand-labeled CVs.

For each CV, gold labels include:
- Personal info: name, email, phone, location, LinkedIn, GitHub.
- Section presence: which of {summary, experience, education, skills, certifications, projects} exists.
- Skills set: explicit skills if a skills section exists.

**Metrics** (2026-04-27 — parity with 2026-04-25 baseline; pure-regex parser, no LLM):
- **Personal info accuracy** — Per-field match rate. Aggregate: **0.942** (10 CVs × 6 fields = 60 cells).
- **Section detection F1** — Did the parser find each section that exists, and not invent ones that don't?
- **Skills F1** — For CVs with explicit skills sections only (5 of 10):
  - F1 = **0.429**, Jaccard = 0.303.
- **Skills F1 (all 10 CVs)**:
  - F1 = **0.296**, Jaccard = 0.197.

The gap between the two skills numbers reflects how badly skills extraction fares on CVs without explicit skills sections (the parser falls back to inferring from job descriptions, which is noisy).

The metric was scoped to "in-scope CVs" (those with explicit skills sections) in commit `1b41469` because the original 0.30 F1 looked worse than the system actually performs in its primary use case.

## 68. Phase D2 — Skill Extractor F1

`benchmarks/skill_extractor_eval.py`. Evaluates `extract_skills()` against 5 hand-labeled JDs, run 3 times each (15 trials).

For each JD, gold labels are the set of skills that should be extracted. Per trial:
- Predicted set = `extract_skills(jd_text)`.
- TP = |gold ∩ predicted|.
- FP = |predicted - gold|.
- FN = |gold - predicted|.

**Metrics** (2026-04-27 latest):
- Precision = TP / (TP + FP) = **0.943**
- Recall    = TP / (TP + FN) = **0.892**
- F1        = 2·P·R / (P+R)  = **0.915**
- Hallucination rate = FP / |predicted| = **0.057**

History: hallucination dropped 0.31 → 0.24 in commit `a80de9e` (soft-skill denylist + JD anchoring via substring + trimmed-suffix + all-words-present). The 0.24 → **0.057** lift between the 2026-04-25 baseline and the 2026-04-27 measurement came from `2b10a7b` — JD fixture label completeness fix. The extractor was correctly identifying tools mentioned in JD bodies (Tailwind, Bootstrap, Axios, Figma, REST API, etc.) that the gold lists had failed to enumerate; treating valid extractions as false positives was the dominant source of "hallucinations." No code change to the extractor itself between the two snapshots.

Determinism: 3 trials per JD have a small variance (Cohen's κ = 0.78 on per-skill predictions). Acceptable.

## 69. Phase D3 — Gap Analyzer Coverage and Separation

`benchmarks/gap_eval.py`. Evaluates `compute_gap_analysis()` against 50 hand-labeled (CV, JD) pairs.

The 50 pairs are 10 CVs × 5 JDs. Each pair is labeled with one of:
- `strong` — CV strongly matches JD.
- `partial` — Some overlap, some gaps.
- `weak` — Mostly mismatched.

**Coverage metric**: For each pair, did the gap analyzer's output account for every JD skill? (i.e., does every JD skill appear in matched + missing + partial?)

- **Result: 0.997** (47 of 50 pairs at 100% coverage; 0.999 on the 2026-04-25 baseline — within reconciliation noise).
- The non-100% pairs are JDs with 50+ skills where the LLM dropped a few obscure ones below the difflib reconciliation threshold.

**Separation metric** (Cohen's d): Are similarity scores statistically distinguishable across the three label classes?

- Strong pairs: mean similarity_score = **0.465**, std = 0.13.
- Partial pairs: mean similarity_score = **0.383**, std = 0.10.
- Weak pairs: mean similarity_score = **0.141**, std = 0.08.
- Cohen's d (strong vs weak) = **1.685** (large effect, easily distinguishable).
- Cohen's d (strong vs partial) ≈ 0.51 (moderate — there's overlap, which is expected).

History: separation moved from 1.594 → **1.685** between the 2026-04-25 baseline and 2026-04-27 via commit `787f4fb` — added an explicit `SIMILARITY SCORE RUBRIC` to the gap-analyzer prompt so the LLM anchors `similarity_score` to the matched / missing ratio it itself produces (≥80% matched → 0.55–0.85; 50–80% → 0.35–0.65; <50% → 0.05–0.30).

A Cohen's d of 1.685 means the score is reliably useful as a routing signal: high-score → "Generate Resume," low-score → "Learning Path".

## 70. Phase D4 — ATS Scoring Determinism

`benchmarks/ats_eval.py`. Evaluates `compute_ats_breakdown()` for:
1. **Determinism**: Re-run on the same input 10 times. Standard deviation should be 0.
2. **Separation**: Matched-resume vs mismatched-resume scores should be clearly distinguishable.
3. **Stuffing penalty**: A resume with the same keyword 6× should score lower than one with 1×.

**Results**:
- **Determinism**: σ = 0 across 10 runs × 3 fixtures. Confirmed deterministic.
- **Separation**: Matched scores avg **100.0**; mismatched avg **11.0**. Cohen's d = **6.27** (very large effect).
- **Stuffing penalty**: A 6×-stuffed resume scores ~25 points below the equivalent unstuffed.

The σ = 0 result is important: the algorithm has no randomness, no rounding non-determinism, no LLM in the loop. Same input → same output, every time. This is the reverse of most "ATS scoring" tools which use LLM judges with high variance.

## 71. Phase D5 — LLM-Judged Resume Tailoring

`benchmarks/tailoring_eval.py`. Evaluates `generate_resume_content()` for 10 strong (CV, JD) pairs.

For each pair:
1. Run resume generation.
2. Score the result on 4 axes via `llm_judge.py` (which uses a different LLM call to judge):
   - **Factuality** — Does the resume invent claims?
   - **Relevance** — Does it emphasize JD-relevant content?
   - **ATS fit** — Does it surface JD keywords appropriately?
   - **Human voice** — Does it avoid LLM-isms?
3. Run a programmatic entity-grounding check: do the generated companies/schools/projects appear verbatim in the source CV?

**LLM-judged scores** (1-10 scale, 2026-04-27 average across 10 strong pairs):
- Factuality: **6.3**
- Relevance: **6.9**
- ATS fit: **6.8**
- Human voice: **4.7**

**Programmatic entity grounding**: **0.875** of generated companies/schools appear verbatim in source. The 12.5% that don't are typically slight rewordings ("Cornell University" → "Cornell") rather than fabrications.

**Banned-voice hits per resume**: **0.3** (LLM available). Counts occurrences of phrases banned by `profiles.services.prompt_guards.HUMAN_VOICE_RULE`.

Headline movement vs the 2026-04-25 baseline (factuality 8.0 / relevance 6.8 / ats_fit 5.6 / human_voice 5.6):
- **ats_fit +1.2** — driven by `d7032fb` (evidence-grounded resume gen with full GitHub / Scholar / Kaggle signal blocks, JD body cap raised 1000 → 4000 chars, gap-analysis breakdown surfaced).
- **factuality −1.7** — reads worse than it is: baseline ran on n=5 with std=1.265 (SE ≈ 0.566) while 2026-04-27 ran on n=10 with std=3.58 (SE ≈ 1.13). The −1.7 delta is ~1.5 SE on the new run. One pair (`cv_frontend_jr_react × jd_junior_web_dev`) hit Groq `tool_use_failed` and fell through to the offline fallback (which the judge correctly scored as un-tailored), pulling the mean down. Entity grounding stayed flat at 0.875 — the prompt isn't fabricating, the judge is just calling more hedged-but-true content "Yes, with caveats" because the prompt now lets the generator say more.
- **human_voice −0.9** — expected cost of the stricter neutral-voice rule and YoE guardrail in `fe5a3ea` (no third-person name references, no fabricated tenure phrases). The absolute score dips because the prompt now forbids phrasings the prior version permitted.

Full deltas + commit attribution in [`benchmarks/CHANGELOG.md`](../benchmarks/CHANGELOG.md).

## 72. Fixtures (10 CVs × 5 JDs = 50 pairs)

**`benchmarks/fixtures/jobs/`** — 5 hand-crafted JDs:
- `jd_backend_python_node.json` — Senior backend, Python + Node, distributed systems focus.
- `jd_devops_aws_k8s.json` — DevOps engineer, AWS + Kubernetes.
- `jd_flutter_mobile.json` — Flutter mobile developer.
- `jd_junior_web_dev.json` — Junior full-stack web dev.
- `jd_senior_frontend_react.json` — Senior frontend, React + TypeScript.

Each JD JSON has:
```json
{
  "title": "...",
  "company": "...",
  "description": "...full text...",
  "expected_skills": ["Python", "Node.js", ...]
}
```

**`benchmarks/fixtures/labels/`** — 10 CV gold-label files:
- `cv_backend_jr_rust.json` — Junior backend, Rust focus (mismatch with most JDs).
- `cv_devops_jr.json` — Junior DevOps.
- `cv_flutter_intern.json` — Flutter intern (matches `jd_flutter_mobile`).
- `cv_frontend_diploma_react.json` — Diploma-level frontend, React.
- `cv_frontend_entry_no_role.json` — Entry-level, no specific role.
- `cv_frontend_jquery_legacy.json` — Legacy jQuery developer (mismatch with React).
- `cv_frontend_jr_react.json` — Junior frontend, React (matches `jd_senior_frontend_react` partially).
- `cv_frontend_mid_react.json` — Mid-level frontend, React (matches strongly).
- `cv_frontend_senior_react_vue.json` — Senior frontend, React + Vue.
- `cv_frontend_senior_react_vue_v2.json` — V2 of the above.

Each label JSON has:
```json
{
  "personal_info": {"name": "...", "email": "...", ...},
  "sections_present": ["summary", "experience", ...],
  "skills": ["React", "TypeScript", ...]
}
```

**`benchmarks/fixtures/manifest.json`** — Maps each CV to each JD with an expected match strength:
```json
{
  "pairs": [
    {"cv": "cv_frontend_senior_react_vue", "jd": "jd_senior_frontend_react", "strength": "strong"},
    {"cv": "cv_frontend_jquery_legacy", "jd": "jd_senior_frontend_react", "strength": "weak"},
    ...
  ]
}
```

Total 50 pairs, distributed roughly evenly across the three strength classes.

## 73. Latest Results (2026-04-27)

`benchmarks/results/2026-04-27/` is the latest snapshot. Headlines:

| Phase | Metric | 2026-04-25 baseline | 2026-04-27 latest | Δ |
|---|---|---|---|---|
| B  | Warm p95 max (ms)              | 12.58 | **14.77** | +2.19 (within machine noise) |
| D1 | Parser personal-info accuracy  | 0.942 | **0.942** | parity |
| D1 | Parser skills F1 (n=5 in-scope)| 0.429 | **0.429** | parity |
| D2 | Skill extractor F1             | 0.806 | **0.915** | +0.110 |
| D2 | Skill extractor precision      | 0.761 | **0.943** | +0.181 |
| D2 | Skill extractor hallucination  | 0.239 | **0.057** | −0.182 |
| D3 | Gap analyzer Cohen's d         | 1.594 | **1.685** | +0.091 |
| D3 | Gap analyzer coverage          | 0.999 | **0.997** | −0.002 |
| D4 | ATS deterministic σ            | 0     | **0**     | parity |
| D4 | ATS Cohen's d                  | 6.267 | **6.267** | parity |
| D5 | factuality (1–10)              | 8.0 (n=5) | **6.3 (n=10)** | −1.7 (within ~1.5 SE; see CHANGELOG) |
| D5 | relevance                      | 6.8   | **6.9**   | +0.1 |
| D5 | ats_fit                        | 5.6   | **6.8**   | **+1.2** ← headline |
| D5 | human_voice                    | 5.6   | **4.7**   | −0.9 (stricter rule) |
| D5 | entity_grounding               | 0.875 | **0.875** | parity |
| D5 | banned-voice hits per resume   | 0.2   | **0.3**   | within noise |

Driver attribution:
- **D2** — `2b10a7b` (JD fixture label completeness fix; the extractor was correctly identifying tools mentioned in JD bodies that the gold lists had failed to enumerate).
- **D3** — `787f4fb` (explicit SIMILARITY SCORE RUBRIC in the gap-analyzer prompt).
- **D5** — `d7032fb` evidence-grounded resume gen + `fe5a3ea` neutral-voice + YoE-guardrail prompts.

Open caveat: one D5 pair (`cv_frontend_jr_react × jd_junior_web_dev`) hit Groq `tool_use_failed` with prose-formatted output and fell through to the offline fallback. n=9 LLM-available + n=1 fallback is what shipped. Worth a separate repro.

Full per-phase ledger, trade-off discussion, and run-by-run history in
[`benchmarks/CHANGELOG.md`](../benchmarks/CHANGELOG.md). Per-phase
JSON in [`benchmarks/results/2026-04-27/`](../benchmarks/results/2026-04-27/).

---

# PART 13 — Chrome Extension (`extension-outreach/`)

## 74. Manifest V3

```json
{
  "manifest_version": 3,
  "name": "SmartCV Outreach",
  "description": "Sends queued LinkedIn connection requests for your SmartCV outreach campaigns from inside your own browser tab, and auto-discovers targets from the logged-in LinkedIn job page.",
  "version": "0.2.0",
  "permissions": ["storage", "alarms", "scripting", "tabs"],
  "host_permissions": [
    "https://www.linkedin.com/*",
    "http://127.0.0.1/*",
    "http://localhost/*"
  ],
  "background": {
    "service_worker": "background.js",
    "type": "module"
  },
  "action": {
    "default_popup": "popup.html",
    "default_title": "SmartCV Outreach"
  },
  "options_page": "options.html",
  "content_scripts": [
    {
      "matches": ["https://www.linkedin.com/jobs/view/*"],
      "js": ["content_discover.js"],
      "run_at": "document_idle"
    }
  ]
}
```

Permissions:
- `storage` — Saves user's SmartCV host URL + auth token in `chrome.storage.local`.
- `alarms` — Periodic poll of the SmartCV API.
- `scripting` — Inject `content_linkedin.js` into target profile pages on demand.
- `tabs` — Open/refocus tabs.

Host permissions:
- `linkedin.com/*` — Read DOM, click buttons.
- `127.0.0.1/*` and `localhost/*` — Talk to dev SmartCV server.

The content script `content_discover.js` runs on every `linkedin.com/jobs/view/*` page automatically (`run_at: document_idle`). The `content_linkedin.js` is injected on-demand when an outreach action targets a specific profile.

## 75. Background Service Worker (`background.js`)

The service worker is the orchestrator. Pseudocode:

```javascript
chrome.alarms.create('poll', { periodInMinutes: 1.5 + (Math.random() * 0.7) });
// 90s ± 20s jitter

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== 'poll') return;

  const { paused_until } = await chrome.storage.local.get('paused_until');
  if (paused_until && Date.now() < paused_until) return;

  const config = await chrome.storage.local.get(['host', 'token']);
  if (!config.host || !config.token) return;

  // Poll for next action
  const res = await fetch(`${config.host}/profiles/api/outreach/next`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${config.token}` },
  });
  if (res.status === 204) return; // nothing queued
  if (res.status === 429) {
    // weekly cap hit
    await chrome.storage.local.set({
      paused_until: Date.now() + 24 * 60 * 60 * 1000,
    });
    return;
  }

  const action = await res.json();
  // { id, target_handle, target_name, kind, payload }

  // Open or refocus LinkedIn tab on target's profile
  const profileUrl = `https://www.linkedin.com/in/${action.target_handle}/`;
  const [tab] = await chrome.tabs.query({ url: profileUrl });
  let targetTab;
  if (tab) {
    targetTab = tab;
    await chrome.tabs.update(tab.id, { active: true });
  } else {
    targetTab = await chrome.tabs.create({ url: profileUrl, active: true });
    // wait for the page to load
    await new Promise(r => {
      chrome.tabs.onUpdated.addListener(function listener(tabId, info) {
        if (tabId === targetTab.id && info.status === 'complete') {
          chrome.tabs.onUpdated.removeListener(listener);
          r();
        }
      });
    });
  }

  // Inject content_linkedin.js to perform the action
  const result = await chrome.scripting.executeScript({
    target: { tabId: targetTab.id },
    files: ['content_linkedin.js'],
  });

  // Report result back
  await fetch(`${config.host}/profiles/api/outreach/result`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${config.token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ action_id: action.id, ...result[0].result }),
  });
});
```

The 90s ± 20s polling interval is deliberate. Faster would look bot-like; slower would feel sluggish. The jitter prevents synchronized behavior across users.

The `paused_until` mechanism handles weekly caps. When the server returns 429 with a "weekly cap hit" indicator, the worker sleeps for 24 hours.

The service worker also handles discovery push from the discover content script (commit `136c651` "fix(outreach-ext): Route discovery push through SW to bypass Chrome PNA"):

```javascript
chrome.runtime.onMessage.addListener(async (msg, sender) => {
  if (msg.type !== 'DISCOVER_PUSH') return;
  const config = await chrome.storage.local.get(['host', 'token']);
  await fetch(`${config.host}/profiles/api/outreach/discover`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${config.token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(msg.payload),
  });
});
```

Routing through the service worker is necessary because Chrome's Private Network Access policy blocks content scripts from making cross-origin requests to localhost.

## 76. Content Scripts

### `content_discover.js`

Runs on `linkedin.com/jobs/view/*` pages. Looks for:
- The "Meet the hiring team" section — extracts profile links + names + roles.
- The "People you can reach out to" section.
- The "Company employees" section (for some companies).

For each found target:
```javascript
const targets = [];
document.querySelectorAll('.hirer-card a[href*="/in/"]').forEach(a => {
  const handle = a.href.match(/\/in\/([^\/]+)/)[1];
  const card = a.closest('.hirer-card');
  const name = card.querySelector('.name')?.innerText.trim();
  const role = card.querySelector('.role')?.innerText.trim();
  targets.push({ handle, name, role, source: 'hiring_team' });
});
// ... similar for other sections ...

const job_id = inferJobIdFromUrl(); // SmartCV stores LinkedIn job ID → job_id mapping

chrome.runtime.sendMessage({
  type: 'DISCOVER_PUSH',
  payload: { job_id, targets },
});
```

The script runs once per page load. The deduplication is server-side (unique constraint on `(user, job, handle)`).

### `content_linkedin.js`

Runs on individual profile pages when invoked by the service worker. It performs the connect-with-note action:

```javascript
async function performConnectWithNote(payload) {
  // 1. Click the "Connect" button
  const connectBtn = await waitForSelector('button[aria-label*="Connect"]', 5000);
  if (!connectBtn) {
    return { status: 'failed', error: 'selector_drift', detail: 'No Connect button' };
  }
  connectBtn.click();
  await sleep(jitter(500, 1500));

  // 2. In the modal, click "Add a note"
  const noteBtn = await waitForSelector('button[aria-label*="Add a note"]', 3000);
  if (!noteBtn) {
    return { status: 'failed', error: 'selector_drift', detail: 'No Add-a-note button' };
  }
  noteBtn.click();
  await sleep(jitter(400, 1200));

  // 3. Type the note in the textarea
  const textarea = await waitForSelector('textarea[name="message"]', 3000);
  if (!textarea) {
    return { status: 'failed', error: 'selector_drift', detail: 'No textarea' };
  }
  await typeWithJitter(textarea, payload, 40, 120);
  await sleep(jitter(500, 1500));

  // 4. Click Send
  const sendBtn = await waitForSelector('button[aria-label*="Send"]', 3000);
  if (!sendBtn) {
    return { status: 'failed', error: 'selector_drift', detail: 'No Send button' };
  }
  sendBtn.click();
  await sleep(jitter(800, 2400));

  // 5. Verify success state (modal closes, "Pending" indicator appears)
  const pendingIndicator = await waitForSelector('button[aria-label*="Pending"]', 5000);
  if (pendingIndicator) {
    return { status: 'sent' };
  }
  return { status: 'failed', error: 'no_confirmation' };
}
```

Helper functions:
- `waitForSelector(sel, timeoutMs)` — Returns the element or null after timeout. Uses `MutationObserver` for efficiency.
- `sleep(ms)` — `new Promise(r => setTimeout(r, ms))`.
- `jitter(min, max)` — Random integer in [min, max].
- `typeWithJitter(el, text, minChunk, maxChunk)` — Splits text into chunks, sets `el.value`, dispatches `input` events with jittered delays. Mimics human typing.

Error reporting: every `selector_drift` result includes detail so the SmartCV status panel can show "LinkedIn DOM changed; the script needs an update."

## 77. Popup and Options

### `popup.html` + `popup.js`

The popup (clicked via the extension icon) shows:
- Connection status: "Connected to SmartCV at http://localhost:8000" or "Not connected — open Options".
- Recent action count: "Sent 4 connect requests today, 12 this week, paused 6 hours."
- Quick links: "Open SmartCV outreach page," "Pause for 24h," "Resume."

### `options.html` + `options.js`

The options page (right-click → Options) configures:
- **SmartCV host** — `http://127.0.0.1:8000` for dev; the production URL in production.
- **Auth token** — Pasted from `/profiles/extension/pair/`. Saved to `chrome.storage.local`.

```javascript
document.getElementById('save').addEventListener('click', async () => {
  const host = document.getElementById('host').value.trim();
  const token = document.getElementById('token').value.trim();
  await chrome.storage.local.set({ host, token });
  document.getElementById('status').innerText = 'Saved.';
});
```

Token rotation: if the user clicks "Rotate token" in SmartCV's settings page, they need to update the extension. The extension will get 401s with the old token and prompt the user to re-pair.

## 78. API Integration with Backend

The extension hits four endpoints on the backend (defined in `profiles/views_outreach_api.py`):

1. **`POST /profiles/api/outreach/next`**
   - Headers: `Authorization: Bearer <outreach_token>`
   - Returns: `{id, target_handle, target_name, target_role, kind, payload}` or 204 No Content.
   - Side effect: marks the action `'in_flight'`.

2. **`POST /profiles/api/outreach/result`**
   - Body: `{action_id, status, error?, evidence?}`
   - Updates `OutreachAction` row.

3. **`POST /profiles/api/outreach/discover`**
   - Body: `{job_id, targets: [{handle, name, role, source}]}`
   - Creates `DiscoveredTarget` rows. Unique constraint dedupes.

4. **`POST /profiles/api/outreach/check-cap`**
   - Returns `{paused: bool, reason?}`.
   - Used by the popup for status display.

All endpoints validate the token against `User.outreach_token`. Invalid token → 403 with `{error: "invalid_token"}`.

The extension's MV3 service worker can't be packaged for the Chrome Web Store (Web Store policy rejects extensions that automate LinkedIn UI flows). The README documents this as a sideload-only extension intended for personal use.

---

# PART 14 — Documentation

## 79. README.md

The repo's public face. Reproduced verbatim earlier. Highlights:
- Badges: Python 3.12+, Django 5.2, MIT license, 337 tests passing, 53% coverage.
- Tagline: "AI-powered career assistant for job seekers. Upload a CV, paste a job description, and get a gap analysis, an ATS-scored tailored resume, and an outreach campaign plan — backed by an LLM pipeline that reuses the same services everywhere instead of one-off scripts."
- Highlights section: 5 bullets.
- Screenshots section: 3 screenshots (resume editor, gap analysis, outreach campaign builder).
- Benchmarks table: 9 metrics with N values.
- Quick Start: 4 commands.
- Architecture: ASCII diagram of the apps.
- Documentation links to `docs/`.
- License: MIT.

The README final pass (commit `12953aa`) added the badges and license section. Earlier versions were less polished.

## 80. CLAUDE.md

Reproduced verbatim earlier. Provides architecture and command guidance for Claude Code working in the repo. Covers:
- Project overview (1 paragraph).
- Commands (run, migrate, test, shell).
- Architecture: apps, key data flow, LLM integration, profile data storage, gap analysis reconciliation, resume editing list/string conversion, frontend toolchain.
- Database notes (Supabase PgBouncer, pgvector).
- Environment variables.

## 81. `docs/` Folder Contents

### `docs/benchmarks.md`

The "real evaluation methodology" companion to the README's benchmarks table. Sections:
- Suite layout (what's in `benchmarks/`).
- Phase descriptions (B, D1-D5).
- Per-phase methodology including formulas.
- Fixture descriptions.
- Latest results table.
- "What this does not measure" disclosure (e.g., "this doesn't test against actual ATS systems").

### `docs/gap_analysis_system.md`

Technical architecture of the gap analyzer. Sections:
- System overview.
- Data models (`GapAnalysis`, `Job.extracted_skills`, `UserProfile.data_content`).
- Pipeline phases:
  - Phase 0: skill extraction.
  - Phase 1: candidate context build.
  - Phase 2: LLM categorization.
  - Phase 3: programmatic reconciliation.
  - Phase 4: persist + UI.
- Embedding strategy (multi-vector, currently deprecated for matching).
- LLM integration details.
- Fallback path.
- UI/UX (drag-and-drop reclassification).

### `docs/implementation_plan.md`

7-phase roadmap. Most phases are now complete; remaining items include:
- Phase 6 — re-introduce `django-q2` for background embedding pre-computation.
- Phase 7 — RecommendedJob auto-generation pipeline.

## 82. QA Test Plans

### `docs/qa/manual-test-plan.md`

End-to-end manual test scenarios covering:
- Registration → CV upload → Profile review.
- Job URL paste → Skill extraction → Gap analysis.
- Resume generation → Edit → PDF download.
- Cover letter generation.
- Outreach campaign → Extension queue drain.

Each scenario has expected outcomes and "what went wrong" recovery steps.

### `docs/qa/outreach-automation-test-plan.md`

Specific to the Chrome extension. Sections:
- Pairing flow.
- LinkedIn DOM compatibility.
- Weekly cap behavior.
- Error reporting (selector_drift cases).
- Discovery push.

---

# PART 15 — Build, Setup, and Deployment

## 83. Local Development Setup

```bash
# Clone
git clone https://github.com/ZeyadElshenawy/SmartCV.git
cd SmartCV

# Python
python -m venv .venv
source .venv/bin/activate           # macOS/Linux
.venv\Scripts\activate              # Windows

pip install -r requirements.txt

# npm (Tailwind only)
npm install

# Configure
cp .env.example .env
# Edit .env:
#   DATABASE_URL=postgresql://...
#   GROQ_API_KEY=gsk_...
#   SECRET_KEY=<generate with python -c "import secrets; print(secrets.token_urlsafe(50))">

# Database
python manage.py migrate

# Frontend
npm run build:css

# Run
python manage.py runserver
```

## 84. Tailwind Build Workflow

During template work:
```bash
npm run dev:css
```

Watches `static/src/input.css` + the `@content` paths. Re-emits `static/css/output.css` whenever a template changes. Useful when Alpine and Tailwind classes are being added together.

For production:
```bash
npm run build:css  # --minify
```

Produces a smaller `output.css` (~250KB unminified, ~40KB gzipped).

The compiled `output.css` is committed so the dev server works without npm. After significant template changes, rebuild and commit.

## 85. Database Migrations

```bash
python manage.py makemigrations           # generate
python manage.py migrate                   # apply
python manage.py migrate --plan            # preview
python manage.py showmigrations            # status
```

The migration history is fairly complex (especially in `profiles/`). Squashing wasn't done because:
1. Each migration is fast (no large data movement).
2. Squashing would lose the historical context (data migration `0006_migrate_data.py` is informative).

For first-time setup against a fresh Supabase database, run:
```bash
python manage.py migrate
python manage.py createsuperuser
```

The `pgvector` extension is installed automatically by `0004_setup_vector.py` (uses `CREATE EXTENSION IF NOT EXISTS vector`).

## 86. Production Considerations

The repo doesn't have a production deployment target configured, but the pieces are in place:

- **Static files**: `python manage.py collectstatic` produces `staticfiles/`. WhiteNoise serves them with `CompressedManifestStaticFilesStorage`.
- **Database**: Supabase Pgbouncer connection (already configured).
- **Email**: `EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'` — replace with SMTP backend in production.
- **Secrets**: `SECRET_KEY` must be set via env. `DEBUG=False`.
- **Allowed hosts**: Update `ALLOWED_HOSTS` env var to include the production domain.
- **CSRF trusted origins**: Currently not set. Production needs `CSRF_TRUSTED_ORIGINS=https://smartcv.example.com`.
- **CORS**: Currently set to `localhost:3000`. Production may need adjustment if there's a separate frontend.

A typical deployment target would be Render, Railway, Fly.io, or Heroku. Required:
- Procfile or equivalent: `web: python manage.py migrate && gunicorn smartcv.wsgi`.
- Build command: `pip install -r requirements.txt && npm install && npm run build:css && python manage.py collectstatic --noinput`.
- Env vars: `DATABASE_URL, GROQ_API_KEY, SECRET_KEY, DEBUG=False, ALLOWED_HOSTS=...`.

Performance notes for production:
- LLM calls are synchronous (~2-3s each). Each user request that triggers gap analysis or resume generation will hold a worker for that duration. Plan worker count accordingly (e.g., 4 workers for ~120 concurrent users at typical pacing).
- Embedding generation is also synchronous (~10-20s). The plan to introduce django-q2 (Phase 6) addresses this.

## 87. Known Limitations

1. **No CI/CD** — All testing manual. `python manage.py test` must be run before commits, but there's no enforcement.
2. **No staging environment** — All testing happens locally or against the production Supabase.
3. **Synchronous LLM calls** — A user who triggers gap analysis blocks a worker for ~3 seconds. Under load, this is the primary bottleneck.
4. **Embedding generation is heavy** — First profile save takes ~10-20s. Subsequent saves are faster (vectors persist).
5. **Chrome extension is sideload-only** — Web Store rejects LinkedIn-automating extensions. This limits distribution to technical users.
6. **LinkedIn DOM brittleness** — The content script asserts selectors and reports `selector_drift`. Periodic updates required.
7. **No type checking** — `mypy` isn't configured. Some files have type hints; most don't.
8. **No linter / formatter** — `ruff`, `black`, or `flake8` aren't configured. Style is hand-maintained.

---


# PART 16 — Complete Git History

## 88. Repository Statistics

- **Total commits on main**: 168 (matches `git log --all --oneline | wc -l`)
- **Active development window**: 2026-03-10 → 2026-04-25 (~6.5 weeks)
- **Branches**: `main` (current), `master` (ancestor of main, kept for safety)
- **Tags**: None
- **Remote**: `https://github.com/ZeyadElshenawy/SmartCV.git`
- **Average commits per day**: ~3.6

### Contributor breakdown

```
   155	GarGantua <zeyadelshenawy1@gmail.com>
    13	Zeyad Ahmed Elshenawy <115832263+ZeyadElshenawy@users.noreply.github.com>
```

Both are the same person — `GarGantua` is the local git config, `Zeyad Ahmed Elshenawy` is the GitHub-noreply identity (used when commits were made through GitHub's web UI, e.g., merge commits, PR squashes).

### Commit cadence by week

- **Week of 2026-03-10**: 6 commits — Phase 4 UI redesign (initial state).
- **Week of 2026-04-04**: 13 commits — LangChain + Groq migration; gap analysis optimization.
- **Week of 2026-04-07**: 4 commits — django-q removal.
- **Week of 2026-04-12**: 14 commits — Job scrapers, resume quality.
- **Week of 2026-04-14**: 53 commits — Massive design system overhaul + feature work.
- **Week of 2026-04-15**: 30 commits — Profile strength scoring (TDD), job-aware agent.
- **Week of 2026-04-16-17**: 12 commits — UX polish, password reset, error pages.
- **Week of 2026-04-18-19**: 11 commits — Onboarding step, drag-and-drop live updates.
- **Week of 2026-04-20-21**: 14 commits — django-debug-toolbar, outreach v1 + v2.
- **Week of 2026-04-25**: 11 commits — Pre-launch hardening, public release prep.

The biggest single day was **2026-04-14** (the design system overhaul day) with ~30 commits in a single push.

## 89. All 168 Commits, Annotated

Below is the complete commit history in reverse chronological order (newest first). Each entry shows SHA, date, author, subject, and file-change stats.

---

### `fe6ee8a` — 2026-04-25
**Author**: GarGantua  
**Subject**: docs(readme): refresh test count and gap-coverage to match latest artifacts  
**Stats**: 1 file changed, 3 insertions(+), 3 deletions(-)  

### `25082a0` — 2026-04-25
**Author**: Zeyad Ahmed Elshenawy  
**Subject**: human-voice: add SPECIFICITY + opener-variation rules to prompt_guards (#4)  
**Stats**: 2 files changed, 84 insertions(+), 4 deletions(-)  

### `a80de9e` — 2026-04-25
**Author**: Zeyad Ahmed Elshenawy  
**Subject**: skill-extractor: cut hallucination 0.31 -> 0.24 via prompt + JD anchoring (#3)  
**Stats**: 5 files changed, 796 insertions(+), 160 deletions(-)  

### `1b41469` — 2026-04-25
**Author**: Zeyad Ahmed Elshenawy  
**Subject**: parser: drop PDF-noise skills + scope skills-F1 metric to in-scope CVs (#2)  
**Stats**: 7 files changed, 368 insertions(+), 243 deletions(-)  

### `f36c179` — 2026-04-25
**Author**: Zeyad Ahmed Elshenawy  
**Subject**: Update README to include screenshots  
**Stats**: 1 file changed, 8 insertions(+), 8 deletions(-)  

### `43b97b3` — 2026-04-25
**Author**: Zeyad Ahmed Elshenawy  
**Subject**: adding screenshots of the website  
**Stats**: 3 files changed, 0 insertions(+), 0 deletions(-)  

### `df96723` — 2026-04-25
**Author**: Zeyad Ahmed Elshenawy  
**Subject**: Delete docs/images/outreach-campaign.png  
**Stats**: 1 file changed, 0 insertions(+), 0 deletions(-)  

### `1c6877c` — 2026-04-25
**Author**: Zeyad Ahmed Elshenawy  
**Subject**: Delete docs/images/dashboard.jpeg  
**Stats**: 1 file changed, 0 insertions(+), 0 deletions(-)  

### `a157cf9` — 2026-04-25
**Author**: Zeyad Ahmed Elshenawy  
**Subject**: Delete docs/screenshots directory  
**Stats**: 18 files changed, 0 insertions(+), 0 deletions(-)  

### `7a99753` — 2026-04-25
**Author**: GarGantua  
**Subject**: docs(readme): refresh test count, SECRET_KEY note, screenshot placeholders  
**Stats**: 1 file changed, 21 insertions(+), 15 deletions(-)  

### `b6e84bd` — 2026-04-25
**Author**: Zeyad Ahmed Elshenawy  
**Subject**: chore: remove dead code paths, tighten SECRET_KEY guard, drop internal docs (#1)  
**Stats**: 17 files changed, 169 insertions(+), 3977 deletions(-)  

### `21f2da3` — 2026-04-25
**Author**: Zeyad Ahmed Elshenawy  
**Subject**: docs: add screenshots of all SmartCV pages  
**Stats**: 18 files changed, 0 insertions(+), 0 deletions(-)  

### `0fe0f0a` — 2026-04-25
**Author**: Zeyad Ahmed Elshenawy  
**Subject**: docs(images): replace dashboard with fresh capture  
**Stats**: 1 file changed, 0 insertions(+), 0 deletions(-)  

### `c61762a` — 2026-04-25
**Author**: Zeyad Ahmed Elshenawy  
**Subject**: docs(images): outreach campaign screenshot  
**Stats**: 1 file changed, 0 insertions(+), 0 deletions(-)  

### `65fae20` — 2026-04-25
**Author**: Zeyad Ahmed Elshenawy  
**Subject**: docs(images): add dashboard screenshot  
**Stats**: 1 file changed, 0 insertions(+), 0 deletions(-)  

### `12953aa` — 2026-04-25
**Author**: GarGantua  
**Subject**: docs(readme): final pass — badges, screenshot sections, license  
**Stats**: 1 file changed, 43 insertions(+), 4 deletions(-)  

### `7747219` — 2026-04-25
**Author**: GarGantua  
**Subject**: chore: add MIT LICENSE and .env.example  
**Stats**: 2 files changed, 37 insertions(+)  

### `e35f5b1` — 2026-04-25
**Author**: GarGantua  
**Subject**: chore: remove personal/scratch artifacts before public release  
**Stats**: 10 files changed, 698 deletions(-)  

### `bca8abb` — 2026-04-25
**Author**: GarGantua  
**Subject**: feat(benchmarks): Phase D5 — LLM-judged resume tailoring  
**Stats**: 12 files changed, 2272 insertions(+), 1052 deletions(-)  

### `bd39ddd` — 2026-04-25
**Author**: GarGantua  
**Subject**: feat(benchmarks): Phase E orchestrator + docs/benchmarks.md + README  
**Stats**: 9 files changed, 4614 insertions(+), 518 deletions(-)  

### `b465b73` — 2026-04-25
**Author**: GarGantua  
**Subject**: feat(benchmarks): Phase D1-D3 + fixture suite (10 CVs x 5 JDs)  
**Stats**: 22 files changed, 4171 insertions(+)  

### `1f6cbe7` — 2026-04-25
**Author**: GarGantua  
**Subject**: feat(benchmarks): coverage tooling + Phase B latency + D4 ATS scoring eval  
**Stats**: 13 files changed, 965 insertions(+), 17 deletions(-)  

### `9068ae0` — 2026-04-25
**Author**: GarGantua  
**Subject**: chore: pre-launch hardening — observability, prompt voice, UI + DB resilience  
**Stats**: 16 files changed, 989 insertions(+), 138 deletions(-)  

### `fe176bd` — 2026-04-21
**Author**: GarGantua  
**Subject**: fix(ui): Rebuild Tailwind CSS so dark opacity variants apply on outreach pages  
**Stats**: 1 file changed, 1 insertion(+), 1 deletion(-)  

### `136c651` — 2026-04-21
**Author**: GarGantua  
**Subject**: fix(outreach-ext): Route discovery push through SW to bypass Chrome PNA  
**Stats**: 2 files changed, 42 insertions(+), 14 deletions(-)  

### `6c15f64` — 2026-04-21
**Author**: GarGantua  
**Subject**: feat(outreach): v2 — extension auto-discovers targets from logged-in LinkedIn  
**Stats**: 8 files changed, 428 insertions(+), 10 deletions(-)  

### `3de9364` — 2026-04-21
**Author**: GarGantua  
**Subject**: docs(outreach): Spec v2 — move discovery from server into the extension  
**Stats**: 1 file changed, 219 insertions(+)  

### `fdb56c4` — 2026-04-21
**Author**: GarGantua  
**Subject**: feat(outreach): Manual "paste handle" path for campaign builder  
**Stats**: 3 files changed, 109 insertions(+)  

### `a94d38c` — 2026-04-21
**Author**: GarGantua  
**Subject**: fix(outreach): Add nav link to campaign builder + honest empty state  
**Stats**: 3 files changed, 82 insertions(+), 14 deletions(-)  

### `bbc2524` — 2026-04-21
**Author**: GarGantua  
**Subject**: fix(jobs): Save canonical scraped URL and bump Job.url max_length to 2000  
**Stats**: 3 files changed, 31 insertions(+), 4 deletions(-)  

### `cc608b7` — 2026-04-21
**Author**: GarGantua  
**Subject**: chore(dev): Activate project-local .venv inside run_dev.ps1  
**Stats**: 1 file changed, 13 insertions(+)  

### `ba90795` — 2026-04-21
**Author**: GarGantua  
**Subject**: docs(qa): Add manual test plan for outreach automation MVP  
**Stats**: 1 file changed, 236 insertions(+)  

### `d2b3fbc` — 2026-04-21
**Author**: GarGantua  
**Subject**: feat(outreach): Browser-extension hybrid outreach automation MVP  
**Stats**: 21 files changed, 1649 insertions(+), 9 deletions(-)  

### `7cc2f21` — 2026-04-21
**Author**: GarGantua  
**Subject**: docs(outreach): Spec browser-extension hybrid for LinkedIn outreach automation  
**Stats**: 1 file changed, 231 insertions(+)  

### `0625cce` — 2026-04-21
**Author**: GarGantua  
**Subject**: fix(ui): Stop LinkedIn signal card leaking Django comment as visible text  
**Stats**: 1 file changed, 4 insertions(+), 2 deletions(-)  

### `f873562` — 2026-04-21
**Author**: GarGantua  
**Subject**: chore: Drop unused Supabase REST client  
**Stats**: 2 files changed, 17 deletions(-)  

### `9e2c20a` — 2026-04-21
**Author**: GarGantua  
**Subject**: fix(dev): Workaround Py3.13 Windows WMI hang in entrypoints and run_dev.ps1  
**Stats**: 4 files changed, 51 insertions(+), 6 deletions(-)  

### `e17d3cb` — 2026-04-20
**Author**: GarGantua  
**Subject**: feat(dev): Install django-debug-toolbar for per-page query/timing stats  
**Stats**: 3 files changed, 26 insertions(+)  

### `4b66244` — 2026-04-20
**Author**: GarGantua  
**Subject**: fix(profiles): Signal card Edit / Save / Connect state machine  
**Stats**: 4 files changed, 160 insertions(+), 77 deletions(-)  

### `0c47cd5` — 2026-04-19
**Author**: GarGantua  
**Subject**: fix(profiles): Chatbot Completeness reads canonical profile_strength  
**Stats**: 3 files changed, 58 insertions(+), 23 deletions(-)  

### `a36c671` — 2026-04-19
**Author**: GarGantua  
**Subject**: fix(resumes): Restore big-preview template swap + fill thumb whitespace  
**Stats**: 1 file changed, 24 insertions(+), 7 deletions(-)  

### `3881b02` — 2026-04-19
**Author**: GarGantua  
**Subject**: fix(resumes): Thumbnail comment leak + richer mock content  
**Stats**: 1 file changed, 68 insertions(+), 25 deletions(-)  

### `abd4320` — 2026-04-19
**Author**: GarGantua  
**Subject**: feat(resumes): Thumbnail previews on template picker cards  
**Stats**: 2 files changed, 81 insertions(+)  

### `5be7d34` — 2026-04-19
**Author**: GarGantua  
**Subject**: fix(resumes): Make template radio reflect in the live preview  
**Stats**: 2 files changed, 353 insertions(+), 190 deletions(-)  

### `e11c7e2` — 2026-04-18
**Author**: GarGantua  
**Subject**: feat(onboarding): Insert "connect accounts" step after profile review  
**Stats**: 5 files changed, 140 insertions(+), 4 deletions(-)  

### `1cccf00` — 2026-04-18
**Author**: GarGantua  
**Subject**: feat(analysis): Live-update gap match % on drag-and-drop  
**Stats**: 4 files changed, 291 insertions(+), 104 deletions(-)  

### `8170788` — 2026-04-17
**Author**: GarGantua  
**Subject**: fix(profiles): Month-precision YoE with overlap merging  
**Stats**: 3 files changed, 346 insertions(+), 20 deletions(-)  

### `80e95f6` — 2026-04-17
**Author**: GarGantua  
**Subject**: fix(ui): Don't leak multi-line {# #} comments into toast area  
**Stats**: 3 files changed, 22 insertions(+), 4 deletions(-)  

### `c935e12` — 2026-04-17
**Author**: GarGantua  
**Subject**: feat(ui): Auto-dismiss success toasts after 2s  
**Stats**: 3 files changed, 79 insertions(+), 14 deletions(-)  

### `1cfd35f` — 2026-04-17
**Author**: GarGantua  
**Subject**: feat(onboarding): Skip button on every step for fresh signups  
**Stats**: 10 files changed, 146 insertions(+)  

### `a167b71` — 2026-04-17
**Author**: GarGantua  
**Subject**: feat(profiles): Animate + autofocus newly-added form rows  
**Stats**: 2 files changed, 67 insertions(+), 10 deletions(-)  

### `9ae52a3` — 2026-04-17
**Author**: GarGantua  
**Subject**: fix(profiles): Build-by-form flow — unhide input fields for fresh users  
**Stats**: 2 files changed, 151 insertions(+), 36 deletions(-)  

### `10e3268` — 2026-04-17
**Author**: GarGantua  
**Subject**: feat(errors): Styled CSRF failure page  
**Stats**: 4 files changed, 98 insertions(+)  

### `133e78e` — 2026-04-17
**Author**: GarGantua  
**Subject**: fix(components): Rename button.html form param to form_id  
**Stats**: 2 files changed, 15 insertions(+), 2 deletions(-)  

### `f7c744a` — 2026-04-17
**Author**: GarGantua  
**Subject**: feat(auth): Implement functional password reset flow  
**Stats**: 10 files changed, 277 insertions(+), 25 deletions(-)  

### `d9c0c85` — 2026-04-17
**Author**: GarGantua  
**Subject**: feat(auth): Hide top nav on login/register pages  
**Stats**: 3 files changed, 29 insertions(+), 23 deletions(-)  

### `006d436` — 2026-04-17
**Author**: GarGantua  
**Subject**: fix: Broken dashboard URLs, duplicate error messages, login UX, LLM error handling  
**Stats**: 5 files changed, 25 insertions(+), 30 deletions(-)  

### `d83140c` — 2026-04-16
**Author**: GarGantua  
**Subject**: fix: h-full on <html>, duplicate cover letter greeting, chatbot proj TypeError  
**Stats**: 3 files changed, 3 insertions(+), 5 deletions(-)  

### `f21f398` — 2026-04-16
**Author**: GarGantua  
**Subject**: test(auth): Verify login/register redirect authenticated users to dashboard  
**Stats**: 1 file changed, 20 insertions(+)  

### `fa3b29d` — 2026-04-16
**Author**: GarGantua  
**Subject**: fix(layout,auth): Body h-full locked page height; also guard auth pages  
**Stats**: 2 files changed, 5 insertions(+), 1 deletion(-)  

### `b030034` — 2026-04-16
**Author**: GarGantua  
**Subject**: fix(layout): Drop duplicate min-h-screen on page wrappers; load Alpine Collapse  
**Stats**: 25 files changed, 26 insertions(+), 25 deletions(-)  

### `900b079` — 2026-04-15
**Author**: GarGantua  
**Subject**: fix(templates): Collapse multi-line {# #} comments so they don't leak as text  
**Stats**: 5 files changed, 6 insertions(+), 14 deletions(-)  

### `6488280` — 2026-04-15
**Author**: GarGantua  
**Subject**: fix(profile): profile_strength handles list-shaped descriptions  
**Stats**: 2 files changed, 45 insertions(+), 4 deletions(-)  

### `7a8527f` — 2026-04-15
**Author**: GarGantua  
**Subject**: docs(qa): Manual end-to-end test plan covering all SmartCV surfaces  
**Stats**: 1 file changed, 221 insertions(+)  

### `ef5b319` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(profile): insights breakdown partial with component bars and CTAs  
**Stats**: 3 files changed, 95 insertions(+)  

### `02d25f4` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(profile): dashboard ring partial for profile strength  
**Stats**: 4 files changed, 55 insertions(+), 1 deletion(-)  

### `56d9501` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(profile): insights view injects profile_strength  
**Stats**: 2 files changed, 24 insertions(+)  

### `4686eab` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(profile): dashboard view injects profile_strength  
**Stats**: 2 files changed, 16 insertions(+)  

### `2d3d65e` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(profile): compute_profile_strength — assembly + top actions  
**Stats**: 2 files changed, 56 insertions(+), 2 deletions(-)  

### `bb7280a` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(profile): _tier thresholds + _top_actions helper  
**Stats**: 2 files changed, 89 insertions(+)  

### `63c8015` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(profile): _score_signals — 35-pt component with freshness  
**Stats**: 2 files changed, 163 insertions(+)  

### `8a8a0b7` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(profile): _score_evidence — 30-pt component  
**Stats**: 2 files changed, 113 insertions(+)  

### `d121acc` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(profile): _score_completeness — 35-pt component  
**Stats**: 2 files changed, 112 insertions(+)  

### `4dae74c` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(profile): profile_strength scaffold — types, href map, stub fn  
**Stats**: 2 files changed, 319 insertions(+), 216 deletions(-)  

### `e94cc0c` — 2026-04-15
**Author**: GarGantua  
**Subject**: docs(plan): Profile-strength scoring — 11-task TDD implementation plan  
**Stats**: 1 file changed, 1265 insertions(+)  

### `ae3b045` — 2026-04-15
**Author**: GarGantua  
**Subject**: docs(spec): Profile-strength scoring — design for Feature #2  
**Stats**: 1 file changed, 223 insertions(+)  

### `bd89d44` — 2026-04-15
**Author**: GarGantua  
**Subject**: Merge branch 'feat/job-aware-agent-context'  

### `46b57c1` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(agent): interviewing stage surfaces 'Ask agent about this role' chip  
**Stats**: 2 files changed, 20 insertions(+), 1 deletion(-)  

### `c63e7d7` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(agent): scope pill, job-scoped seeds, jobId in POST body  
**Stats**: 3 files changed, 34 insertions(+), 4 deletions(-)  

### `11f064b` — 2026-04-15
**Author**: GarGantua  
**Subject**: polish(agent): Unify job-not-found copy across view and API  
**Stats**: 1 file changed, 2 insertions(+), 2 deletions(-)  

### `8b714d5` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(agent): API accepts job_id, forwards Job to chat()  
**Stats**: 2 files changed, 83 insertions(+), 4 deletions(-)  

### `2d01070` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(agent): view reads ?job= param, validates ownership  
**Stats**: 2 files changed, 73 insertions(+), 3 deletions(-)  

### `6b12904` — 2026-04-15
**Author**: GarGantua  
**Subject**: refactor(agent): Rename prompt section label TALKING ABOUT JOB → JOB CONTEXT  
**Stats**: 2 files changed, 4 insertions(+), 4 deletions(-)  

### `80b8130` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(agent): chat() threads optional job to system prompt  
**Stats**: 1 file changed, 2 insertions(+), 2 deletions(-)  

### `31b5598` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(agent): build_system_prompt accepts optional job for scoped context  
**Stats**: 2 files changed, 36 insertions(+), 1 deletion(-)  

### `2990fe7` — 2026-04-15
**Author**: GarGantua  
**Subject**: fix(agent): log (not swallow) DB errors in job dossier fetches  
**Stats**: 1 file changed, 3 insertions(+), 1 deletion(-)  

### `c474d32` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(agent): job dossier — artifacts subsection  
**Stats**: 2 files changed, 49 insertions(+)  

### `c42d9a4` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(agent): job dossier — snapshot variant subsection  
**Stats**: 2 files changed, 40 insertions(+)  

### `3e89a2a` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(agent): job dossier — gap analysis subsection  
**Stats**: 2 files changed, 45 insertions(+)  

### `758e2d1` — 2026-04-15
**Author**: GarGantua  
**Subject**: feat(agent): _build_job_context_block — base header for job dossier  
**Stats**: 2 files changed, 56 insertions(+)  

### `1b6a7de` — 2026-04-15
**Author**: GarGantua  
**Subject**: docs(plan): Job-aware agent context — 11-task TDD implementation plan  
**Stats**: 1 file changed, 1085 insertions(+)  

### `794f6b6` — 2026-04-15
**Author**: GarGantua  
**Subject**: docs(spec): Job-aware agent context — design for Feature #1  
**Stats**: 1 file changed, 176 insertions(+)  

### `7730188` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(agent): Global career chat at /agent/ — talk to your agent without a job  
**Stats**: 6 files changed, 628 insertions(+), 1 deletion(-)  

### `a5e6db3` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(dashboard): Deep-link stage primary CTA + secondary-action chips  
**Stats**: 4 files changed, 234 insertions(+), 33 deletions(-)  

### `ac4790f` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(onboarding): /welcome/ orchestrator for first-run signups  
**Stats**: 7 files changed, 213 insertions(+), 7 deletions(-)  

### `9bfd2a4` — 2026-04-14
**Author**: GarGantua  
**Subject**: copy(voice): Unify agent voice across task-tool pages  
**Stats**: 9 files changed, 22 insertions(+), 22 deletions(-)  

### `97b3427` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(positioning): Reframe SmartCV as career agent, not CV maker  
**Stats**: 11 files changed, 646 insertions(+), 32 deletions(-)  

### `da7d12d` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(scoring): Smarter ATS score + evidence confidence indicator  
**Stats**: 6 files changed, 404 insertions(+), 23 deletions(-)  

### `f240a4a` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(profiles): LinkedIn / Scholar / Kaggle signal aggregation  
**Stats**: 13 files changed, 1260 insertions(+), 9 deletions(-)  

### `a7c4e5f` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(analysis): Feed GitHub signals into the gap-analysis prompt  
**Stats**: 2 files changed, 149 insertions(+), 2 deletions(-)  

### `e158df6` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(profiles): GitHub signal aggregation (DINQ-inspired)  
**Stats**: 6 files changed, 603 insertions(+), 5 deletions(-)  

### `d97e84a` — 2026-04-14
**Author**: GarGantua  
**Subject**: fix(design): Restore pointer cursor on buttons + interactive controls  
**Stats**: 2 files changed, 20 insertions(+), 1 deletion(-)  

### `2f59313` — 2026-04-14
**Author**: GarGantua  
**Subject**: fix(design): Cool slate page bg so white cards visibly separate  
**Stats**: 2 files changed, 7 insertions(+), 3 deletions(-)  

### `e579a3f` — 2026-04-14
**Author**: GarGantua  
**Subject**: fix(design): Revert to pre-redesign cool light-mode palette  
**Stats**: 2 files changed, 9 insertions(+), 14 deletions(-)  

### `d9708ca` — 2026-04-14
**Author**: GarGantua  
**Subject**: fix(design): Stronger amber light-mode + auto card elevation  
**Stats**: 2 files changed, 24 insertions(+), 8 deletions(-)  

### `736f104` — 2026-04-14
**Author**: GarGantua  
**Subject**: fix(design): Strip rendering Django comments + warm light-mode bg  
**Stats**: 13 files changed, 3752 insertions(+), 3902 deletions(-)  

### `d25280a` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(design): Redesign master-profile form and chatbot  
**Stats**: 3 files changed, 681 insertions(+), 803 deletions(-)  

### `6bd7344` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(design): Redesign remaining secondary pages  
**Stats**: 8 files changed, 472 insertions(+), 515 deletions(-)  

### `d2582c7` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(design): Redesign auth, error, and profile-choice pages  
**Stats**: 8 files changed, 245 insertions(+), 295 deletions(-)  

### `4442be0` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(design): Redesign base chrome (nav + footer) and dashboard  
**Stats**: 3 files changed, 453 insertions(+), 583 deletions(-)  

### `3dd6987` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(design): Redesign resume generate / edit / preview trio  
**Stats**: 4 files changed, 773 insertions(+), 800 deletions(-)  

### `a0d7595` — 2026-04-14
**Author**: GarGantua  
**Subject**: refactor(design): Tighten upload & review-job pages for product fit  
**Stats**: 2 files changed, 34 insertions(+), 29 deletions(-)  

### `912c2d6` — 2026-04-14
**Author**: GarGantua  
**Subject**: refactor(design): Structural rethinks for gap analysis and job input  
**Stats**: 3 files changed, 147 insertions(+), 200 deletions(-)  

### `f7f430d` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(design): Redesign gap analysis page in Editorial AI language  
**Stats**: 2 files changed, 380 insertions(+), 379 deletions(-)  

### `cf5ad80` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(design): Redesign job review/confirm page in Editorial AI language  
**Stats**: 2 files changed, 78 insertions(+), 108 deletions(-)  

### `cbf3211` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(design): Redesign job input page in Editorial AI language  
**Stats**: 2 files changed, 91 insertions(+), 125 deletions(-)  

### `46b8e3b` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(design): Redesign CV upload page in Editorial AI language  
**Stats**: 2 files changed, 118 insertions(+), 77 deletions(-)  

### `a526cab` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(design): Phase 4 — landing page redesign in Editorial AI direction  
**Stats**: 2 files changed, 279 insertions(+), 104 deletions(-)  

### `57a341b` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(design): Phase 3 — component primitives + /design/ styleguide  
**Stats**: 10 files changed, 366 insertions(+), 1 deletion(-)  

### `e364dc3` — 2026-04-14
**Author**: GarGantua  
**Subject**: feat(design): Phase 2 — Tailwind v4 build + creative design tokens  
**Stats**: 43 files changed, 1235 insertions(+), 6128 deletions(-)  

### `657eecd` — 2026-04-14
**Author**: GarGantua  
**Subject**: test(profiles): Cover cv_parser text sanitization and personal info  
**Stats**: 1 file changed, 186 insertions(+), 3 deletions(-)  

### `db44da0` — 2026-04-14
**Author**: GarGantua  
**Subject**: chore: Rename dev smoke scripts from test_*.py to smoke_*.py  
**Stats**: 4 files changed, 0 insertions(+), 0 deletions(-)  

### `e53e71f` — 2026-04-14
**Author**: GarGantua  
**Subject**: test(resumes): Cover description list<->textarea conversion  
**Stats**: 2 files changed, 136 insertions(+), 12 deletions(-)  

### `7424652` — 2026-04-14
**Author**: GarGantua  
**Subject**: test(analysis): Cover gap_analyzer reconciliation, early exits, and fallback  
**Stats**: 1 file changed, 198 insertions(+), 3 deletions(-)  

### `2ddf64a` — 2026-04-12
**Author**: GarGantua  
**Subject**: fix(indeed): Use inner_text() instead of text_content() to exclude inline styles  
**Stats**: 1 file changed, 5 insertions(+), 5 deletions(-)  

### `9609e00` — 2026-04-12
**Author**: GarGantua  
**Subject**: feat(scrapers): Indeed scraper via Playwright; per-source tabs in job input  
**Stats**: 5 files changed, 272 insertions(+), 45 deletions(-)  

### `80f5a9e` — 2026-04-12
**Author**: GarGantua  
**Subject**: feat(scrapers): Multi-source job scraper framework (LinkedIn, Greenhouse, Lever, generic JSON-LD)  
**Stats**: 9 files changed, 609 insertions(+), 12 deletions(-)  

### `1090960` — 2026-04-12
**Author**: GarGantua  
**Subject**: feat(quality): Domain-specific guidance in resume generation prompt  
**Stats**: 1 file changed, 133 insertions(+), 1 deletion(-)  

### `cdd0190` — 2026-04-12
**Author**: GarGantua  
**Subject**: feat(templates): Add 3 professional B&W one-column PDF templates  
**Stats**: 5 files changed, 755 insertions(+), 16 deletions(-)  

### `9c91fe2` — 2026-04-12
**Author**: GarGantua  
**Subject**: feat(quality): Upgrade resume and cover letter generation prompts  
**Stats**: 2 files changed, 75 insertions(+), 15 deletions(-)  

### `1540d53` — 2026-04-12
**Author**: GarGantua  
**Subject**: chore: Commit prior-session WIP, project docs, and ignore rules  
**Stats**: 24 files changed, 3820 insertions(+), 3498 deletions(-)  

### `377534d` — 2026-04-12
**Author**: GarGantua  
**Subject**: fix(gap-analysis): Stop silent task failures, handle empty inputs, seniority hints  
**Stats**: 3 files changed, 110 insertions(+), 37 deletions(-)  

### `ae89394` — 2026-04-12
**Author**: GarGantua  
**Subject**: fix(chatbot): Cache-backed state, loop detection, recoverable errors, retry UI  
**Stats**: 3 files changed, 125 insertions(+), 30 deletions(-)  

### `d8f7628` — 2026-04-12
**Author**: GarGantua  
**Subject**: fix(ux): Add next-step CTAs on dead-end pages, smart job detail action button  
**Stats**: 6 files changed, 526 insertions(+), 458 deletions(-)  

### `5ff4cba` — 2026-04-12
**Author**: GarGantua  
**Subject**: fix(ux): Render flash messages, smart post-profile redirect, cleaner unauth nav  
**Stats**: 2 files changed, 358 insertions(+), 331 deletions(-)  

### `15da729` — 2026-04-12
**Author**: GarGantua  
**Subject**: fix: Tighten regex name/location extraction to prefer null over wrong hints  
**Stats**: 1 file changed, 110 insertions(+), 51 deletions(-)  

### `334b532` — 2026-04-12
**Author**: GarGantua  
**Subject**: fix: Guarantee resume content is populated from profile even if LLM drops sections  
**Stats**: 1 file changed, 108 insertions(+), 6 deletions(-)  

### `a61c453` — 2026-04-12
**Author**: GarGantua  
**Subject**: fix: Auto-regenerate stale resume content when profile is newer  
**Stats**: 1 file changed, 53 insertions(+), 15 deletions(-)  

### `620c8f1` — 2026-04-12
**Author**: GarGantua  
**Subject**: fix: Preserve case in letter-spacing repair; clarify language/experience classification  
**Stats**: 2 files changed, 56 insertions(+), 25 deletions(-)  

### `88d7382` — 2026-04-12
**Author**: GarGantua  
**Subject**: fix: Add field mapping instructions and increase max_tokens in resume generator  
**Stats**: 1 file changed, 18 insertions(+), 7 deletions(-)  

### `bd7368d` — 2026-04-12
**Author**: GarGantua  
**Subject**: fix: Improve CV extraction accuracy for education dates, embedded links, and letter-spaced text  
**Stats**: 2 files changed, 143 insertions(+), 29 deletions(-)  

### `e9deb11` — 2026-04-11
**Author**: GarGantua  
**Subject**: fix: Map UI textarea schemas to backend List[str] schemas in resume_edit_view to prevent bracket notation data corruption  
**Stats**: 3 files changed, 59 insertions(+), 6 deletions(-)  

### `dea50cb` — 2026-04-11
**Author**: GarGantua  
**Subject**: feat: Upgrade PDF and Preview rendering to high-fidelity LaTeX style  
**Stats**: 5 files changed, 286 insertions(+), 276 deletions(-)  

### `4520631` — 2026-04-11
**Author**: GarGantua  
**Subject**: feat: Add CV bullet extraction and state mutation to chatbot  
**Stats**: 25 files changed, 1587 insertions(+), 631 deletions(-)  

### `b8bc656` — 2026-04-07
**Author**: GarGantua  
**Subject**: fix: accept array descriptions in ResumeProject schema to prevent Groq 400 errors  
**Stats**: 1 file changed, 10 insertions(+), 2 deletions(-)  

### `4143d27` — 2026-04-07
**Author**: GarGantua  
**Subject**: fix: remove all stale django_q imports causing NameError on startup  
**Stats**: 4 files changed, 4 insertions(+), 5 deletions(-)  

### `c8d8e03` — 2026-04-07
**Author**: GarGantua  
**Subject**: feat: removed django-q and shifted to synchronous execution for all AI tasks  
**Stats**: 8 files changed, 96 insertions(+), 152 deletions(-)  

### `21e8896` — 2026-04-07
**Author**: GarGantua  
**Subject**: checkpoint before removing django-q clustering  
**Stats**: 3 files changed, 18 insertions(+)  

### `9a5d127` — 2026-04-06
**Author**: GarGantua  
**Subject**: fix: chatbot repeat loop, skill normalization, and quality threshold  
**Stats**: 1 file changed, 129 insertions(+), 108 deletions(-)  

### `4459b11` — 2026-04-06
**Author**: GarGantua  
**Subject**: fix: add Phase 2 reconciliation to guarantee 100% skill coverage in gap analysis  
**Stats**: 1 file changed, 22 insertions(+), 1 deletion(-)  

### `a06c18a` — 2026-04-06
**Author**: GarGantua  
**Subject**: improve: comprehensive gap analysis with full profile context including certs, projects, experience  
**Stats**: 1 file changed, 155 insertions(+), 59 deletions(-)  

### `b8632a4` — 2026-04-06
**Author**: GarGantua  
**Subject**: refactor: remove SentenceTransformer, go full LLM for gap analysis  
**Stats**: 6 files changed, 73 insertions(+), 212 deletions(-)  

### `b800e8a` — 2026-04-06
**Author**: GarGantua  
**Subject**: fix: eliminate infinite loading in chatbot, gap analysis, and resume generation  
**Stats**: 12 files changed, 653 insertions(+), 475 deletions(-)  

### `c795b9c` — 2026-04-05
**Author**: GarGantua  
**Subject**: Optimize SmartCV Gap Analysis Pipeline: Similarity math, Cardinality isolation, Dimension schema constraints, async Background Tasks with django-q2  
**Stats**: 13 files changed, 454 insertions(+), 21 deletions(-)  

### `3446da7` — 2026-04-05
**Author**: GarGantua  
**Subject**: fix(analyzer): add strict prompt rules to stop Llama 3 generating preambles during forced tool calls  
**Stats**: 1 file changed, 3 insertions(+), 1 deletion(-)  

### `2f83b23` — 2026-04-04
**Author**: GarGantua  
**Subject**: feat(ui): add clear button to upload dropzone to remove selected file  
**Stats**: 1 file changed, 15 insertions(+)  

### `bfab412` — 2026-04-04
**Author**: GarGantua  
**Subject**: feat(ui): add dynamic visual feedback on file selection in dropzone  
**Stats**: 1 file changed, 36 insertions(+), 4 deletions(-)  

### `fb0d80d` — 2026-04-04
**Author**: GarGantua  
**Subject**: fix(backend): stop discarding extra schema fields when serializing context for UI  
**Stats**: 1 file changed, 2 insertions(+), 15 deletions(-)  

### `cc55361` — 2026-04-04
**Author**: GarGantua  
**Subject**: feat(ui): strictly hide empty fields and map all schemas to form  
**Stats**: 3 files changed, 408 insertions(+), 13 deletions(-)  

### `a9db863` — 2026-04-04
**Author**: GarGantua  
**Subject**: feat(ui): add dynamic highlights editor for arrays in experience and projects  
**Stats**: 1 file changed, 37 insertions(+), 5 deletions(-)  

### `0496cee` — 2026-04-04
**Author**: GarGantua  
**Subject**: fix(schemas): allow nulls in array fields to prevent strict schema crashes during LLM response mapping  
**Stats**: 1 file changed, 17 insertions(+), 17 deletions(-)  

### `cd6972b` — 2026-04-04
**Author**: GarGantua  
**Subject**: Migrate to LangChain + Groq architecture; improve structured outputs  
**Stats**: 283 files changed, 4747 insertions(+), 4774 deletions(-)  

### `c5f3541` — 2026-03-10
**Author**: GarGantua  
**Subject**: Phase 4f: Final Polish for Login, Register, and Resume List pages  
**Stats**: 3 files changed, 178 insertions(+), 157 deletions(-)  

### `6db9df5` — 2026-03-10
**Author**: GarGantua  
**Subject**: Phase 4e: Redesigned Cover Letter and Job Detail flows  
**Stats**: 4 files changed, 204 insertions(+), 130 deletions(-)  

### `174a976` — 2026-03-10
**Author**: GarGantua  
**Subject**: Phase 4d: Redesigned 3-column builder flows  
**Stats**: 2 files changed, 386 insertions(+), 192 deletions(-)  

### `cff85dd` — 2026-03-10
**Author**: GarGantua  
**Subject**: Phase 4c: Redesigned input forms  
**Stats**: 2 files changed, 194 insertions(+), 137 deletions(-)  

### `fb3aab0` — 2026-03-10
**Author**: GarGantua  
**Subject**: Phase 4b: Redesigned Landing Pages (Home and Dashboard)  
**Stats**: 2 files changed, 142 insertions(+), 79 deletions(-)  

### `1889e85` — 2026-03-10
**Author**: GarGantua  
**Subject**: Initial project state before UI redesign  
**Stats**: 379 files changed, 17227 insertions(+)  


## 90. Branches and Tags

**Branches**:
- `main` (HEAD) — Tracks `origin/main`. Up to date.
- `master` — Older branch, ancestor of `main`. Kept for safety; not actively used.
- `remotes/origin/main` — Matches local `main`.

**Tags**: None. Versioning is commit-based.

## 91. Top 20 Most-Changed Files

These files appear most often in `git log --all --pretty=format: --name-only | sort | uniq -c | sort -rn`:

1. **`static/css/output.css`** — 27 commits. Tailwind build artifact, regenerated whenever templates change.
2. **`core/tests.py`** — 24 commits. Test expansion for observability + agent chat.
3. **`profiles/views.py`** — 22 commits. Profile/signal handling, the most-touched view file.
4. **`profiles/tests.py`** — 19 commits. CV parser test expansion.
5. **`templates/base.html`** — 16 commits. Master layout + navigation evolution.
6. **`templates/profiles/dashboard.html`** — 12 commits. Dashboard redesigns + profile-strength integration.
7. **`templates/jobs/input.html`** — 12 commits. Multi-source tabs added, drag-drop UX iterations.
8. **`templates/analysis/gap_analysis.html`** — 12 commits. Three-column layout + drag-and-drop.
9. **`smartcv/settings.py`** — 12 commits. Configuration evolution.
10. **`core/views.py`** — 11 commits. Landing/agent chat/welcome additions.
11. **`templates/resumes/edit.html`** — 11 commits. Live editor evolution.
12. **`templates/profiles/upload_cv.html`** — 11 commits. Dropzone polish.
13. **`templates/profiles/manual_form.html`** — 11 commits. Form-builder redesigns.
14. **`templates/resumes/generate.html`** — 10 commits. Template picker + thumbnails.
15. **`profiles/urls.py`** — 10 commits. Route additions for outreach + connect-accounts.
16. **`jobs/views.py`** — 10 commits. Scraper integration + status updates.
17. **`analysis/views.py`** — 10 commits. Gap analysis caching + drag-drop API.
18. **`analysis/services/gap_analyzer.py`** — 10 commits. Two-phase logic evolution.
19. **`templates/profiles/chatbot.html`** — 9 commits. Chatbot UI iterations.
20. **`run_dev.ps1`** — 9 commits. Windows dev runner refinements.

## 92. Contributor Statistics

```
$ git shortlog -sne --all
   155	GarGantua <zeyadelshenawy1@gmail.com>
    13	Zeyad Ahmed Elshenawy <115832263+ZeyadElshenawy@users.noreply.github.com>
```

The 13 GitHub-noreply commits are typically PR squashes (#1, #2, #3, #4) and merges performed via GitHub's web UI. The author is the same person — Zeyad Elshenawy — but GitHub uses the noreply email when commits originate from web UI actions.

Total commits: **168**.

---


# PART 17 — Key Data Flows (End-to-End)

## 93. CV Upload → Parse → Validate → Embed → Profile Save

```
User clicks "Upload CV" on dashboard
  ↓
GET /profiles/upload/
  → renders templates/profiles/upload_cv.html
  → Alpine dropzone (x-data="{ file: null, dragOver: false }")

User selects file (PDF or DOCX)
  ↓
POST /profiles/upload/ (multipart, with `cv` file field)
  → views.py:upload_master_profile receives request
  → File saved to media/cvs/<uuid>.pdf
  → cv_parser.parse_cv(file_path) called

cv_parser.parse_cv(file_path):
  1. CVExtractor.extract_text(file_path)
     - if PDF: pdfplumber or PyMuPDF
       - Extract page text + embedded link annotations
       - Append [Embedded Link: 'label' -> uri] markers
     - if DOCX: python-docx
       - Walk paragraphs + tables + relationship hyperlinks
  2. _sanitize_text(raw)
     - Letter-spaced word repair (regex hit-list, ~20 patterns)
     - Header/footer noise removal
     - Whitespace normalization
  3. find_section_headers(text)
     - Fuzzy regex matching against ~10 section types
  4. extract_personal_info(text)
     - Email (regex)
     - Phone (improved regex)
     - URLs from [Embedded Link: ...] tags
     - Name (conservative; null-on-uncertainty)
     - Location (strict City, State/Country format)
     - LinkedIn handle fallback
  5. extract_experience / extract_education / extract_skills /
     extract_projects / extract_certifications
     - Per-section split by date pattern or all-caps headers
  6. Flatten skills dict into list of {name, proficiency, category}
  7. Filter through _is_plausible_skill_name (drops PDF noise)
  8. Return flat dict with full_name, email, phone, location, ...

  ↓
view: validate parsed data
  → llm_validator.validate(parsed)
    → get_structured_llm(SemanticValidationResult).invoke(prompt)
    → returns {makes_sense, clarification_question?}
  → If makes_sense=False, surface the question on review screen.

  ↓
view: save to UserProfile
  → profile, _ = UserProfile.objects.get_or_create(user=user)
  → profile.full_name = parsed['full_name']
  → profile.email = parsed['email']
  → ...
  → profile.data_content = {
       'skills': [{name, proficiency, category}, ...],
       'experiences': [...],
       'education': [...],
       'projects': [...],
       'certifications': [...],
       'languages': [...],
       ...
    }
  → profile.uploaded_cv = file
  → profile.input_method = 'upload'
  → profile.save()

  ↓
view: generate embeddings (synchronous, ~10-20s)
  → embeddings.generate_for_profile(profile)
    - HuggingFace `all-MiniLM-L6-v2` model
    - 4 calls: whole, skills, experience, education
    - profile.embedding = ...
    - profile.embedding_skills = ...
    - profile.embedding_experience = ...
    - profile.embedding_education = ...
  → profile.save()

  ↓
view: trigger external signal aggregation (async-style; runs synchronously
       but is short-circuited if signals already cached)
  → If profile.github_url: github_aggregator.fetch_and_cache(profile)
  → If profile.linkedin_url: linkedin_aggregator.fetch_and_cache(profile)
  → If kaggle handle: kaggle_aggregator.fetch_and_cache(profile)

  ↓
HTTP 302 → /profiles/review/
  → render templates/profiles/review_master_profile.html
  → User reviews, edits, saves
```

This whole pipeline takes 15–30 seconds for a typical CV. The user sees a loading spinner with status messages ("Parsing your CV…", "Validating with AI…", "Generating embeddings…").

## 94. Job Input (URL/Text) → Scrape → Skill Extract → Save

```
User clicks "Add Job" on dashboard
  ↓
GET /jobs/input/
  → renders templates/jobs/input.html
  → Per-source tabs: LinkedIn / Indeed / Greenhouse / Lever / Other
  → Tab selection sets `source_hint` form field

User pastes URL into selected tab, OR types description manually

POST /jobs/input/
  → views.py:job_input_view

if input_method == 'url':
  → scrape_job(url) [scrapers/dispatcher.py]
    → Try LinkedInScraper.can_handle(url) → if true, scrape
    → Try GreenhouseScraper.can_handle(url) → ...
    → Try LeverScraper.can_handle(url) → ...
    → Try IndeedScraper.can_handle(url) → ...
    → Fall back to GenericJSONLDScraper
    → Returns {title, company, description, raw_html, cleaned_url, source}

  → Soft-validate: log mismatch if source_hint != detected source

  → extract_skills(job_data['description']) [skill_extractor.py]
    → Build prompt with anti-hallucination rules
    → get_structured_llm(SkillListResult).invoke(prompt)
    → Filter: _GENERIC_SOFT_SKILL_DENYLIST
    → Filter: _is_jd_anchored (3 passes)
    → Returns clean list of skill names

  → Job.objects.create(
        user=user,
        url=cleaned_url or original_url,
        title=job_data['title'],
        company=job_data['company'],
        description=job_data['description'],
        raw_html=job_data['raw_html'],
        extracted_skills=list(skills),
    )

elif input_method == 'text':
  → Use posted title/company/description directly
  → extract_skills(description) → same pipeline as above
  → Job.objects.create(...)  # url=None

  ↓
HTTP 302 → /jobs/review/<id>/
  → render templates/jobs/review_job.html
  → User confirms title/company/description
  → If description changed, re-extract skills (and bust embedding)
  → POST → continue to /analysis/<id>/
```

The cleaned-URL save (commit `bbc2524`) strips LinkedIn tracking tokens. Without this, URLs would routinely exceed Django's default 200-char URLField limit.

## 95. Gap Analysis → LLM Categorize → Reconcile → Persist

```
User clicks "Run Gap Analysis" on review_job page
  ↓
GET /analysis/<job_id>/
  → views.py:gap_analysis_view
  → Check for cached GapAnalysis row matching (job, user)
  → If exists and not ?refresh=1, render with cached data + return early

  → Validate preconditions:
    - job.extracted_skills must be non-empty
    - profile.skills must be non-empty
  → If failure, redirect with messages.error / messages.warning

  → render with `is_computing=True` (spinner)

POST /analysis/<job_id>/compute/
  → views.py:compute_gap_api
  → Call tasks.compute_gap_analysis_task(job.id, user.id)

tasks.compute_gap_analysis_task:
  → Load profile and job
  → result = compute_gap_analysis(profile, job)

compute_gap_analysis(profile, job):
  1. Build candidate_context via _build_full_candidate_context(profile)
     - Skills section
     - Work experience (top 5, with description[:300] and highlights[:4])
     - Projects (top 5, with description[:200] and highlights[:3])
     - Certifications (top 10)
     - Education (top 3)
     - GitHub block (if signals cached)
     - Scholar block (if signals cached)
     - Kaggle block (if signals cached)

  2. Build the long prompt with:
     - JOB TITLE / COMPANY / REQUIRED SKILLS
     - {candidate_context}
     - 5 critical matching rules (HOLISTIC EVIDENCE, DIRECTIONAL SPECIFICITY,
       NO DUPLICATES, CASE-INSENSITIVE, SENIORITY)

  3. structured_llm = get_structured_llm(GapAnalysisResult, temp=0.1, max_tokens=2000)
     result = structured_llm.invoke(prompt)
     # result.matched_skills, .critical_missing_skills,
     # .soft_skill_gaps, .similarity_score

  4. Phase 2: Programmatic Reconciliation
     - matched_set = lowercased matched
     - missing_set = lowercased missing
     - Drop anything in both (LLM duplication)
     - For every job_skill not in either set:
       - Fuzzy match (cutoff 0.85) against matched_set
       - If close match, count as matched
       - Else, conservatively add to missing
     - Returns dict with all four lists + similarity_score + analysis_method

  ↓
tasks: save to DB
  → GapAnalysis.objects.update_or_create(
        job=job, user=user,
        defaults={
            'matched_skills': result['matched_skills'],
            'missing_skills': result['missing_skills'],
            'partial_skills': result['partial_skills'],
            'similarity_score': result['similarity_score'],
        }
    )

  ↓
view: HTTP 200 with success JSON
  → Frontend reloads /analysis/<job_id>/
  → renders gap_analysis.html with full data

User views drag-and-drop interface
  → Three columns: matched / partial-soft / missing
  → Live-update match% on drag (Alpine recompute)
  → POST /analysis/<job_id>/skills/ persists changes
```

The full LLM call takes 2–3 seconds. The reconciliation is pure-Python and microsecond-level. The 50-pair benchmark shows 99.9% coverage (49 of 50 pairs achieve 100%).

## 96. Resume Generation → Domain Detect → Tailor → Score → Render

```
User clicks "Generate Tailored Resume" from gap analysis page
  (only enabled if match% > 50%)
  ↓
GET /resumes/generate/<gap_id>/
  → views.py:resume_generate_view
  → Render template picker (6 styles with thumbnails)

User selects template, clicks "Generate"

POST /resumes/generate/<gap_id>/run/
  → views.py:generate_resume_view
  → Load gap_analysis, profile, job
  → result = generate_resume_content(profile, job, gap_analysis)

generate_resume_content(profile, job, gap_analysis):
  1. Build raw_cv_data from profile.data_content
  2. Slim it: drop raw_text, empty fields, normalized_summary, objective
  3. Domain detection: _detect_job_domain(job)
     - Lowercase concatenation of title + description[:500]
     - Score each domain by keyword hit count
     - Return best, defaulting to 'general'
  4. Domain prompt addendum: _DOMAIN_PROMPTS[domain]
  5. Build long prompt:
     - JOB DETAILS
     - COMPLETE CV DATA (slim_cv as JSON)
     - MATCHED SKILLS (high priority)
     - FIELD MAPPING table
     - STRICT ANTI-HALLUCINATION RULE
     - REMOVE FROM RESUMES list
     - LANGUAGE & STYLE rules
     - BULLET POINT STANDARDS
     - LENGTH & DENSITY
     - REWRITE & STRUCTURING
     - ATS OPTIMIZATION
     - THEME MIRRORING
     - {domain_section}
     - {HUMAN_VOICE_RULE}
  6. structured_llm = get_structured_llm(ResumeContentResult, temp=0.7, max_tokens=8192)
  7. result = structured_llm.invoke(prompt)
  8. resume_content = result.model_dump()
  9. _ensure_profile_data_preserved(resume_content, raw_cv_data)
     - Fill empty experience from profile.experiences
     - Patch empty year on education from graduation_year
     - Fill empty projects, certifications, languages, skills
  10. Return resume_content

  ↓
view: compute ATS score
  → ats_score = calculate_ats_score(resume_content, job.extracted_skills)
    → compute_ats_breakdown(content, skills) → AtsBreakdown
    → Returns final 0-100 score

  ↓
view: save to DB
  → resume = GeneratedResume.objects.create(
        gap_analysis=gap_analysis,
        content=resume_content,
        ats_score=ats_score,
    )

  ↓
HTTP 302 → /resumes/edit/<resume_id>/
  → render templates/resumes/edit.html
  → Live editor with textareas + live ATS score (Alpine)
  → On save: POST persists changes; recomputes ATS score

User reviews, may iterate, then clicks "Download PDF"
  ↓
GET /resumes/pdf/<resume_id>/
  → views.py:resume_pdf_view
  → Pick template based on ?template=<name> query param
  → Render templates/resumes/pdf_template_<name>.html with context
  → pdf_generator.generate_pdf(html) → bytes
  → HttpResponse(content, content_type='application/pdf')
```

LLM call: ~3 seconds. Total flow: ~5 seconds.

## 97. Outreach Campaign → Discover → Queue → Extension Drains → Audit

This is the most complex flow because it spans the server, the user's browser tab, the Chrome extension, and LinkedIn's actual UI.

```
=== Setup phase ===

User pairs the extension:
  GET /profiles/extension/pair/
    → Renders pair-screen with QR code + token
    → If user.outreach_token is None: rotate (new UUID)
    → Display token

  User opens chrome://extensions, configures Options:
    - SmartCV host: http://127.0.0.1:8000
    - Auth token: <pasted>
  → chrome.storage.local.set({host, token})


=== Campaign setup ===

User goes to /profiles/outreach/<job_id>/
  → If no campaign: form to create one
  → If campaign exists: show queue + status

User opens LinkedIn job page in their browser (logged in)
  → content_discover.js auto-runs
  → Scrapes the "Meet the hiring team" / "People you can reach out to"
    sections
  → For each found target:
    - { handle, name, role, source }
  → chrome.runtime.sendMessage({type: 'DISCOVER_PUSH', payload})

Service worker receives DISCOVER_PUSH:
  → POST <host>/profiles/api/outreach/discover with token
    Body: { job_id (inferred from current LinkedIn page),
            targets: [{handle, name, role, source}, ...] }

Server (views_outreach_api.py:discover_push):
  → Validate token → User
  → For each target, DiscoveredTarget.objects.get_or_create(
        user=user, job=job, handle=handle,
        defaults={'name': ..., 'role': ..., 'source': ...},
    )

User goes back to /profiles/outreach/<job_id>/
  → Refreshes; sees discovered targets in "Pending discovery" section
  → Clicks "Queue with note" on a target

Server (views_outreach_api.py or views.py):
  → Generate personalized message via outreach_generator.generate_campaign(
        profile, job, target.name, target.role
    )
    → Build prompt with anti-generic rules
    → get_structured_llm(OutreachCampaignResult).invoke(prompt)
    → Returns {linkedin_message, cold_email_subject, cold_email_body}
  → OutreachAction.objects.create(
        campaign=campaign,
        target_handle=handle,
        target_name=name,
        kind='connect',
        payload=linkedin_message,
        status='queued',
    )

User reviews queued action, can edit message, then clicks "Save & Queue"


=== Drain phase (Extension does this autonomously) ===

Service worker alarm fires every 90s ± 20s

Service worker:
  → Check chrome.storage.local for paused_until
  → If paused, return (sleeping until cap reset)

  → POST <host>/profiles/api/outreach/next with token
  Server:
    → Validate token → User
    → Find oldest 'queued' action across user's running campaigns
    → Mark as 'in_flight'
    → Return {id, target_handle, target_name, kind, payload, selectors}

  → Find or open tab on LinkedIn profile
    chrome.tabs.query({url: `https://www.linkedin.com/in/${handle}/`})
    or chrome.tabs.create({url, active: true})

  → Inject content_linkedin.js
    chrome.scripting.executeScript({target: {tabId}, files: ['content_linkedin.js']})

content_linkedin.js performConnectWithNote(payload):
  1. waitForSelector('button[aria-label*="Connect"]', 5000)
     - Uses MutationObserver to wait for DOM
     - If not found, return {status: 'failed', error: 'selector_drift'}
  2. Click Connect; sleep jittered(500-1500ms)
  3. waitForSelector('button[aria-label*="Add a note"]', 3000)
  4. Click Add-a-note; sleep jittered(400-1200ms)
  5. waitForSelector('textarea[name="message"]', 3000)
  6. typeWithJitter(textarea, payload, chunk size 40-120ms)
  7. sleep jittered(500-1500ms)
  8. waitForSelector('button[aria-label*="Send"]', 3000)
  9. Click Send; sleep jittered(800-2400ms)
  10. waitForSelector('button[aria-label*="Pending"]', 5000)
      - Confirms send succeeded
      - Return {status: 'sent'}
  11. If no pending indicator, return {status: 'failed', error: 'no_confirmation'}

Service worker reports back:
  → POST <host>/profiles/api/outreach/result with token
    Body: {action_id, status, error?, evidence?}

Server:
  → action.status = result.status
  → action.completed_at = now()
  → if status == 'failed':
      action.attempts += 1
      action.last_error = result.error

If 429 returned (weekly cap hit):
  → chrome.storage.local.set({paused_until: now + 24h})


=== Audit / monitoring ===

User goes to /profiles/outreach/<job_id>/ status panel
  → Shows action counts by status
  → Shows discovery queue
  → Allows manual mark-as-sent / skip / cancel
  → Shows selector_drift errors prominently

If selector_drift seen 3+ times in a row:
  → Server flag campaign as 'paused' automatically
  → Show admin alert
```

The 90s ± 20s polling is deliberate. The full action takes 8–15 seconds (open tab, wait for DOM, type with jitter, send). Each user can drain ~30–40 actions per hour at this pace.

The `selector_drift` reporting is critical — when LinkedIn changes its DOM (it does, periodically), the script reports the failure rather than silently failing. The status panel surfaces these errors so the developer can update selectors.

---

# PART 18 — Security and Performance

## 98. Authentication (UUID + Email + JWT)

**User identification**: UUID primary keys (not auto-incrementing integers). Reasons:
- No information leakage (`/users/12345/` reveals user count; `/users/<uuid>/` doesn't).
- Distributable — UUIDs can be generated client-side without coordination.
- No collision risk for the foreseeable future.

**Login**: Email-based via `USERNAME_FIELD = 'email'` on the custom `User` model. Standard Django `authenticate()` + `login()` for session-cookie auth.

**Extension auth**: A separate `outreach_token: UUIDField(unique=True, db_index=True)` field. The extension sends `Authorization: Bearer <token>` and the API endpoints in `views_outreach_api.py` validate it against `User.outreach_token`. Method `User.rotate_outreach_token()` lets the user revoke a leaked token.

**JWT**: Configured in `REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']` but only used by `jobs/views.py:save_job_extension_view` (an early extension API endpoint that's no longer the primary auth path). The outreach extension uses the simpler bearer-token scheme.

**Password reset**: Django's built-in views (`PasswordResetView`, `PasswordResetConfirmView`, etc.) with custom templates. Email goes to console in dev; SMTP backend required for production.

**Password change**: Custom logic in `account_settings_view` validates current password, checks length ≥ 8, ensures new passwords match. Calls `update_session_auth_hash` to prevent logout after the change.

## 99. CSRF Protection and Custom Failure Page

Django's default CSRF middleware is enabled. Every POST form includes `{% csrf_token %}`. AJAX requests (e.g., the gap-analysis drag-and-drop) include the token via:

```javascript
fetch('/analysis/<job_id>/skills/', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': '{{ csrf_token }}',
    },
    body: JSON.stringify({...})
});
```

When CSRF fails (typically because the user's session expired or they have a stale tab open), Django would normally show a bare HTML "Forbidden (403) CSRF verification failed" page. Commit `10e3268` introduced a custom failure page:

```python
# settings.py
CSRF_FAILURE_VIEW = 'core.views.csrf_failure'
```

```python
# core/views.py
def csrf_failure(request, reason=""):
    logger = logging.getLogger(__name__)
    logger.warning(
        "CSRF verification failed: %s | path=%s | method=%s | referer=%s",
        reason, request.path, request.method,
        request.META.get('HTTP_REFERER', '-'),
    )
    return render(request, '403_csrf.html', {'reason': reason}, status=403)
```

The technical reason goes to the log, not to the user. The user sees a friendly "Your session expired — please refresh and try again" page with links back to home and login.

## 100. Database Connection (PgBouncer + SELECT 1 ping)

Connection settings:
```python
DATABASES['default'] = dj_database_url.config(
    default=os.getenv('DATABASE_URL'),
    conn_max_age=60,
    conn_health_checks=True,
)
DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'] = True
DATABASES['default']['OPTIONS'] = {'sslmode': 'require', 'connect_timeout': 10}
```

Why each setting:
- **`conn_max_age=60`** — Reuse TCP connections for up to 60 seconds. Without this, every request does a fresh TCP+TLS handshake (~300ms each) plus PgBouncer client-pool acquisition. Cold-handshake makes every request 2–11 seconds.
- **`conn_health_checks=True`** — Send a `SELECT 1` on connection reuse. PgBouncer transaction-mode kills idle connections; without this check, reused-but-dead connections throw `InterfaceError: connection already closed`.
- **`DISABLE_SERVER_SIDE_CURSORS=True`** — Required for PgBouncer transaction mode. Server-side cursors persist across transactions, which conflicts with PgBouncer's per-transaction routing.
- **`sslmode=require`** — Supabase enforces TLS.
- **`connect_timeout=10`** — Saturated pool fails fast (raises `OperationalError`) instead of hanging server boot.

Test mode swaps to in-memory SQLite to avoid PgBouncer's hold on connections that block `CREATE DATABASE test_smartcv`.

## 101. Latency SLOs and Observability

The `RequestObservabilityMiddleware` (defined in `core/middleware.py`) records every request. Per-route accumulators track latency over a rolling window. The middleware is deliberately last in the stack so it observes the final response after all other middleware processing.

**SLO targets** (informal, verified by Phase B benchmark):
- `/healthz/` — p95 ≤ 5ms.
- `/healthz/deep/` — p95 ≤ 20ms (cached 15s; rare cache misses spike higher).
- Profile/job/gap pages — p95 ≤ 15ms.
- LLM-bound endpoints (gap recompute, resume generate) — typically 2–5s, no SLO; UI shows loading state.

**Health endpoints**:
- `/healthz/` — Returns 200 if Python is alive. No DB hit.
- `/healthz/deep/` — Runs `SELECT 1`, cached 15s. Confirms DB connectivity.
- `/healthz/metrics` — JSON snapshot of all per-route stats: count, p50, p95, p99, max.

Metrics are in-memory only (per-worker). No persistence. For production-grade observability, integrating with Prometheus/Datadog would be straightforward (the middleware's `record()` could push to a metrics backend).

## 102. Anti-Abuse (Outreach Weekly Cap, Rate Limiting)

**Outreach weekly cap**: LinkedIn rate-limits connection requests at ~100/week. The extension respects this:
- The server tracks sent connection count over the last 7 days per user.
- When the count reaches `weekly_cap` (default 100), the API returns 429 on `/next` polls.
- The extension's service worker sets `paused_until = now + 24h` and stops polling.
- The popup shows "Paused — weekly cap reached. Resumes in 18 hours."

**Daily invite cap**: `OutreachCampaign.daily_invite_cap` (default 15). The dispatcher won't release more than 15 actions per (user, day).

**Rate limiting**: Django itself doesn't enforce request rate limits. The Groq API has its own rate limits (currently ~30 req/min for the free tier; ~120 req/min for paid). For SmartCV's solo-user workload, this is comfortably within limits.

**Brute force**: No Django-level lockout. Users can attempt to log in unlimited times. For a public-facing deployment, adding `django-axes` or a similar middleware would be a hardening step.

**CSRF**: Already covered. Standard Django middleware + custom failure page.

**SQL injection**: Django ORM parameterizes all queries. No raw SQL except in the migration `0006_migrate_data.py` which uses `connection.cursor()` for the JSONB conversion (parameterized).

**XSS**: Django templates auto-escape by default. The few places using `|safe` (e.g., the gap-analysis JSON injection into Alpine) are auditable and use server-controlled JSON.

**Secrets in logs**: The `csrf_failure` view logs the request path, referer, and CSRF reason. None of these contain secrets. The Groq API key is read from env and never logged.

---

# PART 19 — Appendices

## 103. Full File Index

### Top-level files

| File | Purpose | LOC |
|---|---|---|
| `manage.py` | Django entry | 22 |
| `requirements.txt` | Python deps | 21 |
| `package.json` | npm deps + scripts | 15 |
| `package-lock.json` | npm lock | ~32K |
| `.env.example` | Env template | 16 |
| `.gitignore` | Git exclusions | 30 |
| `.coveragerc` | Coverage config | ~25 |
| `CLAUDE.md` | Claude Code guidance | 98 |
| `LICENSE` | MIT | 21 |
| `README.md` | Public README | 151 |
| `create_superuser.py` | Helper script | ~15 |
| `run_dev.ps1` | Windows dev runner | ~30 |
| `ux_changelog.md` | UX changelog | varies |

### `accounts/` (8 files, ~280 lines)

| File | LOC |
|---|---|
| `models.py` | 22 |
| `views.py` | 91 |
| `urls.py` | 42 |
| `tests.py` | 106 |
| `admin.py` | 3 |
| `apps.py` | 6 |
| `migrations/0001_initial.py` | ~20 |
| `migrations/0002_user_outreach_token.py` | ~15 |

### `analysis/` (10 files, ~1100 lines)

| File | LOC |
|---|---|
| `models.py` | 22 |
| `views.py` | 305 |
| `urls.py` | 11 |
| `tasks.py` | ~30 |
| `tests.py` | ~600 |
| `services/gap_analyzer.py` | 424 |
| `services/learning_path_generator.py` | ~50 |
| `services/salary_negotiator.py` | ~50 |
| `services/skill_score.py` | ~25 |
| `migrations/*` | ~30 |

### `core/` (10 files, ~1500 lines)

| File | LOC |
|---|---|
| `views.py` | 257 |
| `urls.py` | 26 |
| `tests.py` | ~900 |
| `health.py` | ~60 |
| `metrics.py` | ~80 |
| `middleware.py` | ~50 |
| `context_processors.py` | ~20 |
| `services/agent_chat.py` | 313 |
| `services/action_planner.py` | 175 |
| `services/career_stage.py` | 233 |

### `jobs/` (12 files, ~1500 lines)

| File | LOC |
|---|---|
| `models.py` | 48 |
| `views.py` | 284 |
| `urls.py` | 11 |
| `tests.py` | ~250 |
| `services/skill_extractor.py` | 193 |
| `services/linkedin_scraper.py` | ~100 |
| `services/people_finder.py` | ~80 |
| `services/scrapers/base.py` | ~50 |
| `services/scrapers/dispatcher.py` | ~30 |
| `services/scrapers/linkedin.py` | ~150 |
| `services/scrapers/greenhouse.py` | ~80 |
| `services/scrapers/lever.py` | ~80 |
| `services/scrapers/indeed.py` | ~150 |
| `services/scrapers/generic.py` | ~100 |
| `migrations/*` | ~50 |

### `profiles/` (25 files, ~5000 lines — largest app)

| File | LOC |
|---|---|
| `models.py` | 213 |
| `views.py` | 849 |
| `views_outreach_api.py` | 337 |
| `urls.py` | 43 |
| `tests.py` | ~1500 |
| `tests_interviewer.py` | ~400 |
| `tests_outreach.py` | ~250 |
| `tests_prompt_guards.py` | ~120 |
| `services/llm_engine.py` | 87 |
| `services/schemas.py` | 223 |
| `services/cv_parser.py` | ~1000 |
| `services/llm_validator.py` | ~120 |
| `services/embeddings.py` | ~80 |
| `services/experience_math.py` | ~120 |
| `services/profile_strength.py` | ~250 |
| `services/interviewer.py` | ~200 |
| `services/outreach_generator.py` | ~150 |
| `services/outreach_dispatcher.py` | ~100 |
| `services/github_aggregator.py` | ~150 |
| `services/linkedin_aggregator.py` | ~100 |
| `services/scholar_aggregator.py` | ~120 |
| `services/kaggle_aggregator.py` | ~120 |
| `services/profile_auditor.py` | ~120 |
| `services/semantic_validator.py` | ~80 |
| `services/prompt_guards.py` | ~50 |

### `resumes/` (10 files, ~1300 lines)

| File | LOC |
|---|---|
| `models.py` | 30 |
| `views.py` | 445 |
| `urls.py` | 21 |
| `tests.py` | ~500 |
| `services/resume_generator.py` | 375 |
| `services/scoring.py` | 188 |
| `services/cover_letter_generator.py` | ~100 |
| `services/pdf_generator.py` | 79 |
| `services/pdf_exporter.py` | 28 |
| `templates/resumes/resume_template.html` | ~200 |

### `smartcv/` (4 files, ~300 lines)

| File | LOC |
|---|---|
| `settings.py` | 244 |
| `urls.py` | 28 |
| `wsgi.py` | ~10 |
| `asgi.py` | ~10 |

### `benchmarks/` (12 files, ~1500 lines)

| File | LOC |
|---|---|
| `_io.py` | ~80 |
| `run_all.py` | ~250 |
| `parser_eval.py` | ~200 |
| `skill_extractor_eval.py` | ~150 |
| `gap_eval.py` | ~200 |
| `ats_eval.py` | ~150 |
| `tailoring_eval.py` | ~250 |
| `latency_runner.py` | ~120 |
| `llm_judge.py` | ~120 |

### `extension-outreach/` (10 files, ~600 lines)

| File | LOC |
|---|---|
| `manifest.json` | 28 |
| `background.js` | ~150 |
| `content_discover.js` | ~100 |
| `content_linkedin.js` | ~250 |
| `popup.html` | ~50 |
| `popup.js` | ~50 |
| `options.html` | ~30 |
| `options.js` | ~30 |
| `README.md` | 36 |

### `templates/` (48 files)

Full inventory in PART 9.

### `static/`

| File | Lines |
|---|---|
| `static/src/input.css` | 140 |
| `static/css/output.css` | 3722 (compiled) |

### `docs/` (5 files + images)

| File | Purpose |
|---|---|
| `benchmarks.md` | Methodology + latest results |
| `gap_analysis_system.md` | Gap analyzer architecture |
| `implementation_plan.md` | 7-phase roadmap |
| `qa/manual-test-plan.md` | E2E QA scenarios |
| `qa/outreach-automation-test-plan.md` | Extension QA |
| `images/dashboard.png` | Dashboard screenshot |
| `images/gap-analysis.png` | Gap analysis screenshot |
| `images/outreach-campaign.png` | Outreach screenshot |
| `images/resume-editor.png` | Editor screenshot |

## 104. Glossary of Terms

- **ATS** — Applicant Tracking System. Software that scans resumes for keyword matches.
- **Cohen's d** — Statistical measure of effect size. >0.8 is "large effect."
- **CV** — Curriculum Vitae. Used interchangeably with "resume" in this codebase.
- **DRF** — Django REST Framework.
- **F1** — Harmonic mean of precision and recall.
- **GIN** — Generalized Inverted Index (Postgres). Fast for JSONB containment queries.
- **JD** — Job Description.
- **JSONB** — Postgres binary JSON column type. Indexable.
- **LLM** — Large Language Model.
- **LPU** — Language Processing Unit (Groq's hardware).
- **MV3** — Manifest Version 3 (Chrome extension API).
- **PgBouncer** — PostgreSQL connection pooler. Supabase uses transaction-mode pooling on port 6543.
- **pgvector** — PostgreSQL extension for vector similarity search.
- **PNA** — Private Network Access (Chrome security policy that blocks cross-origin requests to localhost from content scripts).
- **STAR** — Situation, Task, Action, Result. Resume bullet structure.
- **YoE** — Years of Experience.

## 105. Statistics Summary

| Metric | Value |
|---|---|
| Total commits | 168 |
| Active development window | 6.5 weeks |
| Total lines added (history) | ~52,000 |
| Total lines deleted | ~31,000 |
| Net Python LOC (excluding tests, migrations) | ~6,850 |
| Net HTML template LOC | ~5,200 |
| Tests | 337 |
| Test coverage overall | 53% |
| Test coverage core/ | 76.9% |
| Python deps (prod) | 21 |
| Python deps (dev-only) | 2 |
| npm deps | 2 |
| Django apps | 6 |
| Django models | 11 (User, UserProfile, JobProfileSnapshot, OutreachCampaign, OutreachAction, DiscoveredTarget, Job, RecommendedJob, GapAnalysis, GeneratedResume, CoverLetter) |
| Pydantic schemas | 25 |
| HTML templates | 48 |
| LLM model | Groq Llama-4-Scout (17B/16E) |
| Vector dimension | 384 |
| Benchmark fixtures | 50 (CV, JD) pairs |
| ATS scoring determinism | σ = 0 |
| Endpoint warm p95 (max) | ≤ 13 ms |
| CV parser personal-info accuracy | 0.94 |
| Skill extractor F1 | 0.81 |
| Skill extractor hallucination rate | 0.24 |
| Gap analyzer coverage | 0.999 |
| Gap analyzer Cohen's d (strong vs weak) | 1.59 |
| Tailored resume factuality (1-10) | 8.0 |
| Tailored resume relevance (1-10) | 6.8 |
| Tailored resume ATS fit (1-10) | 5.6 |
| Tailored resume human voice (1-10) | 5.6 |
| Programmatic entity grounding | 0.875 |

## 106. Notes on Future Work

Inferred from `docs/implementation_plan.md` and recent commits:

### Phase 6 — Reintroduce background workers
- `django-q2` (or Celery) for synchronous heavy operations:
  - Embedding generation (~10-20s).
  - Multi-source job enrichment.
  - Gap analysis pre-computation on profile change.
- Would unblock the request thread for these operations.

### Phase 7 — RecommendedJob auto-generation
- The `RecommendedJob` model exists but isn't populated.
- Pipeline:
  - User profile embedded.
  - Periodic scrape of new jobs from configured sources (LinkedIn job recommendations, public boards).
  - Cosine similarity ranking.
  - Top N saved as RecommendedJobs.
  - Surfaces on dashboard with match score.

### Outreach v3 candidates
- Direct messages (not just connect-with-note).
- Follow-up automation after-accept.
- Multi-tier campaign (initial connect → wait → message after acceptance).
- Email-channel outreach in parallel with LinkedIn.

### Multi-template support beyond resumes
- Cover letter style picker (currently single style).
- Email outreach templates (formal/casual/cold).

### Internationalization
- Translations for the major languages (Arabic, French, German, Spanish).
- Currently `LANGUAGE_CODE = 'en-us'` and only English content.

### Production deployment
- Procfile + Render/Railway/Fly.io setup.
- CI via GitHub Actions for tests + coverage.
- Sentry / Bugsnag integration.
- Real email backend (Postmark/SendGrid/SES).

### LinkedIn DOM-stability
- Extension's content script asserts selectors. Periodic updates needed.
- A "selector definitions" file the user can update without rebuilding the extension would reduce friction.

### Type safety
- Add `mypy` configuration.
- Annotate the most-used services first (`llm_engine`, `gap_analyzer`, `scoring`).

### Linting
- Add `ruff` config + pre-commit hook.
- Format with `black` or `ruff format`.

### Coverage push to 70%+
- The `profiles/services/cv_parser.py` has many uncovered edge cases.
- Aggregator services (github, scholar, kaggle) need integration tests.

---

# END OF DOCUMENT

**Document statistics**:
- Length: ~80–100 pages (depending on rendering).
- Sections: 19 parts, 106 numbered subsections.
- Coverage: Every Django app, every service, every model, every Pydantic schema, every commit, every benchmark phase, every template directory.
- Generated: 2026-04-26.
- Source of truth: `G:\New folder\SmartCV\` at commit `fe6ee8a`.

For any details not captured here, consult:
- `README.md` for the public-facing overview.
- `CLAUDE.md` for Claude Code guidance.
- `docs/benchmarks.md` for evaluation methodology.
- `docs/gap_analysis_system.md` for the gap analyzer's design.
- The source code itself — every file referenced here is in the repo.

End of SmartCV Complete Context Document.
