# SmartCV Benchmark Run

- **Run date:** 2026-04-27T22:00:00Z
- **Wall time:** 350.53s
- **Platform:** Windows 11 / Python 3.13.9
- **Phases:** ats_eval, latency_runner, parser_eval, skill_extractor_eval, gap_eval, tailoring_eval

## Headline Metrics

| Metric | Value | N | Source |
| --- | --- | --- | --- |
| ATS scoring deterministic (sigma=0) | **True** | 10 runs x 3 fixtures | benchmarks/ats_eval.py |
| ATS matched-vs-mismatched separation | matched **100.0** vs mismatched **11.0** (Cohen's d = **6.267**) | 3 matched, 6 mismatched | benchmarks/ats_eval.py |
| Endpoint warm p95 (max across routes) | **14.77 ms** | 5 routes x 100 req | benchmarks/latency_runner.py |
| CV parser personal-info accuracy | **0.942** | 10 CVs | benchmarks/parser_eval.py |
| CV parser skills F1 (CVs with explicit skills section) | **0.429** (Jaccard 0.303) | 5/10 CVs | benchmarks/parser_eval.py |
| CV parser skills F1 (all CVs, incl. those without a skills section) | 0.296 (Jaccard 0.197) | 10 CVs | benchmarks/parser_eval.py |
| Skill extractor F1 | **0.915** (P=0.943, R=0.892, halluc=0.057) | 5 JDs x 3 runs | benchmarks/skill_extractor_eval.py |
| Gap analyzer coverage (Phase 2 reconciliation) | **0.997** (47/50 pairs at 100%) | 50 (CV,JD) pairs | benchmarks/gap_eval.py |
| Gap analyzer separation (similarity score) | strong **0.465** / partial **0.3833** / weak **0.1412** (Cohen's d strong-vs-weak = **1.685**) | 50 pairs | benchmarks/gap_eval.py |
| Tailored resume — judge axes (1-10) | factuality **6.3** / relevance **6.9** / ats_fit **6.8** / human_voice **4.7** | 10 pairs (strong) | benchmarks/tailoring_eval.py |
| Tailored resume — programmatic entity grounding | **0.875** of generated entities appear verbatim in source CV | 10 pairs | benchmarks/tailoring_eval.py |

## Phase Wall Times

| Phase | Wall (s) | OK |
| --- | --- | --- |
| ats_eval | 0 | yes |
| latency_runner | 19.41 | yes |
| parser_eval | 1.76 | yes |
| skill_extractor_eval | 17.89 | yes |
| gap_eval | 188.63 | yes |
| tailoring_eval | 122.84 | yes |

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