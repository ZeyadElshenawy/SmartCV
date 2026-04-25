"""SmartCV benchmark suite — reproducible evaluations of LLM and scoring features.

Run-everything entrypoint:
    python benchmarks/run_all.py

Per-benchmark entrypoints:
    python -m benchmarks.ats_eval        # deterministic ATS scoring
    python -m benchmarks.latency_runner  # endpoint p50/p95/p99
    python -m benchmarks.parser_eval     # CV parser accuracy (needs fixtures)
    python -m benchmarks.skill_extractor_eval  # job skill F1 (needs fixtures)
    python -m benchmarks.gap_eval        # gap analyzer F1 (needs fixtures)
    python -m benchmarks.tailoring_eval  # LLM-judged resume quality (needs fixtures)

All metrics ship with sample size, methodology, and stochasticity disclosure.
See docs/benchmarks.md for the full methodology.
"""
