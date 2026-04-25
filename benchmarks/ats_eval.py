"""ATS scoring evaluation (Phase D4).

Three checks against ``resumes.services.scoring.compute_ats_breakdown``:

1. Determinism — same (resume, skills) input produces identical scores
   across N=10 runs (σ must be exactly 0; the algorithm is pure Python).
2. Separation — matched resumes (skills present) score strictly higher
   than mismatched resumes (skills absent). Reports mean ± std for each
   group plus Cohen's d effect size.
3. Stuffing penalty — a resume that repeats a single keyword 6× triggers
   the −5 pts/stuffed-keyword penalty exactly as documented.

This benchmark needs no external fixtures: the synthetic CV/job pairs
below are generated in-process so the eval runs anywhere the project
imports cleanly.

Run:
    python -m benchmarks.ats_eval
"""
from __future__ import annotations

import statistics
from typing import Iterable

from benchmarks._io import cohens_d, summary, write_section
from resumes.services.scoring import (
    STUFFING_PENALTY_PER_SKILL,
    STUFFING_THRESHOLD,
    compute_ats_breakdown,
)


# ─── Synthetic suite ────────────────────────────────────────────────────────

JOB_SKILLS_BACKEND = ["Python", "Django", "PostgreSQL", "Docker", "REST API"]
JOB_SKILLS_FRONTEND = ["React", "TypeScript", "CSS", "Webpack", "Jest"]
JOB_SKILLS_DATA = ["Python", "Pandas", "SQL", "Spark", "Airflow"]


def _resume(skills: list[str], experience_bullets: list[str]) -> dict:
    """Build a resume_content dict shaped like the production schema."""
    return {
        "name": "Test Candidate",
        "summary": "Software engineer.",
        "skills": skills,
        "experience": [
            {
                "title": "Engineer",
                "company": "Acme Corp",
                "description": experience_bullets,
            }
        ],
        "education": [{"degree": "BSc CS", "school": "State University"}],
    }


# A "matched" resume mentions every job skill in both the skills list and
# at least one experience bullet — the kind of CV the app should reward.
MATCHED_RESUMES = {
    "backend": _resume(
        skills=JOB_SKILLS_BACKEND,
        experience_bullets=[
            "Built Django REST API services in Python backed by PostgreSQL.",
            "Containerised deployments with Docker for staging environments.",
        ],
    ),
    "frontend": _resume(
        skills=JOB_SKILLS_FRONTEND,
        experience_bullets=[
            "Authored React components in TypeScript with bespoke CSS modules.",
            "Configured Webpack bundles and Jest unit-test pipelines.",
        ],
    ),
    "data": _resume(
        skills=JOB_SKILLS_DATA,
        experience_bullets=[
            "Wrote Pandas pipelines and SQL transforms feeding Spark jobs.",
            "Orchestrated daily batch loads via Airflow DAGs in Python.",
        ],
    ),
}

# A "mismatched" resume targets a different domain — none of the job's
# required skills appear in either the skills list or the bullets.
MISMATCHED_RESUMES = {
    "backend_vs_frontend": _resume(
        skills=JOB_SKILLS_FRONTEND,
        experience_bullets=[
            "Wrote React components in TypeScript with Jest snapshot tests.",
            "Tuned Webpack bundles for first-paint performance on landing pages.",
        ],
    ),
    "frontend_vs_data": _resume(
        skills=JOB_SKILLS_DATA,
        experience_bullets=[
            "Built Pandas notebooks against Spark clusters for analytics.",
            "Owned Airflow scheduling for a fleet of SQL warehouse jobs.",
        ],
    ),
    "data_vs_backend": _resume(
        skills=JOB_SKILLS_BACKEND,
        experience_bullets=[
            "Operated Django services on Docker, calling PostgreSQL via REST.",
            "Wrote service shims in Python for inter-team API contracts.",
        ],
    ),
}

JOB_SUITE = {
    "backend": JOB_SKILLS_BACKEND,
    "frontend": JOB_SKILLS_FRONTEND,
    "data": JOB_SKILLS_DATA,
}


# ─── Checks ─────────────────────────────────────────────────────────────────

def check_determinism(runs: int = 10) -> dict:
    """Score every (matched_resume, its_job) pair ``runs`` times and confirm σ=0."""
    rows = []
    all_zero = True
    for key, skills in JOB_SUITE.items():
        resume = MATCHED_RESUMES[key]
        scores = [compute_ats_breakdown(resume, skills)["score"] for _ in range(runs)]
        std = statistics.pstdev(scores) if len(scores) > 1 else 0.0
        if std != 0.0:
            all_zero = False
        rows.append({
            "fixture": key,
            "runs": runs,
            "mean": round(statistics.fmean(scores), 4),
            "std": round(std, 6),
            "min": min(scores),
            "max": max(scores),
        })
    return {
        "runs_per_fixture": runs,
        "deterministic": all_zero,
        "per_fixture": rows,
    }


def _score_pairs(resumes: dict, jobs: dict, *, same_key_only: bool) -> list[float]:
    out: list[float] = []
    for r_key, resume in resumes.items():
        for j_key, skills in jobs.items():
            if same_key_only and r_key != j_key:
                continue
            if not same_key_only and r_key == j_key:
                continue
            out.append(compute_ats_breakdown(resume, skills)["score"])
    return out


def check_separation() -> dict:
    """Matched resumes vs. mismatched resumes — does the score actually separate them?"""
    matched_scores = _score_pairs(MATCHED_RESUMES, JOB_SUITE, same_key_only=True)
    # For the mismatched group, cross every matched resume against every *other* job.
    mismatched_scores = _score_pairs(MATCHED_RESUMES, JOB_SUITE, same_key_only=False)

    matched_summary = summary(matched_scores)
    mismatched_summary = summary(mismatched_scores)
    d = cohens_d(matched_scores, mismatched_scores)
    separated = (matched_summary["mean"] or 0) > (mismatched_summary["mean"] or 0)

    return {
        "matched": {**matched_summary, "scores": matched_scores},
        "mismatched": {**mismatched_summary, "scores": mismatched_scores},
        "cohens_d": d,
        "separated": separated,
    }


def check_stuffing_penalty() -> dict:
    """Resume that repeats a single keyword 6× should lose 5 points per stuffed skill."""
    stuffed_resume = _resume(
        skills=JOB_SKILLS_BACKEND,
        experience_bullets=[
            # "Python" appears 6 times — past STUFFING_THRESHOLD (4).
            "Python Python Python Python Python Python — and also Django, PostgreSQL, "
            "Docker, REST API for completeness."
        ],
    )
    breakdown = compute_ats_breakdown(stuffed_resume, JOB_SKILLS_BACKEND)
    stuffed = breakdown["stuffed_skills"]
    penalty_applied = breakdown["stuffing_penalty"]

    expected_penalty = len(stuffed) * STUFFING_PENALTY_PER_SKILL
    return {
        "stuffing_threshold": STUFFING_THRESHOLD,
        "penalty_per_skill": STUFFING_PENALTY_PER_SKILL,
        "stuffed_skills_detected": stuffed,
        "stuffing_penalty_applied": penalty_applied,
        "expected_penalty_for_detected": expected_penalty,
        "penalty_matches_formula": penalty_applied == expected_penalty,
        "stuffing_fired": len(stuffed) > 0,
        "keyword_counts": breakdown["keyword_counts"],
    }


# ─── Entry point ────────────────────────────────────────────────────────────

def run() -> dict:
    payload = {
        "benchmark": "ats_eval",
        "version": 1,
        "fixture_kind": "synthetic",
        "n_jobs": len(JOB_SUITE),
        "n_resumes_matched": len(MATCHED_RESUMES),
        "determinism": check_determinism(runs=10),
        "separation": check_separation(),
        "stuffing": check_stuffing_penalty(),
        "disclosure": (
            "Synthetic in-process fixtures — no real CVs. ATS algorithm is "
            "pure Python; σ=0 is the expected determinism outcome. Separation "
            "is measured across 3 matched (resume, job) pairs vs. 6 cross-paired "
            "mismatches."
        ),
    }
    out_path = write_section("ats_eval", payload)
    payload["written_to"] = str(out_path)
    return payload


def _format_report(payload: dict) -> str:
    det = payload["determinism"]
    sep = payload["separation"]
    stuff = payload["stuffing"]
    lines = [
        "-- ATS scoring evaluation (Phase D4) --",
        f"  Determinism  : {'PASS' if det['deterministic'] else 'FAIL'} "
        f"(N={det['runs_per_fixture']} per fixture)",
        f"  Separation   : matched mean={sep['matched']['mean']}  "
        f"mismatched mean={sep['mismatched']['mean']}  "
        f"d={sep['cohens_d']}  -> {'PASS' if sep['separated'] else 'FAIL'}",
        f"  Stuffing     : detected={stuff['stuffed_skills_detected']}  "
        f"penalty={stuff['stuffing_penalty_applied']} pts  "
        f"-> {'PASS' if stuff['stuffing_fired'] and stuff['penalty_matches_formula'] else 'FAIL'}",
        f"  Written to   : {payload['written_to']}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    result = run()
    print(_format_report(result))
