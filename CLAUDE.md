# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SmartCV is an AI-powered career assistant that helps job seekers tailor CVs, analyze skill gaps, and generate ATS-optimized resumes. Built with Django 5.2+, PostgreSQL (Supabase with pgvector), Tailwind CSS, and Groq LLM (LangChain).

## Commands

```bash
# Run dev server
python manage.py runserver

# Database
python manage.py makemigrations
python manage.py migrate

# Tailwind CSS v4 — one-time build
npm run build:css
# Tailwind CSS v4 — watch mode (recommended during template work)
npm run dev:css

# Tests
python manage.py test                                              # all
python manage.py test profiles                                     # single app
python manage.py test profiles.tests.CVParserTest.test_pdf_extraction  # single test

# Interactive shell
python manage.py shell
```

No linter or formatter is configured.

## Architecture

### Django Apps

- **accounts** - Custom UUID-based User model (`AUTH_USER_MODEL = 'accounts.User'`), email-based auth
- **profiles** - CV upload/parsing, user profile (JSONB `data_content`), chatbot for profile building
- **jobs** - Job input (LinkedIn scraping or manual paste), LLM-based skill extraction
- **analysis** - Gap analysis engine (LLM + fuzzy reconciliation), learning paths, salary negotiation
- **resumes** - Tailored resume generation, cover letters, PDF export (xhtml2pdf)
- **core** - Landing page, error handlers

### Key Data Flow

1. User uploads CV (PDF/DOCX) -> `profiles/services/cv_parser.py` extracts text -> LLM structures into Pydantic `ResumeSchema` -> stored in `UserProfile.data_content` (JSONB)
2. User adds job (URL or text) -> `jobs/services/linkedin_scraper.py` + `skill_extractor.py` -> `Job.extracted_skills` (JSON array)
3. Gap analysis -> `analysis/services/gap_analyzer.py` sends full profile + job skills to LLM -> `GapAnalysisResult` Pydantic output -> Phase 2 fuzzy reconciliation ensures 100% skill coverage -> cached in `GapAnalysis` model
4. Resume generation -> LLM tailors profile for job -> `GeneratedResume.content` (JSON) -> preview/edit/PDF export

### LLM Integration

Central hub: `profiles/services/llm_engine.py`

- `get_llm()` - plain text generation (ChatGroq)
- `get_structured_llm(PydanticSchema)` - guaranteed Pydantic-validated output via `with_structured_output()`
- `get_llm_client()` - legacy shim mimicking OpenAI client API (deprecated, use `get_llm()` for new code)
- Model: Groq `meta-llama/llama-4-scout-17b-16e-instruct`, configurable via `GROQ_MODEL` env var
- Pydantic schemas live in `profiles/services/schemas.py`

All LLM calls are synchronous (django-q was removed). Typical latency is 2-3 seconds.

### Profile Data Storage

`UserProfile.data_content` is a single JSONB field storing the entire parsed CV. Property accessors (`profile.skills`, `profile.experiences`, etc.) provide backward compatibility. This avoids rigid per-section DB tables and preserves arbitrary CV sections.

`JobProfileSnapshot` stores per-job profile variants when users customize for a specific job, with `pre_chatbot_data` for rollback.

### Gap Analysis Reconciliation

Two-phase approach: (1) LLM categorizes skills as matched/missing/partial, (2) programmatic fuzzy matching (cutoff 0.85) ensures every job skill is accounted for. Key rules: holistic evidence matching across all CV sections, directional specificity (broad matches narrow, not vice versa), no duplicates, case-insensitive.

### Resume Editing: List/String Conversion

Schemas store descriptions as `List[str]` (bullet points). Views in `resumes/views.py` convert list->multiline string for textarea display and back on POST. This was a source of bugs (bracket notation corruption) -- see commit `fd90299`. The conversion is centralized in `_description_text_to_list` / `_description_list_to_text` helpers (tested in `resumes/tests.py`).

### Frontend Toolchain

Tailwind CSS v4 via the standalone CLI (`@tailwindcss/cli`), built from `static/src/input.css` to `static/css/output.css`. Config is CSS-first (`@theme { ... }` inside `input.css`) — there is no `tailwind.config.js`. Alpine.js is still loaded via CDN. Fonts: Inter (UI), Fraunces variable serif (display), IBM Plex Mono (code), loaded from Google Fonts.

The built `output.css` is committed so `python manage.py runserver` works without running npm. Rebuild after template changes (to refresh the content scan) with `npm run build:css`, or run `npm run dev:css` in a watcher while iterating.

Legacy `rn-*` color tokens (rn-blue, rn-navy, rn-gold, rn-pill radius) are preserved alongside the new `brand-*` / `accent-*` palette so the phased redesign doesn't break existing templates mid-flight.

## Database

PostgreSQL via Supabase PgBouncer (port 6543). Requires:
- `DISABLE_SERVER_SIDE_CURSORS = True`
- `sslmode = 'require'`

pgvector fields use 384 dimensions (all-MiniLM-L6-v2). Vector embeddings are largely deprecated in favor of pure LLM-based analysis.

## Environment Variables

Required in `.env`: `DATABASE_URL`, `GROQ_API_KEY`. Optional: `GROQ_MODEL`, `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`.
