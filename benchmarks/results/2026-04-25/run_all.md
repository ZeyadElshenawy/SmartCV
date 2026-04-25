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