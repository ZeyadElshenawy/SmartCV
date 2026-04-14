# Job-Aware Agent Context — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `/agent/` chat job-aware — when reached via `/agent/?job=<id>` (or POSTed with a `job_id`), the agent receives a rich dossier about that job and grounds its replies in it.

**Architecture:** Extend `core/services/agent_chat.py` with an optional `job` parameter that threads through `build_system_prompt` and `chat`. Views (`agent_chat_view`, `agent_chat_api`) validate ownership of the `job_id` and pass the `Job` instance through. Template carries the `jobId` in Alpine state and sends it on every POST; it also displays a scope pill and swaps seed prompts when job-scoped. Career-stage `interviewing` secondary actions gain an "Ask agent about this role" chip.

**Tech Stack:** Django 5.2, PostgreSQL (Supabase), Alpine.js, Tailwind CSS v4, Groq LLM via LangChain. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-15-job-aware-agent-context-design.md`

---

## File Structure

**Create:** none

**Modify:**
- `core/services/agent_chat.py` — add `_build_job_context_block(job)`; extend `build_system_prompt(user, job=None)` and `chat(user, history, user_message, job=None)`
- `core/views.py` — extend `agent_chat_view` (read `?job=`, validate, pass to template) and `agent_chat_api` (read `job_id`, validate, pass to chat)
- `templates/core/agent_chat.html` — scope pill under header, job-scoped seed prompts, `jobId` in Alpine state + POST body
- `core/services/career_stage.py` — add an "Ask agent about this role" secondary action on `interviewing` stage
- `core/tests.py` — add `JobAwareAgentChatTests` (prompt + view + API) and extend `CareerStageSecondaryActionsTests`

**Test:** all changes land in `core/tests.py` (existing convention).

---

## Task 1: Add `_build_job_context_block` header (title / company / status / skills)

**Files:**
- Modify: `core/services/agent_chat.py`
- Test: `core/tests.py` (new class `JobContextBlockTests`)

- [ ] **Step 1: Write the failing test for the base header**

Append this new test class to `core/tests.py` (below `AgentChatApiTests`):

```python
class JobContextBlockTests(TestCase):
    """_build_job_context_block — renders a rich dossier for a single job."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(
            username='j@example.com', email='j@example.com', password='x'
        )

    def _make_job(self, **kwargs):
        from jobs.models import Job
        defaults = dict(
            user=self.user,
            title='Senior SWE',
            company='Stripe',
            description='Build scalable payment infra.',
            extracted_skills=['Python', 'Go', 'Kubernetes'],
            application_status='interviewing',
        )
        defaults.update(kwargs)
        return Job.objects.create(**defaults)

    def test_header_includes_title_company_status_and_skills(self):
        from core.services.agent_chat import _build_job_context_block
        job = self._make_job()
        block = _build_job_context_block(job)
        self.assertIn('Senior SWE', block)
        self.assertIn('Stripe', block)
        self.assertIn('interviewing', block)
        self.assertIn('Python', block)
        self.assertIn('Go', block)
        self.assertIn('Kubernetes', block)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.JobContextBlockTests.test_header_includes_title_company_status_and_skills -v 2`
Expected: FAIL (`ImportError: cannot import name '_build_job_context_block'`).

- [ ] **Step 3: Implement the base `_build_job_context_block`**

In `core/services/agent_chat.py`, add this function immediately after `_applications_summary` (around line 142, before `build_system_prompt`):

```python
def _build_job_context_block(job) -> str:
    """Rich per-job dossier for the system prompt.

    Assembles: header (title, company, status, required skills), gap
    analysis result, job-specific profile snapshot diff, and artifacts.
    Missing subsections are silently omitted.
    """
    lines: list[str] = []

    title = getattr(job, 'title', None) or '(untitled role)'
    company = getattr(job, 'company', None) or '(unknown company)'
    status = getattr(job, 'application_status', None) or 'saved'
    lines.append(f"- Role: {title} at {company}")
    lines.append(f"- Application status: {status}")

    skills = getattr(job, 'extracted_skills', None) or []
    if skills:
        lines.append(f"- Required skills: {', '.join(str(s) for s in skills)}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test core.tests.JobContextBlockTests.test_header_includes_title_company_status_and_skills -v 2`
Expected: PASS (1 test in ~1–2s).

- [ ] **Step 5: Commit**

```bash
git add core/services/agent_chat.py core/tests.py
git commit -m "feat(agent): _build_job_context_block — base header for job dossier"
```

---

## Task 2: Add gap analysis subsection to the dossier

**Files:**
- Modify: `core/services/agent_chat.py`
- Test: `core/tests.py` (extend `JobContextBlockTests`)

- [ ] **Step 1: Write failing tests — gap present and gap missing**

Append to `JobContextBlockTests`:

```python
    def test_includes_gap_analysis_when_present(self):
        from analysis.models import GapAnalysis
        from core.services.agent_chat import _build_job_context_block
        job = self._make_job()
        GapAnalysis.objects.create(
            job=job, user=self.user,
            matched_skills=['Python'],
            partial_skills=['Go'],
            missing_skills=['Kubernetes'],
            similarity_score=0.67,
        )
        block = _build_job_context_block(job)
        self.assertIn('Gap analysis', block)
        self.assertIn('67%', block)  # similarity_score rendered as percent
        self.assertIn('Matched: Python', block)
        self.assertIn('Partial: Go', block)
        self.assertIn('Missing: Kubernetes', block)

    def test_omits_gap_section_when_no_analysis_cached(self):
        from core.services.agent_chat import _build_job_context_block
        job = self._make_job()
        block = _build_job_context_block(job)
        self.assertNotIn('Gap analysis', block)
```

- [ ] **Step 2: Run tests — confirm both fail**

Run: `python manage.py test core.tests.JobContextBlockTests -v 2`
Expected: `test_includes_gap_analysis_when_present` FAILS (assertion on `'Gap analysis'`), `test_omits_gap_section_when_no_analysis_cached` PASSES (nothing emitted yet).

- [ ] **Step 3: Extend `_build_job_context_block` with the gap subsection**

Add inside `_build_job_context_block`, after the skills block and before `return`:

```python
    # Gap analysis — fetch lazily to keep the function import-cheap.
    try:
        from analysis.models import GapAnalysis
        gap = GapAnalysis.objects.filter(job=job, user=getattr(job, 'user', None)).order_by('-created_at').first()
    except Exception:
        gap = None

    if gap is not None:
        pct = int(round((gap.similarity_score or 0.0) * 100))
        lines.append("")
        lines.append(f"Gap analysis (overall match: {pct}%):")
        matched = gap.matched_skills or []
        partial = gap.partial_skills or []
        missing = gap.missing_skills or []
        if matched:
            lines.append(f"- Matched: {', '.join(str(s) for s in matched)}")
        if partial:
            lines.append(f"- Partial: {', '.join(str(s) for s in partial)}")
        if missing:
            lines.append(f"- Missing: {', '.join(str(s) for s in missing)}")
```

- [ ] **Step 4: Run tests — confirm both pass**

Run: `python manage.py test core.tests.JobContextBlockTests -v 2`
Expected: PASS on both `test_includes_gap_analysis_when_present` and `test_omits_gap_section_when_no_analysis_cached`.

- [ ] **Step 5: Commit**

```bash
git add core/services/agent_chat.py core/tests.py
git commit -m "feat(agent): job dossier — gap analysis subsection"
```

---

## Task 3: Add snapshot (job-specific profile variant) subsection

**Files:**
- Modify: `core/services/agent_chat.py`
- Test: `core/tests.py` (extend `JobContextBlockTests`)

- [ ] **Step 1: Write failing tests — snapshot present and absent**

Append to `JobContextBlockTests`:

```python
    def test_includes_snapshot_note_when_present(self):
        from profiles.models import UserProfile, JobProfileSnapshot
        from core.services.agent_chat import _build_job_context_block
        profile = UserProfile.objects.create(
            user=self.user, full_name='J',
            data_content={'summary': 'Original', 'skills': [{'name': 'Python'}]},
        )
        job = self._make_job()
        JobProfileSnapshot.objects.create(
            profile=profile, job=job,
            data_content={'summary': 'Tailored for Stripe', 'skills': [{'name': 'Python'}, {'name': 'Go'}]},
            pre_chatbot_data={'summary': 'Original', 'skills': [{'name': 'Python'}]},
        )
        block = _build_job_context_block(job)
        self.assertIn('Job-specific profile variant', block)
        self.assertIn('summary', block)
        self.assertIn('skills', block)

    def test_omits_snapshot_section_when_absent(self):
        from core.services.agent_chat import _build_job_context_block
        job = self._make_job()
        block = _build_job_context_block(job)
        self.assertNotIn('Job-specific profile variant', block)
```

- [ ] **Step 2: Run tests — confirm the present-case fails**

Run: `python manage.py test core.tests.JobContextBlockTests -v 2`
Expected: `test_includes_snapshot_note_when_present` FAILS; the absent-case passes.

- [ ] **Step 3: Extend with snapshot subsection**

Add inside `_build_job_context_block`, after the gap block and before `return`:

```python
    # Job-specific profile snapshot — list field names that differ from pre-chatbot state.
    try:
        from profiles.models import JobProfileSnapshot
        snap = JobProfileSnapshot.objects.filter(job=job).first()
    except Exception:
        snap = None

    if snap is not None:
        pre = snap.pre_chatbot_data or {}
        cur = snap.data_content or {}
        changed = sorted([k for k in set(list(pre.keys()) + list(cur.keys())) if pre.get(k) != cur.get(k)])
        lines.append("")
        lines.append("Job-specific profile variant exists for this role.")
        if changed:
            lines.append(f"- Fields tailored vs. master profile: {', '.join(changed)}")
```

- [ ] **Step 4: Run tests — confirm both snapshot tests pass**

Run: `python manage.py test core.tests.JobContextBlockTests -v 2`
Expected: PASS on all 5 JobContextBlockTests.

- [ ] **Step 5: Commit**

```bash
git add core/services/agent_chat.py core/tests.py
git commit -m "feat(agent): job dossier — snapshot variant subsection"
```

---

## Task 4: Add artifacts subsection (tailored resume + cover letter)

**Files:**
- Modify: `core/services/agent_chat.py`
- Test: `core/tests.py` (extend `JobContextBlockTests`)

- [ ] **Step 1: Write failing tests — artifacts present, cover-letter-only, none**

Append to `JobContextBlockTests`:

```python
    def test_includes_artifacts_when_resume_and_cover_letter_exist(self):
        from analysis.models import GapAnalysis
        from resumes.models import GeneratedResume, CoverLetter
        from profiles.models import UserProfile
        from core.services.agent_chat import _build_job_context_block
        profile = UserProfile.objects.create(user=self.user, full_name='J')
        job = self._make_job()
        gap = GapAnalysis.objects.create(job=job, user=self.user, similarity_score=0.5)
        GeneratedResume.objects.create(gap_analysis=gap, name='v1', content={})
        CoverLetter.objects.create(job=job, profile=profile, content='Dear Stripe, ...')
        block = _build_job_context_block(job)
        self.assertIn('Artifacts for this job', block)
        self.assertIn('Tailored resume: yes', block)
        self.assertIn('Cover letter: yes', block)

    def test_omits_artifacts_section_when_none_exist(self):
        from core.services.agent_chat import _build_job_context_block
        job = self._make_job()
        block = _build_job_context_block(job)
        self.assertNotIn('Artifacts for this job', block)
```

- [ ] **Step 2: Run tests — confirm present-case fails, absent-case passes**

Run: `python manage.py test core.tests.JobContextBlockTests -v 2`
Expected: `test_includes_artifacts_when_resume_and_cover_letter_exist` FAILS.

- [ ] **Step 3: Extend with artifacts subsection**

Add inside `_build_job_context_block`, after the snapshot block and before `return`:

```python
    # Artifacts generated for this job.
    resume_exists = False
    resume_updated = None
    cover_exists = False
    try:
        from resumes.models import GeneratedResume, CoverLetter
        resume = (GeneratedResume.objects
                  .filter(gap_analysis__job=job, gap_analysis__user=getattr(job, 'user', None))
                  .order_by('-created_at').first())
        if resume is not None:
            resume_exists = True
            resume_updated = resume.created_at
        cover = CoverLetter.objects.filter(job=job).order_by('-created_at').first()
        if cover is not None:
            cover_exists = True
    except Exception:
        pass

    if resume_exists or cover_exists:
        lines.append("")
        lines.append("Artifacts for this job:")
        if resume_exists:
            stamp = resume_updated.strftime('%Y-%m-%d') if resume_updated else 'unknown date'
            lines.append(f"- Tailored resume: yes (last updated {stamp})")
        else:
            lines.append("- Tailored resume: no")
        lines.append(f"- Cover letter: {'yes' if cover_exists else 'no'}")
```

- [ ] **Step 4: Run tests — confirm all JobContextBlockTests pass**

Run: `python manage.py test core.tests.JobContextBlockTests -v 2`
Expected: PASS on all 7 tests.

- [ ] **Step 5: Commit**

```bash
git add core/services/agent_chat.py core/tests.py
git commit -m "feat(agent): job dossier — artifacts subsection"
```

---

## Task 5: Extend `build_system_prompt` to accept an optional `job`

**Files:**
- Modify: `core/services/agent_chat.py` — `build_system_prompt(user, job=None)`
- Test: `core/tests.py` (new class `BuildSystemPromptWithJobTests`)

- [ ] **Step 1: Write failing tests**

Append to `core/tests.py`:

```python
class BuildSystemPromptWithJobTests(TestCase):
    """build_system_prompt gains an optional job parameter."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from profiles.models import UserProfile
        self.user = get_user_model().objects.create_user(
            username='bsp@example.com', email='bsp@example.com', password='x'
        )
        UserProfile.objects.create(
            user=self.user, full_name='Jane',
            data_content={'skills': [{'name': 'Python'}]},
        )

    def test_prompt_without_job_omits_job_context_section(self):
        from core.services.agent_chat import build_system_prompt
        prompt = build_system_prompt(self.user)
        self.assertNotIn('TALKING ABOUT JOB', prompt)

    def test_prompt_with_job_includes_job_context_section(self):
        from jobs.models import Job
        from core.services.agent_chat import build_system_prompt
        job = Job.objects.create(
            user=self.user, title='ML Eng', company='Stripe',
            description='x', extracted_skills=['Python'],
            application_status='interviewing',
        )
        prompt = build_system_prompt(self.user, job=job)
        self.assertIn('TALKING ABOUT JOB', prompt)
        self.assertIn('ML Eng', prompt)
        self.assertIn('Stripe', prompt)
```

- [ ] **Step 2: Run — confirm second test fails, first passes**

Run: `python manage.py test core.tests.BuildSystemPromptWithJobTests -v 2`
Expected: `test_prompt_with_job_includes_job_context_section` FAILS (build_system_prompt rejects the `job` kwarg), first passes.

- [ ] **Step 3: Extend `build_system_prompt` signature and body**

In `core/services/agent_chat.py`, replace the signature and the section-building portion of `build_system_prompt`. Find:

```python
def build_system_prompt(user) -> str:
```

Replace with:

```python
def build_system_prompt(user, job=None) -> str:
```

Then inside, locate the line that sets `sections = [...]` and add a job-context section at the end of the `if profile is None:` / `else:` branch. The full updated body:

```python
    from profiles.models import UserProfile
    try:
        profile = UserProfile.objects.get(user=user)
    except UserProfile.DoesNotExist:
        profile = None

    if profile is None:
        context_block = "CONTEXT: The user hasn't built a profile yet. Ask what they're working toward and suggest they upload a CV when the moment fits."
    else:
        sections = [f"CANDIDATE PROFILE:\n{_profile_summary(profile)}"]
        signals = _signals_summary(profile)
        if signals:
            sections.append(f"EXTERNAL SIGNALS (use as evidence):\n{signals}")
        apps = _applications_summary(user)
        if apps:
            sections.append(f"APPLICATIONS:\n{apps}")
        if job is not None:
            sections.append(f"TALKING ABOUT JOB:\n{_build_job_context_block(job)}")
        context_block = "\n\n".join(sections)
```

Leave the returned prompt string exactly as-is.

- [ ] **Step 4: Run — confirm both tests pass**

Run: `python manage.py test core.tests.BuildSystemPromptWithJobTests -v 2`
Expected: PASS on both.

- [ ] **Step 5: Regression run — existing prompt tests should still pass**

Run: `python manage.py test core.tests.AgentChatSystemPromptTests -v 2`
Expected: all existing tests PASS (no `job` argument means unchanged behavior).

- [ ] **Step 6: Commit**

```bash
git add core/services/agent_chat.py core/tests.py
git commit -m "feat(agent): build_system_prompt accepts optional job for scoped context"
```

---

## Task 6: Thread `job` through `chat()`

**Files:**
- Modify: `core/services/agent_chat.py` — `chat(user, history, user_message, job=None)`
- Test: verified indirectly through Task 8 API tests; no new unit test here.

- [ ] **Step 1: Update `chat()` signature and forward `job`**

Find in `core/services/agent_chat.py`:

```python
def chat(user, history: list[ChatTurn], user_message: str) -> ChatResult:
```

Replace with:

```python
def chat(user, history: list[ChatTurn], user_message: str, job=None) -> ChatResult:
```

Then find inside the function body:

```python
    system_prompt = build_system_prompt(user)
```

Replace with:

```python
    system_prompt = build_system_prompt(user, job=job)
```

- [ ] **Step 2: Run regression suite — existing API tests must still pass**

Run: `python manage.py test core.tests.AgentChatApiTests -v 2`
Expected: all 5 existing API tests PASS (the signature change is backwards-compatible; `job` defaults to `None`).

- [ ] **Step 3: Commit**

```bash
git add core/services/agent_chat.py
git commit -m "feat(agent): chat() threads optional job to system prompt"
```

---

## Task 7: `agent_chat_view` reads `?job=` and validates ownership

**Files:**
- Modify: `core/views.py` — `agent_chat_view`
- Test: `core/tests.py` (new class `AgentChatViewJobTests`)

- [ ] **Step 1: Write failing tests**

Append to `core/tests.py`:

```python
class AgentChatViewJobTests(TestCase):
    """GET /agent/?job=<id> — validates ownership, injects job into template."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(
            username='av@example.com', email='av@example.com', password='x'
        )
        self.other = get_user_model().objects.create_user(
            username='other@example.com', email='other@example.com', password='x'
        )
        self.client.force_login(self.user)

    def _make_job(self, user, company='Stripe'):
        from jobs.models import Job
        return Job.objects.create(
            user=user, title='SWE', company=company,
            description='x', extracted_skills=['Python'],
            application_status='interviewing',
        )

    def test_no_job_param_renders_general_chat(self):
        resp = self.client.get(reverse('agent_chat'))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context.get('job'))

    def test_valid_owned_job_id_passes_job_to_template(self):
        job = self._make_job(self.user)
        resp = self.client.get(reverse('agent_chat') + f'?job={job.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context.get('job').id, job.id)
        self.assertEqual(str(resp.context.get('job_id')), str(job.id))

    def test_foreign_job_id_redirects_to_agent(self):
        foreign_job = self._make_job(self.other)
        resp = self.client.get(reverse('agent_chat') + f'?job={foreign_job.id}')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('agent_chat'))

    def test_invalid_uuid_redirects_to_agent(self):
        resp = self.client.get(reverse('agent_chat') + '?job=not-a-uuid')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('agent_chat'))
```

- [ ] **Step 2: Run — confirm last three fail, first passes**

Run: `python manage.py test core.tests.AgentChatViewJobTests -v 2`
Expected: `test_no_job_param_renders_general_chat` PASSES; the other 3 FAIL (the view doesn't read `?job=` yet).

- [ ] **Step 3: Update `agent_chat_view`**

In `core/views.py`, replace the current `agent_chat_view` function entirely. Find:

```python
@login_required
def agent_chat_view(request):
    """General agent chat — not tied to a specific job. Users talk career
    strategy with an agent that already knows their profile + signals."""
    return render(request, 'core/agent_chat.html')
```

Replace with:

```python
@login_required
def agent_chat_view(request):
    """General agent chat — not tied to a specific job by default.

    When reached via ``/agent/?job=<id>``, the agent is scoped to that job
    and receives a rich dossier (gap analysis, snapshot, artifacts) in the
    system prompt. Foreign or malformed job ids redirect back to the
    general chat with a user-facing warning.
    """
    import uuid as _uuid
    from django.contrib import messages
    from jobs.models import Job

    job = None
    raw = request.GET.get('job')
    if raw:
        try:
            _uuid.UUID(str(raw))
        except (ValueError, TypeError):
            messages.warning(request, "That job couldn't be found.")
            return redirect('agent_chat')
        job = Job.objects.filter(id=raw, user=request.user).first()
        if job is None:
            messages.warning(request, "That job couldn't be found.")
            return redirect('agent_chat')

    return render(request, 'core/agent_chat.html', {
        'job': job,
        'job_id': str(job.id) if job else None,
    })
```

- [ ] **Step 4: Run — all four tests should pass**

Run: `python manage.py test core.tests.AgentChatViewJobTests -v 2`
Expected: PASS on all 4.

- [ ] **Step 5: Commit**

```bash
git add core/views.py core/tests.py
git commit -m "feat(agent): view reads ?job= param, validates ownership"
```

---

## Task 8: `agent_chat_api` accepts `job_id`, validates, forwards to `chat()`

**Files:**
- Modify: `core/views.py` — `agent_chat_api`
- Test: `core/tests.py` (new class `AgentChatApiJobTests`)

- [ ] **Step 1: Write failing tests**

Append to `core/tests.py`:

```python
class AgentChatApiJobTests(TestCase):
    """POST /agent/api/ with job_id — validates ownership, forwards job to chat()."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(
            username='api@example.com', email='api@example.com', password='x'
        )
        self.other = get_user_model().objects.create_user(
            username='other@example.com', email='other@example.com', password='x'
        )
        self.client.force_login(self.user)

    def _make_job(self, user, company='Stripe'):
        from jobs.models import Job
        return Job.objects.create(
            user=user, title='SWE', company=company,
            description='x', extracted_skills=['Python'],
            application_status='interviewing',
        )

    def _post(self, body):
        import json as _j
        return self.client.post(
            reverse('agent_chat_api'),
            data=_j.dumps(body),
            content_type='application/json',
        )

    def test_valid_job_id_forwards_job_to_chat(self):
        from unittest.mock import patch, MagicMock
        job = self._make_job(self.user)
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = MagicMock(content='scoped reply')
        with patch('profiles.services.llm_engine.get_llm', return_value=fake_llm), \
             patch('core.views.chat', wraps=__import__('core.services.agent_chat', fromlist=['chat']).chat) as spy:
            resp = self._post({'history': [], 'message': 'Prep me.', 'job_id': str(job.id)})
        self.assertEqual(resp.status_code, 200)
        # chat() should have been called with the Job instance as the `job` kwarg.
        kwargs = spy.call_args.kwargs if spy.call_args else {}
        self.assertIsNotNone(kwargs.get('job'))
        self.assertEqual(kwargs['job'].id, job.id)

    def test_foreign_job_id_returns_403(self):
        foreign = self._make_job(self.other)
        resp = self._post({'history': [], 'message': 'Hi', 'job_id': str(foreign.id)})
        self.assertEqual(resp.status_code, 403)
        self.assertIn('error', resp.json())

    def test_invalid_job_id_returns_403(self):
        resp = self._post({'history': [], 'message': 'Hi', 'job_id': 'not-a-uuid'})
        self.assertEqual(resp.status_code, 403)

    def test_missing_job_id_is_backwards_compatible(self):
        from unittest.mock import patch, MagicMock
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = MagicMock(content='general reply')
        with patch('profiles.services.llm_engine.get_llm', return_value=fake_llm):
            resp = self._post({'history': [], 'message': 'Hi'})
        self.assertEqual(resp.status_code, 200)
```

- [ ] **Step 2: Run — confirm the first three fail, the fourth passes**

Run: `python manage.py test core.tests.AgentChatApiJobTests -v 2`
Expected: `test_missing_job_id_is_backwards_compatible` PASSES; the other three FAIL.

- [ ] **Step 3: Update `agent_chat_api`**

In `core/views.py`, replace the entire `agent_chat_api` function. Find:

```python
@login_required
def agent_chat_api(request):
    """POST API used by the agent-chat page.

    Body: JSON { history: [{role, content}, ...], message: "..." }
    Returns { reply, error }.
    """
    if request.method != 'POST':
        from django.http import JsonResponse
        return JsonResponse({'error': 'POST only'}, status=405)

    import json
    from django.http import JsonResponse
    from .services.agent_chat import chat

    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)

    history = payload.get('history') or []
    if not isinstance(history, list):
        history = []
    message = (payload.get('message') or '').strip()
    if not message:
        return JsonResponse({'error': 'Empty message.'}, status=400)

    result = chat(request.user, history, message)
    if result.get('error'):
        return JsonResponse({'error': result['error']}, status=502)
    return JsonResponse({'reply': result['reply']})
```

Replace with:

```python
@login_required
def agent_chat_api(request):
    """POST API used by the agent-chat page.

    Body: JSON { history: [{role, content}, ...], message: "...", job_id?: "<uuid>" }
    Returns { reply } on success, { error } on failure.

    When ``job_id`` is present, it must belong to the authenticated user
    (otherwise 403) and the matching Job is forwarded to the chat service
    so the agent's system prompt includes the job's dossier.
    """
    if request.method != 'POST':
        from django.http import JsonResponse
        return JsonResponse({'error': 'POST only'}, status=405)

    import json
    import uuid as _uuid
    from django.http import JsonResponse
    from jobs.models import Job
    from .services.agent_chat import chat

    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)

    history = payload.get('history') or []
    if not isinstance(history, list):
        history = []
    message = (payload.get('message') or '').strip()
    if not message:
        return JsonResponse({'error': 'Empty message.'}, status=400)

    job = None
    raw_job_id = payload.get('job_id')
    if raw_job_id:
        try:
            _uuid.UUID(str(raw_job_id))
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Job not found.'}, status=403)
        job = Job.objects.filter(id=raw_job_id, user=request.user).first()
        if job is None:
            return JsonResponse({'error': 'Job not found.'}, status=403)

    result = chat(request.user, history, message, job=job)
    if result.get('error'):
        return JsonResponse({'error': result['error']}, status=502)
    return JsonResponse({'reply': result['reply']})
```

Note: the test at Step 1 patches `core.views.chat`. Make sure `chat` is imported into the `core.views` module namespace as `from .services.agent_chat import chat` — the local `import` inside the function places it on the local namespace per call but *also* matches the `patch('core.views.chat', ...)` target only if a module-level import exists. To cover both, also add a top-of-file import.

In `core/views.py`, at the top of the file (after the existing `from django.contrib.auth.decorators import login_required`), add:

```python
from .services.agent_chat import chat
```

Then remove the duplicate `from .services.agent_chat import chat` line from inside `agent_chat_api`.

- [ ] **Step 4: Run — all four API tests pass**

Run: `python manage.py test core.tests.AgentChatApiJobTests -v 2`
Expected: PASS on all 4.

- [ ] **Step 5: Regression run — original API tests still pass**

Run: `python manage.py test core.tests.AgentChatApiTests -v 2`
Expected: all 5 original tests PASS.

- [ ] **Step 6: Commit**

```bash
git add core/views.py core/tests.py
git commit -m "feat(agent): API accepts job_id, forwards Job to chat()"
```

---

## Task 9: Template — scope pill, job-scoped seeds, `jobId` in POST body

**Files:**
- Modify: `templates/core/agent_chat.html`
- Test: `core/tests.py` (extend `AgentChatViewJobTests` with template-content assertions)

- [ ] **Step 1: Write failing template-content tests**

Append to class `AgentChatViewJobTests` in `core/tests.py`:

```python
    def test_scope_pill_absent_in_general_chat(self):
        resp = self.client.get(reverse('agent_chat'))
        self.assertNotContains(resp, 'Talking about:')

    def test_scope_pill_renders_for_owned_job(self):
        job = self._make_job(self.user, company='Stripe')
        resp = self.client.get(reverse('agent_chat') + f'?job={job.id}')
        self.assertContains(resp, 'Talking about:')
        self.assertContains(resp, 'Stripe')
        # A dismiss link returning to general chat.
        self.assertContains(resp, 'href="' + reverse('agent_chat') + '"')

    def test_job_scoped_template_includes_jobId_in_alpine_state(self):
        job = self._make_job(self.user)
        resp = self.client.get(reverse('agent_chat') + f'?job={job.id}')
        # The template seeds Alpine with the job id for POST bodies.
        self.assertContains(resp, f"jobId: '{job.id}'")
```

- [ ] **Step 2: Run — confirm the three new tests fail**

Run: `python manage.py test core.tests.AgentChatViewJobTests -v 2`
Expected: the three new tests FAIL (scope pill and jobId state don't exist yet).

- [ ] **Step 3: Update the template**

In `templates/core/agent_chat.html`:

**3a. Add scope pill** — find the `<header ...>` block (starts at line 8) and inside the outer `<div class="flex items-center gap-3">`, append a scope pill *after* the existing header text block (right after the closing `</div>` that wraps "Your agent" + ready line, before the closing `</div>` of the `flex items-center gap-3` container). Replace:

```html
        <div class="flex items-center gap-3">
            <span aria-hidden="true" class="relative flex items-center justify-center w-9 h-9">
```

(keep that block intact up through its closing `</div>`) and then, immediately before `<button @click="reset()"`, insert the pill block:

```html
        {% if job %}
        <span class="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-accent-50 dark:bg-accent-950/40 ring-1 ring-accent-200 dark:ring-accent-800 text-[11px] font-medium text-accent-800 dark:text-accent-200">
            Talking about: {{ job.company|default:"this role" }} · {{ job.title }}
            <a href="{% url 'agent_chat' %}" aria-label="Back to general chat" class="ml-1 text-accent-700 dark:text-accent-300 hover:text-accent-900 dark:hover:text-accent-100">×</a>
        </span>
        {% endif %}
```

**3b. Swap seeds and add `jobId` to Alpine state** — find the `<script>` at the bottom (line 120). Replace the `seeds: [...]` array and add a `jobId` field. The new `agentChat()` returned object should start:

```javascript
function agentChat() {
    return {
        messages: [],
        userInput: '',
        loading: false,
        jobId: '{{ job_id|default_if_none:"" }}',
        seeds: {% if job %}[
            "How should I prep for this interview?",
            "What's my biggest gap on this role?",
            "Help me negotiate this offer.",
            "Which of my projects best fits this role?",
        ]{% else %}[
            "I have two offers — help me compare them.",
            "What should I focus on to switch from backend to ML?",
            "Draft a 30-second pitch from my profile.",
            "How do I explain a 6-month gap in interviews?",
        ]{% endif %},
```

**3c. Include `job_id` in the POST body** — find `body: JSON.stringify({ history, message: text }),` and replace with:

```javascript
                    body: JSON.stringify({ history, message: text, job_id: this.jobId || null }),
```

- [ ] **Step 4: Rebuild Tailwind and run tests**

Run: `npm run build:css`
Then: `python manage.py test core.tests.AgentChatViewJobTests -v 2`
Expected: PASS on all 7 tests in the class.

- [ ] **Step 5: Commit**

```bash
git add templates/core/agent_chat.html static/css/output.css core/tests.py
git commit -m "feat(agent): scope pill, job-scoped seeds, jobId in POST body"
```

---

## Task 10: Career-stage "Ask agent about this role" chip on `interviewing`

**Files:**
- Modify: `core/services/career_stage.py` — `interviewing` stage secondary actions
- Test: `core/tests.py` (extend `CareerStageSecondaryActionsTests`)

- [ ] **Step 1: Write a failing test asserting the new chip**

Append a new method to `CareerStageSecondaryActionsTests` in `core/tests.py`:

```python
    def test_interviewing_stage_includes_ask_agent_chip(self):
        from core.services.career_stage import detect_career_stage
        class _Job:
            id = 'deadbeef-dead-beef-dead-beefdeadbeef'
            company = 'Stripe'
            title = 'SWE'
            created_at = None
        jobs_by_status = {'interviewing': [_Job()]}
        s = detect_career_stage(
            has_profile=True,
            status_counts={'interviewing': 1},
            jobs_by_status=jobs_by_status,
        )
        labels = [a['label'] for a in s['secondary_actions']]
        hrefs = [a['href'] for a in s['secondary_actions']]
        self.assertTrue(any('Ask agent' in l for l in labels),
                        f"expected 'Ask agent' in {labels}")
        self.assertTrue(any(f"/agent/?job={_Job.id}" in h for h in hrefs),
                        f"expected /agent/?job= link in {hrefs}")
```

- [ ] **Step 2: Run — confirm it fails**

Run: `python manage.py test core.tests.CareerStageSecondaryActionsTests.test_interviewing_stage_includes_ask_agent_chip -v 2`
Expected: FAIL (no such chip exists).

- [ ] **Step 3: Add an `_agent_url` helper and a new chip**

In `core/services/career_stage.py`, add a helper alongside the other URL helpers (after `_resume_url`):

```python
def _agent_url(job_id) -> str:
    return f'/agent/?job={job_id}'
```

Then find the `INTERVIEWING` branch inside `detect_career_stage`. Look for:

```python
    # INTERVIEWING — deep-link to the specific job's chatbot for mock interview prep.
    if counts.get(STATUS_INTERVIEWING, 0) > 0:
        iv_job = _latest(jobs_by_status.get(STATUS_INTERVIEWING) or [])
        primary_href = _chat_url(iv_job.id) if iv_job else '/applications/'
        secondary = []
        if iv_job:
            secondary.append(StageAction(label='Review the gap analysis', href=_gap_url(iv_job.id)))
            secondary.append(StageAction(label='Rehearse outreach',       href=_outreach_url(iv_job.id)))
        secondary.append(StageAction(label='See pipeline', href='/applications/'))
```

Replace that block with (insert the new "Ask agent" action as the second secondary, so the strip becomes: gap → ask agent → pipeline; bumping "Rehearse outreach" off to keep the cap at 3):

```python
    # INTERVIEWING — deep-link to the specific job's chatbot for mock interview prep.
    if counts.get(STATUS_INTERVIEWING, 0) > 0:
        iv_job = _latest(jobs_by_status.get(STATUS_INTERVIEWING) or [])
        primary_href = _chat_url(iv_job.id) if iv_job else '/applications/'
        secondary = []
        if iv_job:
            secondary.append(StageAction(label='Review the gap analysis', href=_gap_url(iv_job.id)))
            secondary.append(StageAction(label='Ask agent about this role', href=_agent_url(iv_job.id)))
        secondary.append(StageAction(label='See pipeline', href='/applications/'))
```

- [ ] **Step 4: Run the new test plus the existing `CareerStageSecondaryActionsTests`**

Run: `python manage.py test core.tests.CareerStageSecondaryActionsTests -v 2`
Expected: all tests in this class PASS. If an existing test asserted "Rehearse outreach" on `interviewing`, it will now fail — if so, update that single assertion to match the new three chips (gap → ask agent → pipeline), or replace with `any()`-based existence checks. Do the fix inline before moving on.

- [ ] **Step 5: Commit**

```bash
git add core/services/career_stage.py core/tests.py
git commit -m "feat(agent): interviewing stage surfaces 'Ask agent about this role' chip"
```

---

## Task 11: Full suite + manual sanity check + final commit

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `python manage.py test -v 1`
Expected: ~168 tests PASS, 0 failures, 0 errors (up from the current 156).

- [ ] **Step 2: Rebuild Tailwind (belt-and-braces)**

Run: `npm run build:css`
Expected: silent success; `static/css/output.css` is refreshed.

- [ ] **Step 3: Manual smoke test**

Start the dev server: `python manage.py runserver`
In a browser:
  - Visit `/agent/` — scope pill absent, seeds are the general set.
  - Visit `/agent/?job=<id of a real job you own>` — scope pill shows "Talking about: <company> · <title>" with a `×` dismiss; seeds are the job-scoped set.
  - Send a message — reply should reference the job (company / title / skills). Check browser devtools Network tab — POST body includes `job_id`.
  - Click the `×` in the pill — lands on `/agent/` with general seeds.
  - Visit `/agent/?job=totally-fake-uuid` — redirects to `/agent/` (no crash; a Django message appears if `messages` block is rendered in base.html).
  - Visit `/agent/?job=<someone-else's-job-uuid>` — same redirect.
  - Dashboard → stage is "In interviews" → secondary chips now include "Ask agent about this role" which links to `/agent/?job=<id>`.
Kill the dev server.

- [ ] **Step 4: Commit any trailing output.css diff if present**

```bash
git status
# If static/css/output.css has uncommitted changes:
git add static/css/output.css
git commit -m "chore(css): rebuild tailwind after job-aware agent changes"
```

- [ ] **Step 5: Final all-clear**

Run: `git status`
Expected: working tree clean. Job-aware agent context feature is complete.

---

## Verification checklist (spec coverage)

- [x] Deep-link entry via `?job=<id>` — Task 7
- [x] Rich dossier (title/company/status/skills) — Task 1
- [x] Gap analysis subsection — Task 2
- [x] Snapshot diff subsection — Task 3
- [x] Artifacts subsection — Task 4
- [x] `build_system_prompt(user, job=None)` — Task 5
- [x] `chat(user, history, message, job=None)` — Task 6
- [x] View validates ownership + UUID — Task 7
- [x] API validates ownership + UUID (403 on foreign) — Task 8
- [x] Ephemeral chat (no persistence) — inherited from current behavior, no task needed
- [x] Scope pill + job-scoped seeds + `jobId` in POST — Task 9
- [x] Career-stage "Ask agent" chip — Task 10
- [x] Full test suite regression — Task 11
