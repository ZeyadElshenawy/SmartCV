# CLAUDE.md

Guidance for Claude Code working in this repo. This is the **short** map; the deep reference is
[`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) — drill into it by section when you need detail.

## What SmartCV is

AI career assistant: upload a CV → paste/scrape a job → get a **two-phase gap analysis**, an
**ATS-scored tailored résumé**, and an **outreach campaign**. Django 5.2 monolith, Groq LLM,
PostgreSQL (Supabase + pgvector), Tailwind v4. Core bet: **the LLM proposes, deterministic Python
disposes** — every model output is reconciled, grounded, and number-checked in code.
→ [Pitch & overview](PROJECT_CONTEXT.md#0--the-60-second-pitch) · [System tiers](PROJECT_CONTEXT.md#1--system-overview)

## Commands

```bash
python manage.py runserver
python manage.py makemigrations && python manage.py migrate
python manage.py build_knowledge_index          # populate the RAG KnowledgeChunk corpus
npm run build:css                               # Tailwind v4 (output.css is committed; rebuild after template edits)
npm run dev:css                                 # Tailwind watch mode
python manage.py test                           # app tests (in-memory SQLite; no network)
python manage.py test profiles                  # single app
pytest                                          # tests/ dir only (services + integration replay)
python -m benchmarks.run_all                    # evaluation suite
```
No linter/formatter configured. → [Full dev setup](PROJECT_CONTEXT.md#12--config--environment)

## Architecture (6 apps)

- **accounts** — custom UUID `User` (`AUTH_USER_MODEL='accounts.User'`), email auth, outreach token.
- **profiles** — CV parsing, the JSONB **master profile** (`UserProfile.data_content`), chatbot,
  aggregators (GitHub/Scholar/Kaggle/LinkedIn), RAG, outreach, project enrichment.
- **jobs** — job input (URL/paste), tiered LLM skill extraction, discovery scrapers + ranking.
- **analysis** — the headline **two-phase gap analysis**, learning paths, salary, skill scoring.
- **resumes** — tailored résumé generation (v1 default + v2 flagged), ATS scorer, findings UX, exporters.
- **core** — landing/dashboard/insights, agent chat, career-stage logic, health/metrics middleware.

→ [Repository map](PROJECT_CONTEXT.md#3--repository-map) · [Data model + ER diagram](PROJECT_CONTEXT.md#4--the-data-model)

## Key data flow

1. CV upload → `profiles/services/cv_parser.py` (deterministic) → `llm_validator.py` structures into
   `ResumeSchema` → `UserProfile.data_content` (JSONB). → [§7.1](PROJECT_CONTEXT.md#71--cv-parsing--profilesservicescv_parserpy-deterministic--llm_validatorpy-llm)
2. Job → `jobs/services/skill_extractor.py` → `Job.extracted_skills_tiers`. → [§7.7](PROJECT_CONTEXT.md#77--job-discovery--two-distinct-scraper-systems)
3. Gap → `analysis/services/gap_analyzer.py`: Phase 1 LLM categorises, **Phase 2 deterministic
   reconciliation guarantees 100% JD-skill coverage** + grounds every match → `GapAnalysis` (cached).
   → [§7.2](PROJECT_CONTEXT.md#72--two-phase-gap-analysis--analysisservicesgap_analyzerpy-the-headline-feature)
4. Résumé → `resumes/services/pipeline_dispatch.py` → v1 `resume_generator.py` →
   `scoring.py` ATS (σ=0) → `GeneratedResume` (FKs **GapAnalysis**, not Job). → [§7.3](PROJECT_CONTEXT.md#73--resume-generation--resume_generatorpy-v1-and-resume__v2py-v2) · [§7.4](PROJECT_CONTEXT.md#74--deterministic-ats-scoring--resumesservicesscoringpy)

## The LLM hub

**Every** Groq call goes through `profiles/services/llm_engine.py`: `get_llm()` (text),
`get_structured_llm(Schema)` (Pydantic-validated), `get_llm_client()` (deprecated shim). Per-task key
rotation (`GROQ_API_KEY_<TASK>[2-4]` → global), TPM throttle + TPD key rotation, and
`AllGroqKeysExhausted` (loud, never a silent degrade). Model: Groq
`meta-llama/llama-4-scout-17b-16e-instruct` (`GROQ_MODEL`). Pydantic schemas live in
`profiles/services/schemas.py`. → [§5 hub](PROJECT_CONTEXT.md#5--the-llm-hub--per-task-routing) · [§6 schema catalog](PROJECT_CONTEXT.md#6--pydantic-schema-catalog)

## Conventions that bite

- **Model proposes, code disposes; ground before fluency; fail toward honesty.** → [§17](PROJECT_CONTEXT.md#17--conventions--design-philosophy)
- **Bullets are `List[str]`** in schemas; `resumes/views.py` converts list↔multiline for textareas
  (`_description_*` helpers; was a bug source — commit `fd90299`). `mode='before'` validators in
  `schemas.py` fold `highlights`/`achievements`/… into `description`.
- **Anti-AI-tell** is centralized in `profiles/services/prompt_guards.py` (`HUMAN_VOICE_RULE` +
  `BANNED_PHRASES`) and enforced deterministically in `resumes/services/bullet_validator.py`. → [§8](PROJECT_CONTEXT.md#8--the-anti-ai-tell--prompt-guard-system)
- **All LLM calls are synchronous** (django-q removed). The **only** thread is the Playwright job
  scraper (`jobs/services/job_sources/runner.py`) — don't generalize that pattern.
- **Tests use in-memory SQLite** (forced when `'test' in sys.argv`); a secure `SECRET_KEY` is
  required outside tests.
- **Front-end has NO custom JS files** — inline Alpine.js + Tailwind v4 CSS-first `@theme` (no
  `tailwind.config.js`). Legacy `rn-*` tokens coexist with `brand-*`/`accent-*`. → [§9](PROJECT_CONTEXT.md#9--front-end-architecture)

## Database

PostgreSQL via Supabase PgBouncer (port 6543): requires `DISABLE_SERVER_SIDE_CURSORS=True`,
`sslmode='require'`, `conn_max_age=60`. pgvector fields are **384-dim** (all-MiniLM-L6-v2); profile
vectors are dormant (LLM analysis replaced them). → [§4](PROJECT_CONTEXT.md#4--the-data-model)

## Gotchas & live state (read before trusting docs)

- **Production ships v1 résumé generation.** `RESUME_GENERATOR_PIPELINE=v1` (default) — the v2
  evidence-first stack (FactStore/fact_extractor/planner_v2/…) is real but **flag-gated OFF**.
  `SUPERVISOR_ENABLED=False` (vision supervisor dormant). → [§18](PROJECT_CONTEXT.md#18--known-issues-tech-debt--stubs)
- **Benchmark numbers conflict** across README / `docs/benchmarks.md` / latest `benchmarks/results/`
  JSON. The **newest JSON wins**; both docs lag. Latest (2026-06-06): gap Cohen's d 2.58, skill F1
  0.835, ATS σ=0. → [§14](PROJECT_CONTEXT.md#14--evaluation--benchmarks)
- **Test counts:** ~1,800 real `def test_` methods; README badges (337/398) are stale. The suite is
  **not always green** (`benchmarks/results/_test_failures.txt`). → [§15](PROJECT_CONTEXT.md#15--testing)
- `RAG_ENABLED` defaults `True` despite a comment saying `False`. `smartcv/settings_constants.py`
  describes an unused Gemini/768-dim stack — ignore it.
- Live scrapers (LinkedIn/Kaggle/Indeed/Glassdoor) break **silently** on site re-renders. → [§11](PROJECT_CONTEXT.md#11--external-integrations)

## Environment

Required in `.env`: `DATABASE_URL`, `GROQ_API_KEY`. Common optional: `GROQ_MODEL`,
`GROQ_API_KEY_<TASK>[2-4]`, `GITHUB_TOKEN`, `RAG_ENABLED`, `RESUME_GENERATOR_PIPELINE`,
`SUPERVISOR_ENABLED`, `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`. → [full table §12](PROJECT_CONTEXT.md#12--config--environment)

---
_See [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) for the exhaustive reference (data model, schema
catalog, every pipeline, benchmarks, file index). Regenerate per its [§21 Maintenance](PROJECT_CONTEXT.md#21--maintenance) note._
