# SmartCV Integration Tests

Behavior-driven end-to-end tests for the resume generation pipeline.
Unlike the unit tests under `tests/services/` (which probe individual
functions in isolation), integration tests run real fixtures through the
full pipeline and assert on user-visible behavior — what the user sees
in their generated resume.

The most informative validation in the PR 1-3 series was the manual
Zeyad re-run diagnostic. This suite makes that diagnostic permanent and
runnable on every PR.

## Running

### Replay mode (fast, CI-safe — default)

Tests use recorded LLM responses; no network calls.

```bash
pytest tests/integration/ -m integration_recorded
```

Runs in seconds. Safe to wire into CI.

### Record mode (slow, requires `GROQ_API_KEY` in `.env`)

Real Groq calls; recordings are saved/updated under `fixtures/recordings/`.

```bash
INTEGRATION_RECORD=1 pytest tests/integration/
```

Runs in ~1-3 minutes per test class (multiple LLM calls per pipeline run:
role classifier x2, gap analyzer, resume generator). On Windows
PowerShell:

```powershell
$env:INTEGRATION_RECORD=1; pytest tests/integration/
```

## Adding a new fixture

1. Create `tests/integration/fixtures/<name>.json` — the profile data.
   Top-level keys mirror `UserProfile.data_content` (skills,
   experiences, projects, certifications, etc.). Add a
   `_test_metadata` block describing the scenario and expected
   behaviours — it's documentation, not data (the loader strips it).
2. Create `tests/integration/fixtures/<name>.jd.json` — the JD payload
   (`title`, `company`, `description`, `extracted_skills`,
   `extracted_skills_tiers`, `domain`). The `.jd.json` is the source of
   truth for the JD; tests read from it.
3. Optionally create `tests/integration/fixtures/<name>.jd.txt` —
   plain-text JD body for legibility. The loader falls back to
   `description` from `.jd.json` if this file is missing.
4. Add a test class in a new test file under `tests/integration/`.
   Use `test_zeyad_ai_developer.py` or `test_junior_devops.py` as
   templates.
5. Add a fixture loader in `conftest.py` (one line) so test files can
   request the fixture by name.
6. Run with `INTEGRATION_RECORD=1` to capture recordings.
7. Commit fixtures + recordings together.

## What makes a good assertion

### GOOD (behaviour the user can see)

- `"TensorFlow appears in skills or in any bullet text"`
- `"Brain Tumor project included in output"`
- `"No LANGUAGES section when profile has no spoken languages"`
- `"NLP cert appears for AI Developer JDs"`

### BAD (implementation detail; breaks when behaviour improves)

- `"_canonical_project_name returns 'braintumor' for 'Brain Tumor'"`
- `"trim_projects_to_plan returns exactly 6 projects"`
- `"_discriminating_tech_overlap returns the tuple (3, ['python'])"`

Implementation-detail tests cement implementation. Behavioral tests
survive refactors. Always prefer the latter.

## When to update recordings

### REFRESH the recording when:

- The LLM prompt changes (the model sees different input → produces
  different output)
- The schema (`ResumeContentResult`) changes
- The retrieval logic changes (different chunks → different prompt
  context)

### DO NOT refresh when:

- An assertion fails — investigate; the failure is a real signal
- The post-LLM normalizer changes (recordings still capture valid LLM
  output; normalizer changes affect what we assert on)
- A bug is found in the assertion (fix the assertion, keep recordings)

### How to refresh:

```bash
# Delete the specific recording
rm tests/integration/fixtures/recordings/test_zeyad_ai_developer.json

# Run record mode just for that test
INTEGRATION_RECORD=1 pytest tests/integration/test_zeyad_ai_developer.py

# Inspect the new recording before committing
git diff tests/integration/fixtures/recordings/
```

## When tests discover bugs

If an integration test fails, **do not fix the bug in the same PR that
fails the test**. Mark the test:

```python
@pytest.mark.xfail(reason="Bug: cert restoration regresses; tracked in PR 4.1")
def test_nlp_cert_present(self):
    ...
```

When the bug is fixed in a separate PR, remove the `xfail` marker.
The test becomes a regression lock.

## Architecture

### LLM patching

`conftest.py` patches `profiles.services.llm_engine.get_structured_llm`
and `profiles.services.llm_engine.get_llm` to return recording-aware
stubs. The pipeline's `.invoke(prompt)` calls flow through
`_LLMRecorder.get_or_record(...)`:

- In **record mode**: the real LLM is invoked, the response is
  serialised (Pydantic `model_dump()` for structured outputs), and
  the dump is appended to the recording file.
- In **replay mode**: the next recorded response is deserialised and
  returned. Replay is keyed by call ordinal — the pipeline is
  deterministic, so the call sequence matches the recording sequence.

Both the source module AND the importing modules are patched, because
`from profiles.services.llm_engine import get_structured_llm` binds the
function into the importing module's namespace. The patcher walks the
known importers (`gap_analyzer`, `role_classifier`,
`resume_generator`, `skill_extractor`) and overrides each.

### Pipeline runner

`run_full_pipeline(profile_data, jd_payload)` drives:

1. Construct duck-typed `_FakeProfile`, `_FakeJob`, and `_FakeGapAnalysis`
   (the pipeline reads them via attribute access only — no Django ORM
   needed).
2. Compute gap analysis via `analysis.services.gap_analyzer.compute_gap_analysis`
   (LLM call 1).
3. Call `resume_generator.generate_resume_content(profile, job, ga,
   metadata={})`. Internal calls:
   - `_build_standards_section` → `classify_for_jd` (LLM calls 2 + 3)
     + `retrieve_chunks` (sentence-transformers, local)
   - `_build_v2_grounding` → `retrieve_for_skills` (local) +
     `build_inclusion_plan` (pure)
   - Main resume generation LLM call (LLM call 4)
4. Return the post-normalised content dict with three metadata keys
   attached:
   - `_classification`: `{primary_role, seniority, region, profile_role}`
   - `_retrieval_metadata`: `{chunk_ids, chunk_types, chunk_roles}`
   - `_plan_metadata`: `{project_count_in_plan, cert_count_in_plan,
     skill_count_in_plan, project_names_in_plan, cert_names_in_plan}`

Tests assert on whatever the renderer would emit (the resume content
fields) plus on the underscore-prefixed metadata.

## Tracked known failures and operational blockers

Updated after PR 4.1 (recorder rate-limit handling + completeness
markers). Status moved from "5 xfails caused by incomplete recordings"
to "9 DevOps tests fully passing + Zeyad re-record blocked on Groq
daily token ceiling".

### Recordings status

- **DevOps suite (9 tests)**: all 4/4 LLM calls captured, v2 format,
  complete. All passing in replay.
- **Zeyad suite (11 tests)**: not recorded. PR 4.1's PR-record run hit
  Groq's tokens-per-DAY (TPD) limit of 500K mid-suite. The new pacing
  (15 s between calls) addressed the per-MINUTE TPM ceiling, but TPD
  is cumulative across the full record run. Zeyad's 101 KB profile
  produces a ~28K-token resume-gen prompt; 11 tests × ~50K tokens per
  test = ~550K tokens, slightly over the daily 500K budget.

### Active xfails

| Test | Reason | Status |
|---|---|---|
| `TestZeyadAIDeveloper.test_role_classification` | Recording-infra (now: TPD daily limit) | Dormant — test skips when no recording exists |
| `TestZeyadAIDeveloper.test_dual_role_retrieval` | DB-less RAG retrieval | Still applies after re-record (separate issue) |
| `TestZeyadAIDeveloper.test_summary_no_banned_opener` | Recording-infra (now: TPD daily limit) | Dormant — skips |
| `TestZeyadAIDeveloper.test_brain_tumor_project_included` | Recording-infra (now: TPD daily limit) | Dormant — skips |
| `TestZeyadAIDeveloper.test_nlp_cert_present` | Recording-infra (now: TPD daily limit) | Dormant — skips |

The xfail markers remain on the 5 Zeyad tests because the underlying
issue (recording-infra blockers preventing assertion validation) is
unresolved — only the diagnostic cause shifted from per-minute to
per-day. After Zeyad re-record succeeds:

- 4 of 5 should clear (the rate-limit-cascade ones)
- 1 (`test_dual_role_retrieval`) stays — DB-less conftest issue, PR 4.2 scope

### To unblock Zeyad re-record

One of:

1. **Wait** for Groq's TPD counter to reset (24-hour window).
2. **Upgrade** to Groq's Developer tier ($XX/month, lifts TPD to
   multi-million).
3. **Record fewer tests per session** — split into 2-3 record-mode
   runs separated by ≥24 hours. Workable for a one-off but bad
   ergonomics for future fixture refreshes.
4. **Reduce prompt size** — trim Zeyad's profile fixture (101K → ~40K)
   by removing redundant data. Risk: changes what the LLM sees, so the
   recording captures a different scenario than production.

Option 2 is recommended for sustained CI-level use; option 1 is fine
for solo-developer cadence.

### Additional finding: schema-validation failures during record

3 Zeyad tests didn't hit the TPD limit but hit `BadRequestError: tool
call validation failed` — Groq rejected the LLM's tool-call output
because fields like `/education/0/honors` got `null` where the schema
expected `array`, etc. The production code recovers from this via
PR 3f's tolerant parse + flattener. The current recorder catches the
exception, marks the recording incomplete, and re-raises — bypassing
the production recovery path.

**Flag for PR 4.3**: extend the recorder to capture exceptions as
first-class entries (replay re-raises the same exception so production
recovery runs in the same way). Lets us record what production
actually handles, not just what the LLM happily produces.

When refreshing recordings, run `python tools/check_recordings.py`
before commit — it flags incomplete captures, version mismatches,
and short-by-call-count recordings.

## Fixtures shipped

| Fixture | Profile shape | JD | What it covers |
|---|---|---|---|
| `zeyad_ai_developer` | Zeyad Elshenawy — CS undergrad, SmartCV (Llama-4 + Groq + pgvector RAG), Brain Tumor CNN, Healthcare Prediction, Customer Segmentation, End-to-End Pipeline. 21 certs. | Pharco Corporation AI Developer (mixed must-have/nice-to-have with soft-skill noise) | PR 1 LANGUAGES; PR 2a dual-role; PR 2b project/cert; PR 3a restoration; PR 3c openers; PR 3d soft skills; PR 3e evidence linking; PR 2c ml_engineer KB |
| `junior_devops` | Constructed DevOps intern with Docker/K8s/Terraform projects. AWS CCP + CKA certs. | Junior DevOps Engineer (mirror structure to Pharco; same soft-skill block) | Non-AI track regression: PR 2c chunks must NOT fire here; soft-skill filter must work generically |
