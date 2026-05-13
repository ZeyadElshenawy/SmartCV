"""
benchmarks/jd_generator.py — Generate role-appropriate job descriptions for benchmark CVs.

Produces one JD per CV in benchmarks/fixtures/manifest.json (cvs[]). The JD is NOT derived
from the candidate's exact skill list — instead the LLM detects the candidate's primary
role + seniority, then writes a realistic JD a hiring manager might actually post for that
role. Some skills overlap with the CV; some don't. This preserves benchmark diagnostic
value (gap analyzer can find both matches and missing skills).

Output: benchmarks/fixtures/jobs/jd_auto_<cv_id>.json (one per CV).

CLI:
    python -m benchmarks.jd_generator --all
    python -m benchmarks.jd_generator --cv-ids cv_taher_mobile_jr cv_zeyad_datascience_jr
    python -m benchmarks.jd_generator --all --regenerate
    python -m benchmarks.jd_generator --cv-ids cv_taher_mobile_jr --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

# Bootstrap Django (cv_parser uses Django settings).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "smartcv.settings")
import django  # noqa: E402

django.setup()

# Windows console: force UTF-8 stdout so unicode arrows/glyphs in CV paths don't crash logging.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

from pydantic import BaseModel, Field  # noqa: E402

from profiles.services.cv_parser import parse_cv  # noqa: E402
from profiles.services.llm_engine import get_structured_llm  # noqa: E402
# RoleClassification + detect_role_seniority + _profile_summary_for_llm moved to
# profiles.services.role_classifier so the RAG retrieval flow can use them
# from production code without importing from benchmarks/.
from profiles.services.role_classifier import (  # noqa: E402
    RoleClassification,
    _profile_summary_for_llm,
    detect_role_seniority,
)


FIXTURES = ROOT / "benchmarks" / "fixtures"
MANIFEST = FIXTURES / "manifest.json"
JOBS_DIR = FIXTURES / "jobs"


# ---------- Pydantic schemas (LLM structured outputs) ----------


class GeneratedJd(BaseModel):
    """LLM call 2 output — a realistic JD for the detected role.

    title and expected_skills_must_have have safe defaults so a partial LLM
    response can still be salvaged (post-processed below).
    """
    title: str = Field(
        default="",
        description=(
            "Job title string. Example: 'Backend Engineer (Python)', 'Mobile Engineer (Flutter)'."
        ),
    )
    company: str = Field(description="Fictional company name. Avoid real-firm names like Google/Meta/etc.")
    description: str = Field(
        max_length=3000,
        description=(
            "Full JD text under 3000 characters: short intro, "
            "'Responsibilities:' (5-8 bullets), 'Must-have skills:' (6-10 bullets), "
            "'Nice to have:' (3-6 bullets). Plain text with section headers."
        ),
    )
    expected_skills: List[str] = Field(
        description=(
            "8-14 short canonical skill names the JD requires "
            "(e.g. 'Django', 'PostgreSQL', 'REST API'). Never sentences."
        ),
    )
    expected_skills_must_have: List[str] = Field(
        default_factory=list,
        description=(
            "Subset of expected_skills (6-10 entries) representing must-have requirements."
        ),
    )


# `_profile_summary_for_llm`, `RoleClassification`, and `detect_role_seniority`
# now live in profiles.services.role_classifier (imported at top of file).


# ---------- LLM calls ----------


def generate_role_jd(role: RoleClassification) -> GeneratedJd:
    """LLM call 2 — write a realistic JD for the classified role."""
    llm = get_structured_llm(GeneratedJd, temperature=0.3, max_tokens=4500, task="jd_generator")
    prompt = (
        "You are a hiring manager writing a realistic job description.\n\n"
        f"Target role: {role.primary_role}\n"
        f"Seniority: {role.seniority}\n"
        f"Reference tech-stack signals (NOT a copy list): {', '.join(role.tech_stack_signals)}\n\n"
        "OUTPUT ALL FIVE FIELDS: title, company, description, expected_skills, expected_skills_must_have. "
        "Do NOT omit any field — title comes first.\n\n"
        "RULES:\n"
        "1. Fictional company name — no real firms like Google/Meta/etc.\n"
        "2. The JD reflects the role and seniority, NOT the candidate's exact skills. "
        "   Include 8-14 skills total in expected_skills, of which 6-10 are must-haves. "
        "   Include 2-4 skills that are role-typical but might NOT appear in the "
        "   candidate's stack — this makes the gap-analysis benchmark meaningful.\n"
        "3. Description is plain text under 3000 characters with sections: short intro, "
        "   'Responsibilities:' (5-8 bullets), 'Must-have skills:' (6-10 bullets), "
        "   'Nice to have:' (3-6 bullets).\n"
        "\n"
        "=== CRITICAL — expected_skills FORMATTING ===\n"
        "Each entry MUST be a single canonical, atomic skill name. Examples of GOOD entries:\n"
        "  Python, Django, PostgreSQL, Docker, Kubernetes, REST API, Git, AWS, Redis,\n"
        "  React, TypeScript, GraphQL, Jest, Webpack, CI/CD, Terraform, gRPC, Kafka.\n"
        "\n"
        "FORBIDDEN — never use category labels, plurals of categories, or descriptive phrases:\n"
        "  BAD: 'Programming languages'   GOOD: pick one — 'Python' or 'JavaScript'\n"
        "  BAD: 'Cloud platforms'         GOOD: pick one — 'AWS' or 'GCP' or 'Azure'\n"
        "  BAD: 'Database management'     GOOD: pick one — 'PostgreSQL' or 'MongoDB'\n"
        "  BAD: 'Containerization'        GOOD: 'Docker'\n"
        "  BAD: 'Version control systems' GOOD: 'Git'\n"
        "  BAD: 'Agile development methodologies' / 'Agile development'   GOOD: 'Agile'\n"
        "  BAD: 'CI/CD pipelines' / 'CI/CD Pipelines'   GOOD: 'CI/CD'\n"
        "  BAD: 'Testing frameworks'      GOOD: pick one — 'Jest' or 'pytest' or 'Mocha'\n"
        "  BAD: 'Monitoring and logging tools'   GOOD: pick one — 'Prometheus' or 'ELK Stack'\n"
        "  BAD: 'Programming proficiency'  BAD: 'Software development'  BAD: 'Web development'\n"
        "  BAD: 'Mobile development'       BAD: 'API design principles'\n"
        "\n"
        "If you would otherwise write a category, replace it with one or two specific atomic skills.\n"
        "expected_skills_must_have follows the same rule and is a subset of expected_skills.\n"
    )
    return llm.invoke(prompt)


# ---------- Per-CV orchestration ----------


# Programmatic enforcement of the "atomic skills only" rule. Even with
# explicit BAD examples in the prompt, the LLM occasionally slips through
# category-style entries ('Backend technologies', 'Testing frameworks').
# This filter is a defense-in-depth — drop them before we serialize.
_CATEGORY_LABEL_PATTERNS = (
    "technologies", "frameworks", "platforms", "languages", "tools",
    "systems", "methodologies", "principles", "practices", "techniques",
    "tooling", "ecosystem", "best practices", "concepts", "fundamentals",
    "stack", "stacks", "skills",
)
# Phrases that are themselves category labels (case-insensitive, exact).
_CATEGORY_LABEL_EXACT = frozenset({
    "programming languages", "cloud platforms", "database management",
    "version control", "version control systems", "containerization",
    "agile development", "agile development methodologies",
    "ci/cd pipelines", "ci/cd pipeline",
    "testing frameworks", "testing", "monitoring and logging tools",
    "monitoring", "software development", "web development",
    "mobile development", "api design", "api design principles",
    "responsive web design", "front-end development", "frontend development",
    "back-end development", "backend development", "full-stack development",
    "backend technologies", "frontend technologies", "mobile technologies",
    "data structures and algorithms", "data structures", "algorithms",
    "operating systems", "scripting languages", "markup languages",
    "state management libraries", "build tools", "automation tools",
    "deployment tools", "ide", "ides",
})


def _is_atomic_skill(s: str) -> bool:
    """Reject category labels like 'Backend technologies' / 'Testing frameworks'."""
    if not s or not s.strip():
        return False
    low = s.strip().lower()
    if low in _CATEGORY_LABEL_EXACT:
        return False
    # Multi-word ending in a generic category noun
    tokens = low.split()
    if len(tokens) >= 2 and tokens[-1] in _CATEGORY_LABEL_PATTERNS:
        return False
    return True


def _strip_category_labels(skills: List[str]) -> List[str]:
    return [s for s in skills if _is_atomic_skill(s)]


def generate_jd_for_cv(cv_entry: dict, regenerate: bool = False, dry_run: bool = False) -> dict:
    """Run the two-step pipeline for one CV. Returns the JD dict (also written to disk unless dry_run)."""
    cv_id = cv_entry["id"]
    cv_path = ROOT / cv_entry["path"]
    out_path = JOBS_DIR / f"jd_auto_{cv_id}.json"

    if out_path.exists() and not regenerate and not dry_run:
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        print(f"[skip] {cv_id}: JD already exists at {out_path.name}")
        return existing

    print(f"[parse] {cv_id} <- {cv_entry['path']}")
    profile = parse_cv(str(cv_path))
    summary = _profile_summary_for_llm(profile)

    print(f"[role ] {cv_id}: classifying...")
    role = detect_role_seniority(summary)
    print(f"        primary_role={role.primary_role!r} seniority={role.seniority!r}")

    print(f"[jd   ] {cv_id}: generating JD...")
    jd = generate_role_jd(role)

    # Post-process: salvage partial LLM responses by filling missing fields from the role classification.
    if not jd.title.strip():
        seniority_prefix = ""
        if role.seniority and role.seniority.lower() in {"senior", "lead", "junior"}:
            seniority_prefix = role.seniority.capitalize() + " "
        jd.title = f"{seniority_prefix}{role.primary_role}".strip()
        print(f"        [post] synthesized title: {jd.title!r}")
    # Strip category labels from skill lists (defense-in-depth against prompt non-compliance).
    before_n = len(jd.expected_skills)
    jd.expected_skills = _strip_category_labels(jd.expected_skills)
    if len(jd.expected_skills) < before_n:
        print(f"        [post] dropped {before_n - len(jd.expected_skills)} category labels from expected_skills")
    jd.expected_skills_must_have = _strip_category_labels(jd.expected_skills_must_have)
    if not jd.expected_skills_must_have:
        # Default to first 7 of expected_skills as the must-have core.
        jd.expected_skills_must_have = jd.expected_skills[:7]
        print(f"        [post] synthesized expected_skills_must_have from first 7 expected_skills")

    jd_id = f"jd_auto_{cv_id}"
    payload = {
        "id": jd_id,
        "title": jd.title,
        "company": jd.company,
        "description": jd.description,
        "expected_skills": jd.expected_skills,
        "expected_skills_must_have": jd.expected_skills_must_have,
        "paired_cv_id": cv_id,
        "_meta": {
            "generated_by": "benchmarks.jd_generator",
            "source_cv_id": cv_id,
            "detected_role": role.primary_role,
            "detected_seniority": role.seniority,
            "tech_stack_signals": role.tech_stack_signals,
        },
    }

    if dry_run:
        print(f"[dry  ] {cv_id}: would write {out_path.name} ({len(jd.description)} chars description)")
    else:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[write] {cv_id}: {out_path.name} ({out_path.stat().st_size:,} bytes)")

    return payload


# ---------- CLI ----------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate role-appropriate JDs for benchmark CVs.")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--all", action="store_true", help="Generate for all CVs in manifest.")
    grp.add_argument("--cv-ids", nargs="+", help="Generate for these specific CV IDs.")
    ap.add_argument("--regenerate", action="store_true", help="Overwrite existing JD files.")
    ap.add_argument("--dry-run", action="store_true", help="Print plan without writing.")
    args = ap.parse_args(argv)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cvs = manifest["cvs"]
    if args.cv_ids:
        wanted = set(args.cv_ids)
        cvs = [c for c in cvs if c["id"] in wanted]
        missing = wanted - {c["id"] for c in cvs}
        if missing:
            print(f"[error] unknown cv_ids: {sorted(missing)}", file=sys.stderr)
            return 2

    print(f"Generating JDs for {len(cvs)} CV(s) (regenerate={args.regenerate} dry_run={args.dry_run})\n")
    t0 = time.time()
    failures: List[str] = []
    for i, cv in enumerate(cvs, 1):
        print(f"--- ({i}/{len(cvs)}) {cv['id']} ---")
        try:
            generate_jd_for_cv(cv, regenerate=args.regenerate, dry_run=args.dry_run)
        except Exception as e:
            print(f"[FAIL ] {cv['id']}: {type(e).__name__}: {e}", file=sys.stderr)
            failures.append(cv["id"])
        print()

    dt = time.time() - t0
    print(f"=== done in {dt:.1f}s ===")
    if failures:
        print(f"failures: {failures}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
