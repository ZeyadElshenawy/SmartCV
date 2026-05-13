"""A/B harness wrapping the existing tailoring_eval for the §5 RAG evaluation.

Flips RAG + validator settings per treatment via `django.test.override_settings`
(touches Django's settings object, not the .env file — leaves the dev DB and
.env intact between runs), then invokes `benchmarks.tailoring_eval.run` with
a unique `section_name` so each treatment writes to its own JSON snapshot.

Usage:
    python -m benchmarks.tailoring_eval_ab --treatment T0 --buckets strong partial
    python -m benchmarks.tailoring_eval_ab --treatment T2 --buckets strong --max-pairs 2

Treatments (per §5.2 of section5-eval-plan.md):
    T0  baseline: RAG off, validator report_only (current behavior on main)
    T1  RAG on, validator report_only (retrieval-only signal)
    T2  RAG on, safe_autofix           (the real shipping config)
    T3  T2 + BULLET_RETRY=True         (one-shot LLM retry; reserved)

Output:
    benchmarks/results/<date>/tailoring_eval__T<n>.json

This wrapper does NOT modify the .env or any committed config. Each run is
self-contained.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "smartcv.settings")

import django  # noqa: E402
django.setup()  # noqa: E402

from django.test.utils import override_settings  # noqa: E402

from benchmarks import tailoring_eval  # noqa: E402


# Treatment definitions: each is a kwargs dict for override_settings.
TREATMENTS: dict[str, dict[str, object]] = {
    "T0": {
        "RAG_ENABLED": False,
        "BULLET_AUTOFIX": "report_only",
        "BULLET_RETRY": False,
        "BULLET_VALIDATOR_STRICT": False,
    },
    "T1": {
        "RAG_ENABLED": True,
        "BULLET_AUTOFIX": "report_only",
        "BULLET_RETRY": False,
        "BULLET_VALIDATOR_STRICT": False,
    },
    "T2": {
        "RAG_ENABLED": True,
        "BULLET_AUTOFIX": "safe_autofix",
        "BULLET_RETRY": False,
        "BULLET_VALIDATOR_STRICT": False,
    },
    "T3": {
        "RAG_ENABLED": True,
        "BULLET_AUTOFIX": "safe_autofix",
        "BULLET_RETRY": True,
        "BULLET_VALIDATOR_STRICT": False,
    },
}


def _sleep_throttle(seconds: float):
    """Inject a per-pair sleep into tailoring_eval. The existing per-LLM-call
    rate-limit lives inside the run loop; this is a coarser knob for staying
    under Groq's 30k TPM ceiling across long batches (matches the --sleep
    pattern in parser_eval / skill_extractor_eval / gap_eval).

    Monkey-patches `time.sleep` inside benchmarks.tailoring_eval briefly so
    the existing loop body sleeps without us editing it.
    """
    # No-op for now — tailoring_eval's loop doesn't currently call time.sleep
    # itself; throttling happens inside the LLM clients. Hook left as a
    # placeholder for parity with the other eval CLIs.
    return seconds


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="SmartCV RAG A/B eval — wraps tailoring_eval with treatment flags."
    )
    p.add_argument(
        "--treatment", choices=sorted(TREATMENTS), default="T0",
        help="Which feature-flag combo to run (default: T0 baseline)."
    )
    p.add_argument(
        "--buckets", nargs="+", default=["strong", "partial"],
        choices=["strong", "partial", "weak"],
        help="Manifest buckets to evaluate (default: strong + partial).",
    )
    p.add_argument("--max-pairs", type=int, default=None,
                   help="Cap on pairs evaluated; useful for smoke tests.")
    p.add_argument("--sleep", type=float, default=0.0,
                   help="Reserved — see _sleep_throttle docstring.")
    p.add_argument(
        "--section-name", default=None,
        help="Override the snapshot filename stem "
             "(default: tailoring_eval__<treatment>)."
    )
    return p.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    treatment = args.treatment
    overrides = TREATMENTS[treatment]
    section_name = args.section_name or f"tailoring_eval__{treatment}"

    print(f"[ab] treatment={treatment}  overrides={overrides}")
    print(f"[ab] buckets={args.buckets}  max_pairs={args.max_pairs}  "
          f"section_name={section_name}")
    started = time.perf_counter()

    with override_settings(**overrides):
        payload = tailoring_eval.run(
            buckets=tuple(args.buckets),
            max_pairs=args.max_pairs,
            treatment_label=treatment,
            section_name=section_name,
        )

    elapsed = round(time.perf_counter() - started, 1)
    print(f"[ab] DONE in {elapsed}s — wrote {payload.get('written_to')}")
    print(tailoring_eval._format_report(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
