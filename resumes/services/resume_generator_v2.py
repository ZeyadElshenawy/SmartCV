"""Section generator — stage 4 of the v2 evidence-first pipeline.

    ingest → extract → FactStore → plan → SECTION GENERATOR (this)
        → review → assemble

The planner already decided WHAT goes where (allocated fact ids per
section, entity ordering, ``metric_fact_ids`` per entity, hedge flags).
This stage decides **HOW it reads** — the prose. Bullets, summary
sentences, the skills line.

The contract is **"prose free, claims bounded, NUMBERS LOCKED"**:

- **PROSE** is free. The generator may rephrase and combine facts into
  natural, compelling bullets ("Built X using Y, achieving Z"). It
  does not quote evidence verbatim.
- **CLAIMS** are bounded. A bullet may only assert things supported
  by the facts allocated to its section/entity in the plan. No new
  claims that no allocated fact supports. (This is enforced
  *softly* — prompt-level; the structural enforcement is on
  numbers, where fabrication is most damaging.)
- **NUMBERS** are locked, in CODE. Every numeric token in a
  generated bullet must trace back to a value in one of the
  bullet's allocated facts (within float epsilon, after K/M
  suffix + comma + percent normalization). A number that doesn't
  trace is a fabrication → bullet regenerates ONCE with explicit
  anti-fabrication feedback; if it still fabricates, the bullet
  is **dropped** and the event is logged. We never emit prose
  with an ungrounded number.

This module is **isolated** — nothing in v1 generation depends on
it; nothing in the v2 fact store, extractors, or planner does
either. The only outward couplings are:
  - ``resumes.services.fact_store`` (read-only — fact + store types)
  - ``resumes.services.resume_planner_v2`` (read-only — plan types)
  - ``profiles.services.llm_engine.get_llm`` (the Groq client wrap;
    same TPM throttle the rest of the system rides through)

The shared bullet-quality rules from v1 (``BULLET_QUALITY_AND_SAFETY_RULES``)
are RESTATED inline in this module — deliberately not imported —
so v2 stays a parallel module that doesn't break if v1 refactors.
The wording is intentionally similar; the constant lives here so
the two pipelines can drift independently.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field

from profiles.services.llm_engine import get_llm
from resumes.services.fact_store import (
    FactRecord,
    FactStore,
    FactType,
)
from resumes.services.resume_planner_v2 import (
    EntityAllocation,
    FactAllocation,
    PlanResult,
    SectionPlan,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


class GeneratedBullet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    fact_ids: list[str] = Field(
        default_factory=list,
        description="Which allocated facts this bullet drew from. "
                    "Survives into the GeneratedResume for "
                    "traceability/defense and downstream grounding "
                    "validation.",
    )
    hedged: bool = False


class EntityBlock(BaseModel):
    """One role / project in the experience or projects sections."""
    model_config = ConfigDict(extra="forbid")
    entity_id: str
    entity_display: str
    anchor_fact_id: Optional[str] = None
    bullets: list[GeneratedBullet] = Field(default_factory=list)


class GeneratedSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: str
    # Exactly one of these will be populated based on section type:
    summary_text: str = ""
    skills_line: str = ""
    bullets: list[GeneratedBullet] = Field(default_factory=list)
    entities: list[EntityBlock] = Field(default_factory=list)
    # Flat list entries for education / certifications.
    lines: list[str] = Field(default_factory=list)


class FabricationEvent(BaseModel):
    """Audit record of every time the number guard caught an
    ungrounded number in generated prose."""
    model_config = ConfigDict(extra="forbid")
    section: str
    entity_id: str = ""
    bullet_text: str
    ungrounded_numbers: list[float]
    action: str = Field(
        ...,
        description='"regenerated" (first catch, retried) | '
                    '"dropped" (retry also fabricated; bullet refused).',
    )


class GeneratedResumeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: dict[str, GeneratedSection]
    fabrication_events: list[FabricationEvent] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Bullet-quality rules (restated; deliberately not imported from v1).
# ---------------------------------------------------------------------------


_BULLET_QUALITY_RULES = """ACHIEVEMENT SHAPE — every bullet reads as a RESULT, not a duty:
  [Strong action verb] + [What you did, briefly] + [Concrete outcome — a result, a deliverable, a metric, a scope marker]

NUMBERS POLICY (the load-bearing guard — read this twice):
- Use ONLY numbers that appear in the facts I'm giving you for this bullet. The numbers I give you are LOCKED. You may include them, omit them, or reorder them, but you may NOT invent, round, or "approximately" emit a number that isn't in the facts. A bullet with an invented number will be DROPPED by the post-check; you don't get partial credit.
- If a fact is HEDGED (marked hedged=true), the number must be phrased with a qualifier ("~", "around", "approximately"). Never present a hedged number as a hard figure.
- If you have NO real number for an item, write the bullet qualitatively. An honest qualitative bullet beats a fake quantitative one.

WEAK SHAPES TO AVOID:
- "Contributed to / Applied / Worked on / Helped with / Participated in / Took part in / Engaged in / Involved in <X>" — pure duty framing; lead with the verb of action and end on the outcome.
- "Responsible for / Tasked with / In charge of / Duties included <X>" — same fix.
- "Developed and evaluated <models>" / "Built and tested <X>" — compound verbs that hide the outcome.

PHRASING:
- Lead with a different action verb than the previous bullet in this role/project.
- 1-2 lines, ~15-25 words. No walls of text, no one-word bullets.
- Don't quote evidence verbatim — rephrase. But don't pad."""


# ---------------------------------------------------------------------------
# Number extraction + normalization + grounding.
# ---------------------------------------------------------------------------


# Capture decimals, integers with optional commas, optional K/M/B suffix,
# optional trailing %. Word-boundary on both sides so "Python3" doesn't
# get "3" extracted as a number. Order matters: longer alternatives first.
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9.])"
    r"-?\d+(?:,\d{3})*(?:\.\d+)?[kKmMbB]?%?"
    r"(?![A-Za-z0-9])",
)


def _normalize_number(s: str) -> Optional[float]:
    """Parse a raw numeric token to a float. Handles:
      - integers ("337")
      - decimals ("0.89", "0.6027")
      - percentages ("40%", "4.9%")
      - K/M/B suffixes ("541K" → 541000, "1.2M" → 1_200_000)
      - thousand-separators ("1,470")

    Returns ``None`` on parse failure. Percentage values are returned
    as the bare number (40, not 0.40) — that matches how
    ``fact.value`` is stored when ``fact.unit == "%"``.
    """
    if not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return None
    if s.endswith("%"):
        s = s[:-1].strip()
    multiplier = 1.0
    if s and s[-1] in "kKmMbB":
        suffix = s[-1].lower()
        s = s[:-1].strip()
        multiplier = {"k": 1_000.0, "m": 1_000_000.0, "b": 1_000_000_000.0}[suffix]
    s = s.replace(",", "")
    try:
        return float(s) * multiplier
    except ValueError:
        return None


def _numbers_in(text: str) -> set[float]:
    """Set of normalized numeric values in `text`. Used both to scan
    bullets and to harvest the "allowed numbers" pool from facts."""
    out: set[float] = set()
    if not isinstance(text, str):
        return out
    for m in _NUMBER_RE.finditer(text):
        n = _normalize_number(m.group(0))
        if n is not None:
            out.add(n)
    return out


def _allowed_numbers_from_facts(facts: list[FactRecord]) -> set[float]:
    """Aggregate the set of numbers any of these facts is permitted
    to reference. Combines:
      - ``fact.value`` when set
      - numbers parsed out of ``fact.claim``
      - numbers parsed out of ``fact.evidence_quote``

    This is the "grounded numbers" pool against which a generated
    bullet's numbers are checked. A bullet from THIS allocation can
    only use numbers from THIS pool — a different entity's facts
    aren't in here, so cross-entity number leakage is structurally
    impossible (the generator's caller passes only the facts
    allocated to one bullet/section)."""
    allowed: set[float] = set()
    for f in facts:
        if f.value is not None:
            try:
                allowed.add(float(f.value))
            except (TypeError, ValueError):
                pass
        allowed |= _numbers_in(f.claim)
        allowed |= _numbers_in(f.evidence_quote)
    return allowed


_NUMBER_EPSILON = 1e-3


def _ungrounded_numbers(
    text: str, allowed: set[float],
) -> list[float]:
    """Numbers in `text` that don't match any value in `allowed`
    (within ``_NUMBER_EPSILON`` for float comparison). Empty list
    means the prose is fully grounded numerically."""
    bad: list[float] = []
    for n in _numbers_in(text):
        if any(abs(n - a) <= _NUMBER_EPSILON for a in allowed):
            continue
        # Also accept if the bullet's number equals an allowed number
        # divided/multiplied by 100 — a hedged 0.89 ROC-AUC vs the
        # prose's "89%". This is a TIGHT compatibility window; only
        # exact ×100 / ÷100 with very loose tolerance is accepted.
        if any(
            abs(n * 100 - a) <= _NUMBER_EPSILON
            or abs(n / 100 - a) <= _NUMBER_EPSILON
            for a in allowed if a != 0
        ):
            continue
        bad.append(n)
    return bad


# ---------------------------------------------------------------------------
# LLM-call helper (mockable per call site).
# ---------------------------------------------------------------------------


def _llm_call(prompt: str, *, task: str = "resume_gen_v2",
              temperature: float = 0.5, max_tokens: int = 800) -> str:
    """Plain-text LLM call. Mockable in tests via
    ``patch.object(resume_generator_v2, "_llm_call", ...)``."""
    llm = get_llm(temperature=temperature, max_tokens=max_tokens, task=task)
    resp = llm.invoke([HumanMessage(content=prompt)])
    content = getattr(resp, "content", None)
    if isinstance(content, str):
        return content.strip()
    # LangChain occasionally returns list-shaped content for multimodal —
    # collapse to text best-effort.
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        ).strip()
    return str(content or "").strip()


# ---------------------------------------------------------------------------
# Per-bullet generate + verify + (regenerate-once-then-drop) flow.
# ---------------------------------------------------------------------------


def _fact_brief(f: FactRecord) -> str:
    """One-line representation of a fact for prompts. Includes the
    locked numeric value/unit explicitly so the model has the canonical
    form to copy from, plus the hedge flag."""
    parts = [f"type={f.type.value}", f"claim={f.claim!r}"]
    if f.value is not None:
        parts.append(f"value={f.value} unit={f.unit or ''}")
    if f.hedged:
        parts.append("HEDGED")
    return "- " + "; ".join(parts)


def _bullet_prompt(
    *, role_hint: str, facts: list[FactRecord], regen_feedback: str = "",
) -> str:
    facts_block = "\n".join(_fact_brief(f) for f in facts) or "(no facts)"
    feedback = ""
    if regen_feedback:
        feedback = (
            "\n\nREGENERATION — your previous output failed the number guard:\n"
            + regen_feedback
            + "\nRewrite the bullet using ONLY the numbers in the facts above. "
            "Remove any number you can't trace to a fact. A qualitative "
            "bullet without numbers is acceptable.\n"
        )
    return (
        f"You are writing ONE resume bullet for {role_hint}. Use the facts below.\n"
        f"{_BULLET_QUALITY_RULES}\n\n"
        f"FACTS (the ONLY content + numbers you may draw from):\n{facts_block}\n"
        f"{feedback}\n"
        "Return JUST the bullet text — one line, no quotes, no bullet character, "
        "no commentary."
    )


def _generate_one_bullet(
    *,
    section: str,
    entity_id: str,
    role_hint: str,
    facts: list[FactRecord],
    allowed_numbers: set[float],
    events: list[FabricationEvent],
) -> Optional[GeneratedBullet]:
    """Generate one bullet, run the number guard, regenerate once on
    failure, drop on persistent failure.

    Returns ``None`` when the bullet was dropped — caller skips it.
    Mutates ``events`` to record every fabrication catch.
    """
    # --- First attempt ---
    text = _llm_call(_bullet_prompt(role_hint=role_hint, facts=facts))
    bad = _ungrounded_numbers(text, allowed_numbers)
    if not bad:
        hedged = any(f.hedged for f in facts)
        return GeneratedBullet(
            text=text,
            fact_ids=[f.id for f in facts],
            hedged=hedged,
        )

    # --- Catch + regenerate once ---
    logger.warning(
        "resume_generator_v2: number guard caught ungrounded number(s) "
        "%s in %s[%s] first attempt; regenerating once. Bullet was: %r",
        bad, section, entity_id or "-", text[:120],
    )
    events.append(FabricationEvent(
        section=section, entity_id=entity_id,
        bullet_text=text, ungrounded_numbers=bad, action="regenerated",
    ))
    regen_feedback = (
        f"You included number(s) {bad} which do NOT appear in the provided "
        "facts. Remove them, or use only the given numbers."
    )
    text2 = _llm_call(
        _bullet_prompt(role_hint=role_hint, facts=facts,
                       regen_feedback=regen_feedback),
    )
    bad2 = _ungrounded_numbers(text2, allowed_numbers)
    if not bad2:
        hedged = any(f.hedged for f in facts)
        return GeneratedBullet(
            text=text2,
            fact_ids=[f.id for f in facts],
            hedged=hedged,
        )

    # --- Persistent failure → DROP ---
    logger.warning(
        "resume_generator_v2: number guard caught ungrounded %s in regen too "
        "for %s[%s]; DROPPING bullet. Original: %r; Regen: %r",
        bad2, section, entity_id or "-", text[:120], text2[:120],
    )
    events.append(FabricationEvent(
        section=section, entity_id=entity_id,
        bullet_text=text2, ungrounded_numbers=bad2, action="dropped",
    ))
    return None


# ---------------------------------------------------------------------------
# Resolution helper — turn FactAllocation → FactRecord against the store.
# ---------------------------------------------------------------------------


def _resolve_facts(
    store: FactStore, allocations: list[FactAllocation],
) -> list[FactRecord]:
    """Map plan FactAllocations to live FactRecords from the store.
    Silently skips any id the store doesn't have (defensive — should
    never happen, planner verifies)."""
    out: list[FactRecord] = []
    for fa in allocations or []:
        f = store.get(fa.fact_id)
        if f is not None:
            out.append(f)
    return out


def _resolve_ids(store: FactStore, ids: list[str]) -> list[FactRecord]:
    out: list[FactRecord] = []
    for fid in ids or []:
        f = store.get(fid)
        if f is not None:
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# Section generators
# ---------------------------------------------------------------------------


def _generate_skills_line(
    store: FactStore, section: SectionPlan,
) -> GeneratedSection:
    """Skills section: no LLM — just emit the planner-allocated skill
    names as a comma-separated line. The planner already ranked them
    by JD relevance + reliability and applied the cap."""
    facts = _resolve_facts(store, section.facts)
    names = [f.claim.strip() for f in facts if f.claim and f.claim.strip()]
    return GeneratedSection(
        section="skills",
        skills_line=", ".join(names),
        bullets=[
            GeneratedBullet(text=name, fact_ids=[fid])
            for name, fid in zip(names, [f.id for f in facts])
        ],
    )


def _generate_education_lines(
    store: FactStore, section: SectionPlan,
) -> GeneratedSection:
    """Education: render each allocated education fact as a line.
    Schools/degrees are short-form facts — no LLM needed."""
    facts = _resolve_facts(store, section.facts)
    lines = [f.claim.strip() for f in facts if f.claim and f.claim.strip()]
    return GeneratedSection(
        section="education",
        lines=lines,
        bullets=[
            GeneratedBullet(text=ln, fact_ids=[fid], hedged=f.hedged)
            for ln, fid, f in zip(lines, [f.id for f in facts], facts)
        ],
    )


def _generate_certification_lines(
    store: FactStore, section: SectionPlan,
) -> GeneratedSection:
    """Same shape as education — a flat list."""
    facts = _resolve_facts(store, section.facts)
    lines = [f.claim.strip() for f in facts if f.claim and f.claim.strip()]
    return GeneratedSection(
        section="certifications",
        lines=lines,
        bullets=[
            GeneratedBullet(text=ln, fact_ids=[fid], hedged=f.hedged)
            for ln, fid, f in zip(lines, [f.id for f in facts], facts)
        ],
    )


def _generate_summary(
    store: FactStore,
    section: SectionPlan,
    *,
    job_title: str,
    job_company: str,
    events: list[FabricationEvent],
) -> GeneratedSection:
    """Summary: one short paragraph drawing on the plan's marquee facts.
    Single LLM call; same number guard."""
    facts = _resolve_facts(store, section.facts)
    if not facts:
        return GeneratedSection(section="summary", summary_text="")
    allowed = _allowed_numbers_from_facts(facts)
    role_hint = (
        f"the professional summary for a {job_title} role"
        + (f" at {job_company}" if job_company else "")
    )
    bullet = _generate_one_bullet(
        section="summary", entity_id="",
        role_hint=role_hint, facts=facts,
        allowed_numbers=allowed, events=events,
    )
    return GeneratedSection(
        section="summary",
        summary_text=bullet.text if bullet else "",
        bullets=[bullet] if bullet else [],
    )


def _generate_entity_bullets(
    store: FactStore,
    entity: EntityAllocation,
    *,
    section: str,
    job_title: str,
    events: list[FabricationEvent],
) -> EntityBlock:
    """One role/project's bullets. Each child fact-allocation
    generates ONE bullet; the entity's metrics are merged into the
    allowed-numbers pool AND offered as additional source facts so
    the LLM can weave them in.

    Caller (build) ensures ``entity.metric_fact_ids`` comes from the
    plan, which came from ``store.metrics_for(entity_id)`` — i.e.
    only THIS entity's metrics. No cross-entity number can leak in.
    """
    child_facts = _resolve_facts(store, entity.facts)
    metric_facts = _resolve_ids(store, entity.metric_fact_ids)
    anchor = store.get(entity.anchor_fact_id) if entity.anchor_fact_id else None

    # Anchor + metrics are always in the allowed pool — every bullet
    # at this entity may draw on them.
    base_facts = [f for f in (anchor,) if f is not None]
    bullets: list[GeneratedBullet] = []
    role_hint = (
        f"a {section} entry: {entity.entity_display!r}"
        + (f" (targeting {job_title})" if job_title else "")
    )
    # Distribute metrics across child facts so each bullet pairs with
    # ~one supporting metric. If there are more metrics than child
    # facts, leftover metrics get appended to the last bullet's pool.
    n_children = len(child_facts) or 1
    metric_assignment: list[list[FactRecord]] = [[] for _ in range(n_children)]
    for i, m in enumerate(metric_facts):
        metric_assignment[i % n_children].append(m)

    if not child_facts and metric_facts:
        # No child achievements but we have metrics — make ONE bullet
        # from anchor + all metrics.
        facts_for_bullet = base_facts + metric_facts
        allowed = _allowed_numbers_from_facts(facts_for_bullet)
        b = _generate_one_bullet(
            section=section, entity_id=entity.entity_id,
            role_hint=role_hint, facts=facts_for_bullet,
            allowed_numbers=allowed, events=events,
        )
        if b is not None:
            bullets.append(b)
    else:
        for i, child in enumerate(child_facts):
            facts_for_bullet = base_facts + [child] + metric_assignment[i]
            allowed = _allowed_numbers_from_facts(facts_for_bullet)
            b = _generate_one_bullet(
                section=section, entity_id=entity.entity_id,
                role_hint=role_hint, facts=facts_for_bullet,
                allowed_numbers=allowed, events=events,
            )
            if b is not None:
                bullets.append(b)

    return EntityBlock(
        entity_id=entity.entity_id,
        entity_display=entity.entity_display,
        anchor_fact_id=entity.anchor_fact_id,
        bullets=bullets,
    )


def _generate_experience(
    store: FactStore, section: SectionPlan,
    *, job_title: str, events: list[FabricationEvent],
) -> GeneratedSection:
    blocks = [
        _generate_entity_bullets(
            store, ent, section="experience",
            job_title=job_title, events=events,
        )
        for ent in section.entities
    ]
    return GeneratedSection(section="experience", entities=blocks)


def _generate_projects(
    store: FactStore, section: SectionPlan,
    *, job_title: str, events: list[FabricationEvent],
) -> GeneratedSection:
    blocks = [
        _generate_entity_bullets(
            store, ent, section="projects",
            job_title=job_title, events=events,
        )
        for ent in section.entities
    ]
    return GeneratedSection(section="projects", entities=blocks)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_resume_v2(
    store: FactStore,
    plan: PlanResult,
    *,
    job_title: str = "",
    job_company: str = "",
) -> GeneratedResumeV2:
    """Render the plan into prose.

    Args:
      store: the FactStore the plan was built from (must contain
        every fact id the plan references).
      plan: structured allocations from ``resume_planner_v2.build_plan``.
      job_title / job_company: framing for the summary's role hint.

    Returns: ``GeneratedResumeV2`` with per-section prose + a
    ``fabrication_events`` log of any numbers the guard caught.
    """
    events: list[FabricationEvent] = []
    sections: dict[str, GeneratedSection] = {}

    summary = plan.sections.get("summary")
    if summary is not None:
        sections["summary"] = _generate_summary(
            store, summary,
            job_title=job_title, job_company=job_company, events=events,
        )

    skills = plan.sections.get("skills")
    if skills is not None:
        sections["skills"] = _generate_skills_line(store, skills)

    experience = plan.sections.get("experience")
    if experience is not None:
        sections["experience"] = _generate_experience(
            store, experience, job_title=job_title, events=events,
        )

    projects = plan.sections.get("projects")
    if projects is not None:
        sections["projects"] = _generate_projects(
            store, projects, job_title=job_title, events=events,
        )

    edu = plan.sections.get("education")
    if edu is not None:
        sections["education"] = _generate_education_lines(store, edu)

    creds = plan.sections.get("certifications")
    if creds is not None:
        sections["certifications"] = _generate_certification_lines(store, creds)

    return GeneratedResumeV2(sections=sections, fabrication_events=events)
