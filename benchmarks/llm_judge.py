"""4-axis LLM judge for tailored-resume quality (Phase D5 helper).

Scores a generated resume against the source CV + job on:

- factuality   — does the resume invent experience not in the source CV?
- relevance    — do the bullets address concrete requirements in the JD?
- ats_fit      — does it use the JD's keywords with action-verb diversity?
- human_voice  — does it avoid the AI-tell phrases banned in
                 ``profiles.services.prompt_guards.HUMAN_VOICE_RULE``?

Each axis is scored 1–10 with a one-sentence rationale. The judge call
uses the same Groq model the rest of the project uses, via
``get_structured_llm`` — no parallel LLM client.

A simple programmatic factuality pre-check (entity extraction + verbatim
membership in source CV) is also returned alongside the LLM scores so
the README can disclose both signals.
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Union

from pydantic import BaseModel, Field, field_validator

from profiles.services.llm_engine import get_structured_llm
from profiles.services.prompt_guards import HUMAN_VOICE_RULE


class AxisScore(BaseModel):
    # `score` accepts either int or string (the LLM occasionally emits
    # "7" instead of 7) and coerces to int below. We can't keep the
    # Pydantic schema strict-int because Groq's tool-call validator
    # rejects str-typed outputs upstream of Pydantic — so we accept both
    # in the schema and clamp on the way in.
    #
    # Hard cap on rationale raised from 400 -> 800 because the judge LLM
    # occasionally produces 500-615 char rationales on detailed cases.
    score: Union[int, str] = Field(..., description="Integer 1-10")
    rationale: str = Field(..., max_length=800)

    @field_validator('score', mode='before')
    @classmethod
    def coerce_score(cls, v):
        """Accept either int or str-of-int, clamp to [1, 10]."""
        if isinstance(v, str):
            try:
                v = int(v.strip())
            except (ValueError, AttributeError):
                v = 1
        try:
            v = int(v)
        except (TypeError, ValueError):
            v = 1
        return max(1, min(10, v))


class JudgeVerdict(BaseModel):
    factuality: AxisScore
    relevance: AxisScore
    ats_fit: AxisScore
    human_voice: AxisScore
    overall_summary: str = Field(..., max_length=800)


JUDGE_PROMPT = """You are a strict, neutral resume reviewer. You will read:
- The candidate's SOURCE CV (the truth).
- The TARGET JOB (title + required skills + description excerpt).
- A GENERATED RESUME claiming to be a tailored version of the source CV for the target job.

Score the GENERATED RESUME on FOUR axes from 1 (terrible) to 10 (excellent).

=== AXES ===

1. FACTUALITY (1-10)
   Does every concrete claim in the GENERATED RESUME (companies, schools,
   years, certifications, named projects) appear in the SOURCE CV? Any
   fabricated employer or degree is an automatic <= 3.

2. RELEVANCE (1-10)
   Do the bullets address requirements stated in the TARGET JOB? Generic
   bullets that could apply to any role lower the score; bullets naming
   the JD's required tools / outcomes raise it.

3. ATS_FIT (1-10)
   Does the resume use the job's keywords (without obvious stuffing) and
   vary action verbs across bullets? A resume that misses 50%+ of the
   job's must-have keywords is <= 5.

4. HUMAN_VOICE (1-10)
   Penalize AI-tell phrasing per the rules below. Specific bans:
{voice_rule}

For each axis return:
- `score`: a JSON NUMBER literal between 1 and 10 inclusive (e.g. 7,
  not "7" — never a string-quoted number).
- `rationale`: a CONCISE one-sentence rationale (<= 300 chars) citing
  the specific evidence that drove the score. Do NOT pad with
  summaries or restate the score.

=== INPUT ===

TARGET JOB:
title: {job_title}
company: {job_company}
required_skills: {job_skills}
description (truncated): {job_desc}

SOURCE CV (parsed, JSON):
{source_cv}

GENERATED RESUME (JSON):
{generated_resume}
"""


def _voice_rule_block() -> str:
    """Inline the canonical voice rule so the judge cites the same standards we enforce."""
    return HUMAN_VOICE_RULE.strip()


# ─── Programmatic pre-checks ─────────────────────────────────────────────────

_BANNED_VOICE_TOKENS = (
    "leverage", "leveraging", "leveraged", "utilize", "utilizing", "utilized",
    "synergy", "synergize", "robust", "seamless", "seamlessly", "delve",
    "delving", "unleash", "elevate", "cutting-edge", "world-class",
    "best-in-class", "game-changer", "paradigm", "tapestry", "holistic",
    "spearhead", "embark", "foster", "transformative", "thought leader",
    "results-driven", "demonstrating",
)


def _flatten_text(d) -> str:
    """Flatten any JSON-serializable structure into one lowercase string for substring search."""
    if isinstance(d, str):
        return d.lower()
    return json.dumps(d, default=str).lower()


def banned_phrase_hits(generated_resume: dict) -> list[str]:
    """Return banned voice tokens that occur in the generated resume."""
    text = _flatten_text(generated_resume)
    hits = []
    for tok in _BANNED_VOICE_TOKENS:
        if tok in text:
            hits.append(tok)
    return hits


def _extract_entities(generated_resume: dict) -> list[str]:
    """Pull company + school + degree names out of the generated resume."""
    out: list[str] = []
    for exp in (generated_resume.get("experience") or []):
        if isinstance(exp, dict):
            v = exp.get("company")
            if v:
                out.append(str(v))
    for edu in (generated_resume.get("education") or []):
        if isinstance(edu, dict):
            for k in ("school", "institution", "university"):
                v = edu.get(k)
                if v:
                    out.append(str(v))
    return out


def factuality_check(generated_resume: dict, source_text: str) -> dict:
    """Each entity from the generated resume must appear (case-insensitive) in source CV text."""
    entities = _extract_entities(generated_resume)
    if not entities:
        return {"n_entities": 0, "n_grounded": 0, "ratio": None, "ungrounded": []}
    src = (source_text or "").lower()
    grounded, ungrounded = [], []
    for e in entities:
        if str(e).strip().lower() in src:
            grounded.append(e)
        else:
            ungrounded.append(e)
    return {
        "n_entities": len(entities),
        "n_grounded": len(grounded),
        "ratio": round(len(grounded) / len(entities), 4),
        "ungrounded": ungrounded,
    }


# ─── LLM judge call ──────────────────────────────────────────────────────────

def judge(
    *,
    source_cv: dict,
    job_title: str,
    job_company: str,
    job_skills: Iterable[str],
    job_description: str,
    generated_resume: dict,
) -> JudgeVerdict:
    prompt = JUDGE_PROMPT.format(
        voice_rule=_voice_rule_block(),
        job_title=job_title,
        job_company=job_company or "Unknown",
        job_skills=", ".join(job_skills),
        job_desc=(job_description or "")[:1200],
        source_cv=json.dumps(source_cv, default=str)[:6000],
        generated_resume=json.dumps(generated_resume, default=str)[:6000],
    )
    llm = get_structured_llm(JudgeVerdict, temperature=0.0, max_tokens=1024, task="judge")
    return llm.invoke(prompt)
