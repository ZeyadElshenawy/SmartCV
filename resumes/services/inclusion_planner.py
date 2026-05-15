"""Deterministic inclusion planner for resume tailoring v2.

The LLM is responsible for *writing* bullets; this module is responsible
for deciding *what* makes the cut. Keeping selection rules out of the
prompt has two payoffs:

1. The LLM gets an authoritative spec ("include these N projects, write
   bridges for these K skills, never claim these other skills") so it
   stops second-guessing the user's evidence and stops manufacturing
   filler bullets that the validator would just strip out.
2. The rules are unit-testable. We can prove "low-proximity missing
   must-have skills never appear in the Skills section" without
   poking the LLM.

Inputs:
    - profile           UserProfile (uses `.data_content`)
    - job               Job (uses `.extracted_skills_tiers`)
    - gap_analysis      analysis.models.GapAnalysis (uses v2 fields)
    - per_skill_ev      {skill_name: [CandidateEvidence, ...]} from the
                        retriever — drives evidence-anchoring decisions.

Output:
    InclusionPlan dataclass — passed to the prompt builder and to the
    grounding validator.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Threshold tuning — see plan doc for the rationale.
_BRIDGE_PROXIMITY_MIN = 0.60     # ≥0.6 + bridge_hint → write a bridge bullet
_DROP_PROXIMITY_MAX = 0.30       # <0.3 with no bridge → hard "do not claim"
_MAX_SKILLS_LIST = 20            # ATS-friendly Skills section cap
_MAX_ADJACENT_SKILLS = 5         # matched skills not on the JD we still surface
_MAX_PROJECTS = 6
_MIN_PROJECTS = 3                # never trim below 3 even if JD relevance is low


@dataclass
class ExperiencePlan:
    """One entry in InclusionPlan.experiences — preserves the order/index
    of the source profile.experiences so the LLM can re-write bullets
    without losing the role-to-bullet binding."""

    profile_index: int
    title: str
    company: str
    duration: str
    # `chunk_id`s of bullets this role contributed to per-skill retrieval.
    # The LLM should treat these as "pinned" — keep them in the output,
    # rewriting voice but not content.
    evidence_anchored_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class ProjectPlan:
    profile_index: int
    name: str
    url: str
    # Number of retrieved-chunk hits across all JD skills — used as the
    # relevance score for ranking.
    relevance_score: int
    evidence_anchored_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class InclusionPlan:
    skills_to_list: list[str]
    experiences: list[ExperiencePlan]
    projects: list[ProjectPlan]
    certifications: list[str]
    include_volunteer: bool
    include_publications: bool
    include_awards: bool
    summary_hints: list[str]
    bridge_bullet_skills: list[dict]   # [{name, proximity, bridge_hint, source_quote}, ...]
    drop_skills: list[str]
    # Diagnostics: surfaced for logs / debugging / the validator's grounding
    # check. Not consumed by the prompt itself.
    matched_must_have: list[str] = field(default_factory=list)
    matched_nice_to_have: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _names_only(items: Any) -> list[str]:
    """v2 GapAnalysis buckets carry dicts with `.name`; v1 ones carry bare
    strings. Tolerate both."""
    out: list[str] = []
    for entry in items or []:
        if isinstance(entry, dict):
            n = (entry.get('name') or '').strip()
        else:
            n = str(entry or '').strip()
        if n:
            out.append(n)
    return out


def _canonical(text: str) -> str:
    """Lowercase + alphanum-only — used as a dedup/equality key across the
    LinkedIn-typo case ('Almansour Automative' ≈ 'Al-Mansour Automotive')
    that already bit us in signal_merger."""
    if not text:
        return ''
    return ''.join(c.lower() for c in text if c.isalnum())


def _gap_jd_skills(gap_analysis, job) -> tuple[list[str], list[str]]:
    """Return (must_have, nice_to_have) ordered, canonical-deduped."""
    tiers = (job.extracted_skills_tiers or {}) if job else {}
    must = list(tiers.get('must_have') or [])
    nice = list(tiers.get('nice_to_have') or [])
    # Fall back to v1 flat list when tiers are empty — older Job rows
    # don't have the v2 dict populated.
    if not must and not nice and job and (job.extracted_skills or []):
        must = list(job.extracted_skills)
    return must, nice


def _skill_chunks_by_source(
    per_skill_ev: dict[str, list],
) -> dict[str, set[str]]:
    """Index: source_id (e.g. 'experience:0') -> set of chunk_ids that
    surfaced for any JD skill. Drives the "evidence-anchored" pins."""
    by_source: dict[str, set[str]] = defaultdict(set)
    for chunks in per_skill_ev.values():
        for c in chunks:
            sid = c.source_id or c.chunk_id.split(':bullet:')[0]
            by_source[sid].add(c.chunk_id)
    return by_source


def _retrieval_counter(
    per_skill_ev: dict[str, list],
) -> Counter:
    """How often each source_id surfaced across all JD skill queries.
    Higher = more JD-relevant. Used to rank projects."""
    counter: Counter = Counter()
    for chunks in per_skill_ev.values():
        for c in chunks:
            sid = c.source_id or ''
            if sid:
                counter[sid] += 1
    return counter


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

def build_inclusion_plan(
    profile,
    job,
    gap_analysis,
    per_skill_ev: dict[str, list],
) -> InclusionPlan:
    data = profile.data_content or {}
    by_source = _skill_chunks_by_source(per_skill_ev)
    relevance = _retrieval_counter(per_skill_ev)

    must_jd, nice_jd = _gap_jd_skills(gap_analysis, job)

    # ---- Skills section ---------------------------------------------------
    matched_must = _names_only(getattr(gap_analysis, 'matched_must_have', None))
    matched_nice = _names_only(getattr(gap_analysis, 'matched_nice_to_have', None))
    matched_v1 = _names_only(getattr(gap_analysis, 'matched_skills', None))

    skills_to_list: list[str] = []
    seen_canon: set[str] = set()

    def _add(name: str):
        c = _canonical(name)
        if not c or c in seen_canon:
            return
        seen_canon.add(c)
        skills_to_list.append(name.strip())

    # Order: matched must-have (in JD order if possible), matched nice,
    # then a small adjacent-matched bucket. JD order matters because the
    # ATS scanner often weighs skills that appear in the same order as
    # the JD higher.
    matched_must_canon = {_canonical(s): s for s in matched_must}
    matched_nice_canon = {_canonical(s): s for s in matched_nice}

    for skill in must_jd:
        c = _canonical(skill)
        if c in matched_must_canon:
            _add(matched_must_canon[c])
    for skill in nice_jd:
        c = _canonical(skill)
        if c in matched_nice_canon:
            _add(matched_nice_canon[c])
    # Adjacent matched skills the JD didn't name explicitly — small bucket
    # to round out the profile.
    adjacent_added = 0
    for skill in matched_v1:
        if adjacent_added >= _MAX_ADJACENT_SKILLS:
            break
        c = _canonical(skill)
        if c in seen_canon:
            continue
        _add(skill)
        adjacent_added += 1

    skills_to_list = skills_to_list[:_MAX_SKILLS_LIST]

    # ---- Bridge / drop decisions for missing must-haves ------------------
    bridge_bullet_skills: list[dict] = []
    drop_skills: list[str] = []
    for entry in (getattr(gap_analysis, 'missing_must_have', None) or []):
        if not isinstance(entry, dict):
            continue
        name = (entry.get('name') or '').strip()
        if not name:
            continue
        prox = float(entry.get('proximity') or 0.0)
        bridge = (entry.get('bridge_hint') or '').strip()
        if prox >= _BRIDGE_PROXIMITY_MIN and bridge:
            bridge_bullet_skills.append({
                'name': name,
                'proximity': prox,
                'bridge_hint': bridge,
                'source_quote': (entry.get('source_quote') or '').strip(),
            })
        elif prox < _DROP_PROXIMITY_MAX and not bridge:
            drop_skills.append(name)

    # ---- Experiences: keep all, annotate evidence-anchored bullets -------
    experiences_plan: list[ExperiencePlan] = []
    for i, exp in enumerate(data.get('experiences') or []):
        if not isinstance(exp, dict):
            continue
        sid = f'experience:{i}'
        experiences_plan.append(ExperiencePlan(
            profile_index=i,
            title=(exp.get('title') or exp.get('role') or '').strip(),
            company=(exp.get('company') or '').strip(),
            duration=(exp.get('duration') or '').strip(),
            evidence_anchored_chunk_ids=sorted(by_source.get(sid, set())),
        ))

    # ---- Projects: rank by retrieval-hit count, keep top N ---------------
    project_candidates: list[ProjectPlan] = []
    for i, proj in enumerate(data.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        sid = f'project:{i}'
        anchor_chunks = sorted(by_source.get(sid, set()))
        project_candidates.append(ProjectPlan(
            profile_index=i,
            name=(proj.get('name') or proj.get('title') or '').strip(),
            url=(proj.get('url') or '').strip(),
            relevance_score=relevance.get(sid, 0),
            evidence_anchored_chunk_ids=anchor_chunks,
        ))
    project_candidates.sort(key=lambda p: (-p.relevance_score, p.profile_index))
    # Always keep at least _MIN_PROJECTS even if relevance is uniformly 0
    # (e.g. when per_skill_ev returned nothing useful) — better to surface
    # something than emit a resume with no projects.
    projects_plan = project_candidates[:_MAX_PROJECTS]
    if len(projects_plan) < _MIN_PROJECTS:
        projects_plan = project_candidates[:_MIN_PROJECTS]

    # ---- Certifications: keep only matched ones --------------------------
    matched_cert_canon = {
        _canonical(s) for s in (matched_must + matched_nice + matched_v1)
    }
    certs_plan: list[str] = []
    for cert in (data.get('certifications') or []):
        if not isinstance(cert, dict):
            continue
        name = (cert.get('name') or '').strip()
        if not name:
            continue
        # Two acceptance paths: the cert name matches a matched skill,
        # OR the cert chunk surfaced in per-skill retrieval (which means
        # SOMETHING JD-relevant lives in its description/issuer).
        c = _canonical(name)
        cert_sid = name  # indexer uses cert name as source_id
        if c in matched_cert_canon or relevance.get(cert_sid, 0) > 0:
            certs_plan.append(name)

    # ---- Volunteer / publications / awards: include iff any chunk from
    # that source_type surfaced in retrieval. ----------------------------
    surfaced_types: set[str] = set()
    for chunks in per_skill_ev.values():
        for c in chunks:
            surfaced_types.add(c.source_type)

    # ---- Summary hints: top 3 retrieved phrases across all skills --------
    seen_chunks: set[str] = set()
    summary_hints: list[str] = []
    for chunks in per_skill_ev.values():
        for c in chunks:
            if c.chunk_id in seen_chunks:
                continue
            seen_chunks.add(c.chunk_id)
            # Strip newlines; cap to a reasonable phrase length so the
            # hints stay quotable in the LLM prompt.
            phrase = ' '.join(c.text.split())[:200]
            if phrase and len(summary_hints) < 3:
                summary_hints.append(phrase)

    plan = InclusionPlan(
        skills_to_list=skills_to_list,
        experiences=experiences_plan,
        projects=projects_plan,
        certifications=certs_plan,
        include_volunteer='volunteer' in surfaced_types,
        include_publications='publication' in surfaced_types,
        include_awards='award' in surfaced_types,
        summary_hints=summary_hints,
        bridge_bullet_skills=bridge_bullet_skills,
        drop_skills=drop_skills,
        matched_must_have=matched_must,
        matched_nice_to_have=matched_nice,
    )

    logger.info(
        "inclusion_planner: skills=%d exp=%d proj=%d certs=%d "
        "bridges=%d drops=%d",
        len(plan.skills_to_list), len(plan.experiences), len(plan.projects),
        len(plan.certifications), len(plan.bridge_bullet_skills),
        len(plan.drop_skills),
    )
    return plan
