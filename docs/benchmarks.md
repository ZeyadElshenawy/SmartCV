# SmartCV Benchmarks & Test Results

SmartCV ships with a small but defensible evaluation suite that grades the
LLM-driven features (CV parsing, skill extraction, gap analysis, ATS
scoring) against a hand-curated fixture set, plus a measured-latency runner
for the critical request paths. Every metric here is reproduced from real
code: no fabricated numbers, no parallel "benchmark-only" implementations.

> **Latest results live below** in the auto-generated block. Run
> `python -m benchmarks.run_all` to refresh them.

---

## Suite Layout

```
benchmarks/
  __init__.py            entry-point catalogue
  _io.py                 Django bootstrap + stats helpers
  ats_eval.py            Phase D4 — ATS scoring
  latency_runner.py      Phase B  — endpoint latency
  parser_eval.py         Phase D1 — CV parser accuracy
  skill_extractor_eval.py Phase D2 — JD skill extraction
  gap_eval.py            Phase D3 — gap analyzer separation + coverage
  run_all.py             Phase E  — orchestrator + report writer
  fixtures/
    manifest.json        10 CVs x 5 JDs + expected match strength
    jobs/                5 hand-written JDs with gold skill lists
    labels/              per-CV personal-info + skill labels
  results/
    <YYYY-MM-DD>/        JSON + markdown per run, never auto-deleted
```

The fixture CVs are referenced by their on-disk paths under `test cvs/` and
`test cvs2/` (both git-ignored) so personal PDFs never end up in the repo.
JDs and labels are PII-free and committed.

## Methodology by Phase

### Phase B — Endpoint Latency (`latency_runner.py`)

Hammers a fixture-free slice of routes via Django's in-process
`django.test.Client`, captures per-request wall time with
`time.perf_counter()`, and dumps the project's own
`core.metrics.snapshot()` after the run so the numbers compare cleanly
with the live `/healthz/metrics` endpoint.

Routes covered (anonymous, no fixture seeding):

- `GET /` — anonymous landing
- `GET /healthz/` — liveness (no DB)
- `GET /healthz/deep/` — readiness (one `SELECT 1`, 15 s response cache)
- `GET /accounts/login/` — auth form render
- `GET /accounts/register/` — registration form render

Reported per route: `p50`, `p95`, `p99`, `max`, plus a **cold/warm split**
(first 5 samples vs. the rest) so connection-warmup cost is visible. The
`SERVER_NAME=localhost` kwarg is required because the production
`ALLOWED_HOSTS` setting rejects the test client's default `testserver`
host post-hardening.

### Phase D1 — CV Parser (`parser_eval.py`)

Calls `profiles.services.cv_parser.parse_cv(...)` on each fixture CV and
compares against `benchmarks/fixtures/labels/<cv_id>.json`:

- **Personal-info accuracy**: case- and whitespace-insensitive exact match
  for `name`, `email`, `phone`, `location`. Fields the label sets to `null`
  are *skipped* (not counted against precision).
- **Section presence accuracy**: did the parser emit non-empty output
  for each section the label flags as present?
- **Skills overlap vs `skills_canonical`**: precision, recall, F1, and
  Jaccard. Comparison uses lowercased exact match plus
  `difflib.SequenceMatcher >= 0.85` fuzzy fallback (matches gap-analyzer
  cutoff) so cosmetic synonym differences don't dominate the score.

### Phase D2 — Skill Extractor (`skill_extractor_eval.py`)

Calls `jobs.services.skill_extractor.extract_skills(jd_text)` on each
fixture JD's description and compares to the JD's `expected_skills`
gold list:

- `precision = |extracted ∩ labeled| / |extracted|`
- `recall    = |extracted ∩ labeled| / |labeled|`
- `f1        = harmonic mean`
- `hallucination_rate = |extracted \ labeled| / |extracted|`

The same fuzzy synonym normalization (`>= 0.85`) is used for the
intersection. Each JD is run `--repeats` times (default 1) and per-JD
F1 / precision / recall / hallucination is reported as mean ± std so
LLM stochasticity is visible.

### Phase D3 — Gap Analyzer (`gap_eval.py`)

For every (CV, JD) pair in `manifest.json`:

1. Parse the CV via `parse_cv` (cached per-run).
2. Wrap in a duck-typed profile and a duck-typed job stub.
3. Call `analysis.services.gap_analyzer.compute_gap_analysis(profile, job)`.

Two things are validated:

- **Coverage** — every JD skill should land in `matched_skills`,
  `missing_skills`, or `partial_skills`. The Phase 2 reconciliation
  invariant claims 100%; this metric checks it on real data.
- **Separation** — pairs hand-graded as `strong` in
  `manifest.json -> expected_match_strength` should produce higher
  `similarity_score` than pairs graded `weak`. The runner reports
  per-bucket mean ± std and Cohen's d (strong vs weak).

Per-skill F1 against gold-categorized labels is intentionally **not**
attempted — hand-labeling 50 (CV, JD) pairs at the per-skill level would
be huge manual work, and the bucket separation signal is what actually
matters for "is this gap analyzer useful?".

### Phase D4 — ATS Scoring (`ats_eval.py`)

Three checks against `resumes.services.scoring.compute_ats_breakdown`:

1. **Determinism** — same `(resume, skills)` input run 10 times: std dev
   must be exactly 0 (the algorithm is pure Python). Any non-zero std is
   a regression alarm.
2. **Separation** — matched resumes (skills present) vs. mismatched
   resumes (skills absent), built from a small in-process synthetic suite
   (3 jobs across backend / frontend / data, with both matched and
   cross-paired mismatches). Reports mean ± std per group plus Cohen's d.
3. **Stuffing penalty** — a resume that repeats a single keyword 6 times
   must trigger the documented `−5 pts/stuffed-keyword` penalty.

This phase needs **no external fixtures** and is the cheapest to
re-run.

### Phase D5 — Resume Tailoring (`tailoring_eval.py` + `llm_judge.py`)

Pipeline per (CV, JD) pair, restricted to manifest pairs labeled
`strong` by default (~10 pairs):

1. Parse the CV via `parse_cv`.
2. Run `compute_gap_analysis` to feed the generator.
3. Generate a tailored resume via
   `resumes.services.resume_generator.generate_resume_content`.
4. Score with the 4-axis LLM judge defined in `benchmarks/llm_judge.py`
   — factuality / relevance / ats_fit / human_voice on a 1-10 scale,
   each with a one-sentence rationale.
5. Programmatic factuality pre-check: every company / school name in
   the generated resume must appear verbatim (case-insensitive) in the
   original CV text. Reports the grounded ratio.
6. Programmatic voice check: count occurrences of the banned tokens
   from `profiles.services.prompt_guards.HUMAN_VOICE_RULE`.

The judge prompt inlines the canonical voice rule so it grades against
the same standard the generator is supposed to follow. Judge runs at
`temperature=0.0` for near-deterministic scoring; the generator runs at
the production temperature so per-pair scores will vary across runs.

This phase is opt-in from `run_all.py` because of the LLM-call budget
(3 calls per pair × ~10 pairs ≈ 30 calls):

```bash
python -m benchmarks.run_all --with-tailoring
```

### Phase E — Orchestrator (`run_all.py`)

Single entry point that runs every phase, captures any uncaught exception
per-phase (no single failure aborts the whole run), aggregates the
headline metrics, writes:

- `benchmarks/results/<date>/run_all.json` — combined results blob.
- `benchmarks/results/<date>/run_all.md`   — human-readable summary
  (also re-published into the **autogen block** below).

## Reproduction

```bash
# Everything end-to-end:
python -m benchmarks.run_all

# Per phase:
python -m benchmarks.ats_eval
python -m benchmarks.latency_runner --requests 100
python -m benchmarks.parser_eval
python -m benchmarks.skill_extractor_eval --repeats 3
python -m benchmarks.gap_eval --repeats 3
python -m benchmarks.tailoring_eval                  # strong-bucket only

# With LLM-judged tailoring (Phase D5; slower, more LLM calls):
python -m benchmarks.run_all --with-tailoring

# Heavier disclosure (slower, more stable means):
python -m benchmarks.run_all --gap-repeats 3 --sx-repeats 3 --parser-repeats 3
```

Per-run JSON is never overwritten — each invocation writes into a dated
folder under `benchmarks/results/`.

## What this suite does NOT measure

- **Production load and concurrency.** Latencies are measured in-process
  on a single developer machine; multi-user contention, real WAN latency,
  Supabase queueing under burst load, and real-browser asset costs are
  out of scope.
- **Human-validated resume quality.** Phase D5 (resume tailoring) is
  graded by a single LLM judge plus a programmatic entity-grounding
  check. Treat absolute scores as a smoke test, not human-validated
  ground truth; relative trends across pairs and runs are more reliable
  than any single number.
- **Edge-case CVs.** The 10-CV fixture set is intentionally
  representative of the project's target users (early-career CS / SWE
  candidates, plus a few synthetic-style resumes) and does not yet
  cover non-tech fields, multi-language CVs, or scanned PDFs.
- **Fairness / bias evaluation.** No demographic slicing is performed
  on the fixture set.

## Latest Results

<!-- benchmarks:autogen:start -->

# SmartCV Benchmark Run

- **Run date:** 2026-04-25T11:02:31Z
- **Wall time:** 272.36s
- **Platform:** Windows 11 / Python 3.13.9
- **Phases:** ats_eval, latency_runner, parser_eval, skill_extractor_eval, gap_eval, tailoring_eval

## Headline Metrics

| Metric | Value | N | Source |
| --- | --- | --- | --- |
| ATS scoring deterministic (sigma=0) | **True** | 10 runs x 3 fixtures | benchmarks/ats_eval.py |
| ATS matched-vs-mismatched separation | matched **100.0** vs mismatched **11.0** (Cohen's d = **6.267**) | 3 matched, 6 mismatched | benchmarks/ats_eval.py |
| Endpoint warm p95 (max across routes) | **12.58 ms** | 5 routes x 60 req | benchmarks/latency_runner.py |
| CV parser personal-info accuracy | **0.942** | 10 CVs | benchmarks/parser_eval.py |
| CV parser skills F1 | **0.278** (Jaccard 0.176) | 10 CVs | benchmarks/parser_eval.py |
| Skill extractor F1 | **0.772** (P=0.693, R=0.893, halluc=0.307) | 5 JDs x 1 runs | benchmarks/skill_extractor_eval.py |
| Gap analyzer coverage (Phase 2 reconciliation) | **0.999** (49/50 pairs at 100%) | 50 (CV,JD) pairs | benchmarks/gap_eval.py |
| Gap analyzer separation (similarity score) | strong **0.55** / partial **0.4933** / weak **0.1882** (Cohen's d strong-vs-weak = **1.594**) | 50 pairs | benchmarks/gap_eval.py |
| Tailored resume — judge axes (1-10) | factuality **8.0** / relevance **6.8** / ats_fit **5.6** / human_voice **5.6** | 10 pairs (strong) | benchmarks/tailoring_eval.py |
| Tailored resume — programmatic entity grounding | **0.875** of generated entities appear verbatim in source CV | 10 pairs | benchmarks/tailoring_eval.py |

## Phase Wall Times

| Phase | Wall (s) | OK |
| --- | --- | --- |
| ats_eval | 0.0 | yes |
| latency_runner | 6.58 | yes |
| parser_eval | 1.29 | yes |
| skill_extractor_eval | 7.27 | yes |
| gap_eval | 123.11 | yes |
| tailoring_eval | 134.11 | yes |

## Disclosure

- LLM metrics (parser, skill extractor, gap analyzer) are run against Groq llama-4-scout. Stochasticity is reported per-phase as std dev across configurable repeats; the headline values above are the mean.
- Latency numbers are measured in-process via Django's test Client on the developer machine — production WAN latency is not included.
- ATS scoring is pure-Python and deterministic; the matched-vs-mismatched separation uses an in-process synthetic suite (3 jobs x 3 matched and 6 cross-paired mismatches) so it is reproducible without external fixtures.
- Parser, skill-extractor, and gap-analyzer use 10 hand-curated CVs and 5 hand-written JDs (`benchmarks/fixtures/`). The CV PDFs themselves are git-ignored to avoid republishing personal data.

## Reproduction

```bash
python -m benchmarks.run_all
```

Per-phase entry points are listed in `benchmarks/__init__.py`.

<!-- benchmarks:autogen:end -->
