# Job-Aware Agent Context — Design Spec

**Date:** 2026-04-15
**Feature:** #1 of 3 in the agent-enrichment arc (followed by profile-strength scoring, then proactive notifications)
**Status:** Design approved, pending user spec review

## Problem

The global career agent at `/agent/` currently has no job context. A user asking "how do I prep for my Stripe interview?" gets a generic answer because the system prompt only sees their profile, external signals, and application counts — not the specific job, its extracted skills, or the cached gap analysis verdict. Meanwhile, career-stage deep-links (commit `1a2dfb9`) already route users into job-specific tools with a known `job_id`, so the scoping signal is readily available — we just aren't using it.

## Goal

When a user lands on `/agent/?job=<id>` (or POSTs with a `job_id`), the agent receives a rich dossier about that job and can answer questions grounded in it. Bare `/agent/` behavior is unchanged.

## Non-goals

- Persisting chat history server-side (defer to a later cycle)
- An in-page job switcher / dropdown (defer; users re-navigate for now)
- Function/tool-calling so the agent can *do* things per-job (separate design)
- Touching the existing narrow `profiles.chatbot` (per-job interview prep) — left as-is

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│ Deep-link sources (dashboard chips, gap analysis, /applications/)
│   href="/agent/?job=<id>"                                     │
└──────────────┬────────────────────────────────────────────────┘
               │
               ▼
┌───────────────────────────────────────────────────────────────┐
│ agent_chat_view(request)                                      │
│  - reads ?job= query param                                    │
│  - validates Job ownership (redirect to /agent/ if foreign)   │
│  - passes job into template context                           │
└──────────────┬────────────────────────────────────────────────┘
               │
               ▼
┌───────────────────────────────────────────────────────────────┐
│ agent_chat.html (Alpine.js)                                   │
│  - scope pill: "Talking about: <company> · <title> ×"        │
│  - seed prompts swap when job-scoped                          │
│  - POSTs include job_id from hidden data                      │
└──────────────┬────────────────────────────────────────────────┘
               │  POST /agent/api/ {message, history, job_id?}
               ▼
┌───────────────────────────────────────────────────────────────┐
│ agent_chat_api(request)                                       │
│  - re-validates job_id ownership on every turn                │
│  - calls chat(user, history, message, job=...)                │
└──────────────┬────────────────────────────────────────────────┘
               │
               ▼
┌───────────────────────────────────────────────────────────────┐
│ core/services/agent_chat.py                                   │
│  - build_system_prompt(user, job=None)                        │
│  - _build_job_context_block(job)  [new]                       │
│  - chat(user, history, message, job=None)                     │
└───────────────────────────────────────────────────────────────┘
```

## Components

### 1. `core/services/agent_chat.py` — extend existing module

**`build_system_prompt(user, job=None) -> str`**
When `job` is provided, appends a `JOB CONTEXT` section to the existing prompt (after APPLICATIONS, before final instructions). When `job` is `None`, behavior unchanged.

**`_build_job_context_block(job) -> str`** *(new, private)*
Assembles the rich dossier. Gracefully degrades when sub-sections are missing.

Sub-sections included:
- **Header:** title, company, status, location (if set)
- **Required skills:** from `Job.extracted_skills`
- **Gap analysis** (if `GapAnalysis` cached for this job):
  - Overall match percentage
  - Matched / Partial / Missing skill lists
  - Recommendations field from the cached result, if populated (the `GapAnalysis` model's result JSON may or may not include this — emit only if present and non-empty)
- **Job-specific profile variant** (if `JobProfileSnapshot` exists):
  - One-line note: "A job-specific profile variant exists for this role (created <date>)"
  - List of field names that differ from the main profile (e.g., `summary`, `experiences[0].description`) — field names only, not values, to bound token cost
- **Artifacts generated for this job:**
  - Tailored resume: yes/no + last updated date
  - Cover letter: yes/no
  - Outreach drafts: count

If none of gap-analysis / snapshot / artifacts exist, those subsections are silently omitted.

**`chat(user, history, message, job=None) -> ChatResult`**
Passes `job` to `build_system_prompt`. No other behavior change.

### 2. `core/views.py` — extend existing views

**`agent_chat_view(request)`:**
- Read `request.GET.get('job')`
- If set, attempt `Job.objects.get(id=job_id, user=request.user)`
  - Not found / invalid UUID → `messages.warning(...)` + redirect to `/agent/`
  - Found → pass as `job` in template context
- Template context gains: `job` (Job or None), `job_id` (str or None), `seed_prompts` (list — varies by scope)

**`agent_chat_api(request)`:**
- Parse `job_id` from JSON body (optional field)
- If present, validate ownership → 403 on foreign/invalid, with error payload the client renders as a bubble
- Pass validated job to `chat(...)`

### 3. `templates/core/agent_chat.html` — extend existing template

- Add a scope pill directly under the page header:
  - General: `General career chat` (muted badge)
  - Job-scoped: `Talking about: <company> · <title>` with a clickable `×` linking to `/agent/`
- Alpine `x-data` gains `jobId: '{{ job_id|default_if_none:"" }}'`
- Fetch body includes `job_id: this.jobId || null`
- Seed prompts (passed from view):
  - General: existing seeds (What should I focus on? etc.)
  - Job-scoped: "How should I prep for this interview?", "What's my biggest gap on this role?", "Help me negotiate this offer", "Which of my projects best fits this role?"

### 4. Career-stage deep-link update (`core/services/career_stage.py`)

The `interviewing` secondary actions currently route to `/profiles/chatbot/<id>/`. Add a *complementary* action routing to `/agent/?job=<id>` labeled e.g. "Ask agent about this role" — keeps the narrow prep chatbot available while also surfacing the agent.

This change is scoped minimally: add one additional `StageAction` to the `interviewing` stage and optionally to `offer_in_hand` and `actively_applying`. Other stages unchanged.

## Data flow

1. User clicks dashboard chip → browser navigates to `/agent/?job=abc-123`
2. View validates `abc-123` belongs to user; 302 → `/agent/` with flash if not
3. Template renders with scope pill + job-specific seed prompts; Alpine state holds `jobId`
4. User types a message → POST `{message, history, job_id: "abc-123"}`
5. API re-validates ownership → calls `chat(user, history, message, job=Job(abc-123))`
6. `build_system_prompt(user, job)` emits the JOB CONTEXT block
7. LLM responds grounded in job dossier
8. Client appends to history; `jobId` persists across turns until tab close

## Error handling

| Scenario | Behavior |
|---|---|
| `?job=<invalid uuid>` | Redirect `/agent/` with `messages.warning("That job couldn't be found.")` |
| `?job=<foreign user's id>` | Same as above (don't leak existence) |
| POST `job_id` foreign/invalid | 403 with `{error: "Job not found."}`; client renders error bubble |
| Missing `GapAnalysis` / `JobProfileSnapshot` / artifacts | Subsection omitted; no error |
| LLM failure | Existing 502 path unchanged |
| User deletes job mid-conversation | Next POST returns 403; client shows error; user can navigate to `/agent/` |

## Testing

Add to `core/tests.py` (new test class `JobAwareAgentChatTests`):

- `test_system_prompt_includes_job_context_when_job_passed`
- `test_system_prompt_omits_job_context_when_no_job`
- `test_job_context_block_handles_missing_gap_analysis`
- `test_job_context_block_handles_missing_snapshot`
- `test_job_context_block_handles_job_with_no_artifacts`
- `test_job_context_block_includes_full_dossier_when_all_present`
- `test_view_redirects_on_foreign_job_param`
- `test_view_redirects_on_invalid_uuid`
- `test_view_passes_job_to_template_when_owned`
- `test_api_returns_403_on_foreign_job_id`
- `test_api_accepts_valid_job_id`
- `test_api_works_without_job_id_backwards_compatible`

Plus update `CareerStageSecondaryActionsTests` to assert the new "Ask agent" action on `interviewing` stage.

Target: 156 current tests → ~168 after this feature. Full suite must pass.

## Token-cost note

The rich JOB CONTEXT block adds up to ~1200 tokens to every chat turn when a job is scoped. User accepted this trade-off (conversation on 2026-04-15) for maximum answer quality. If conversation token usage becomes a concern, the block can be compressed (drop gap-analysis verbose recommendations first) without re-designing.

## Out of scope (future cycles)

- Feature #2: Profile-strength scoring (next up)
- Feature #3: Proactive agent notifications
- Chat persistence (`ChatThread` / `ChatMessage` models)
- In-page job switcher
- Function-calling / agent-initiated actions
