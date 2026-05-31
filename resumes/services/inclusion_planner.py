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

import json
import logging
import re
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

# "Base" tech that almost every project lists, so a project whose tech
# list only intersects with the JD on THESE tokens has no discriminating
# JD signal. Used by the project tech-overlap filter to drop projects
# like "Brain Tumor Classification App" (HTML/CSS/JavaScript/Swiper —
# zero discriminating overlap with a tabular Data Scientist JD) or
# "Apotheosis Traffic Sign Detection" (Python/OpenCV/Jupyter — only
# Python overlaps and Python is base for any DS JD).
_BASE_TECH_CANON = frozenset({
    'python', 'jupyternotebook', 'notebook', 'jupyter',
    'html', 'css', 'javascript', 'js', 'typescript', 'ts',
    'json', 'yaml', 'xml',
    'git', 'github', 'gitlab', 'bitbucket',
    'vscode', 'pycharm', 'intellij', 'eclipse',
})

# Fix #5 (audit report §6.5, 2026-05-30) — project depth bonus.
#
# Tokens that reliably indicate a non-trivial system was built (backend
# frameworks, databases, vector stores, LLM SDKs, infra/orchestration,
# MLOps) — NOT thin frontends or tutorial wrappers. Review periodically;
# adding a tutorial-common framework here will over-promote shallow
# projects. The depth bonus is bounded (DEPTH_CAP=4) and only fires on
# projects that already pass the keep filter, so adding a borderline
# token has limited blast radius — but it can still tilt rank order
# unexpectedly. Two dry-runs (improving_resume_output/depth_bonus_dryrun*)
# validated this exact set across 3 real profile families.
SYSTEM_STACK_CANON = frozenset({
    # Backend frameworks
    'django', 'flask', 'fastapi', 'rails', 'spring', 'springboot',
    'express', 'nestjs', 'nextjs', 'nuxt',
    # Databases
    'postgresql', 'postgres', 'mysql', 'mariadb', 'sqlserver', 'mongodb',
    'redis', 'cassandra', 'dynamodb', 'sqlite', 'prisma',
    # Vector / LLM / RAG
    'pgvector', 'pinecone', 'weaviate', 'chroma', 'qdrant', 'milvus',
    'langchain', 'llamaindex', 'openai', 'anthropic', 'groq',
    'huggingface', 'llama', 'rag', 'whisper',
    # Cloud / infra / distributed
    'kubernetes', 'k8s', 'docker', 'terraform', 'aws', 'gcp', 'azure',
    'kafka', 'spark', 'pyspark', 'airflow', 'dbt', 'snowflake', 'databricks',
    'prometheus', 'grafana', 'githubactions',
    # ML serving / MLOps
    'mlflow', 'kubeflow', 'tritoninferenceserver', 'triton',
    # Mobile / Devices
    'reactnative', 'flutter', 'swift', 'kotlin',
    # Auth/Edge — 'firebaseauth' and 'firestore' DELIBERATELY EXCLUDED:
    # they are sub-components of one Firebase service. Counting all three
    # of {firebase, firebaseauth, firestore} would let a single service
    # decision collect +3, defeating the anti-sprawl design of the cap.
    'supabase', 'firebase',
    # Systems-y
    'rust', 'go', 'nodejs',
})

# Project-depth bonus caps. SYSTEM_STACK_CAP bounds the per-token
# component; DEPTH_CAP bounds the total (URL + system tokens). Both
# tuned against the dry-runs so JD relevance (disc * 2 = up to 6+ for
# a strongly-aligned project) stays dominant over a maxed-out depth
# bonus (4). See improving_resume_output/depth_bonus_dryrun_v2.py.
_DEPTH_CAP = 4
_DEPTH_URL_BONUS = 1
_DEPTH_SYSTEM_STACK_CAP = 4


def _project_depth_bonus(project: dict, jd_skill_canon: set[str]) -> tuple[int, list[str]]:
    """Compute the additive depth bonus for one project.

    Returns ``(depth, fired_tokens)`` where ``fired_tokens`` is the list
    of project-tech entries that fed the SYSTEM_STACK part of the score
    (for log visibility). Tokens already in ``jd_skill_canon`` are
    excluded because they're already counted by ``disc * 2`` — no
    double-counting. Tokens in ``_BASE_TECH_CANON`` are excluded by
    construction (those are the universal-noise filter).

    The bonus is bounded: the per-token sum caps at ``_DEPTH_SYSTEM_STACK_CAP``
    and the URL+stack total caps at ``_DEPTH_CAP``. A single rich project
    cannot dominate a moderately JD-aligned project — verified by the
    dry-runs in ``improving_resume_output/depth_bonus_dryrun_v2.py``.
    """
    if not isinstance(project, dict):
        return 0, []
    url_part = _DEPTH_URL_BONUS if project.get('url') else 0
    techs = project.get('technologies') or []
    if not isinstance(techs, list):
        techs = []
    seen: set[str] = set()
    fired: list[str] = []
    for t in techs:
        c = _canonical(str(t or ''))
        if not c:
            continue
        if c in jd_skill_canon or c in _BASE_TECH_CANON:
            continue
        if c in SYSTEM_STACK_CANON and c not in seen:
            seen.add(c)
            fired.append(str(t))
    stack_part = min(len(seen), _DEPTH_SYSTEM_STACK_CAP)
    return min(url_part + stack_part, _DEPTH_CAP), fired


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


def _discriminating_tech_overlap(
    project_techs: Any, jd_skill_canon: set[str]
) -> tuple[int, list[str]]:
    """Count how many of a project's declared technologies match a JD
    skill, ignoring "base" tech that doesn't distinguish projects
    (Python, Jupyter, HTML/CSS/JS, Git, …).

    PR2b Fix A — base-tech filter is now JD-AWARE: a tech token in
    ``_BASE_TECH_CANON`` is still counted if the JD explicitly listed
    it. The intuition: "Python" is uninformative for most JDs (every
    project has it), but if the JD says "Python developer needed",
    Python becomes a real signal for that JD and a project listing it
    should not be penalised. The behaviour for non-JD-mentioned base
    tech (the original ``Python``/``Jupyter``/``Git`` exclusion) is
    unchanged.

    Returns ``(overlap_count, jd_rescued_tokens)``. ``jd_rescued_tokens``
    is the list of tech entries that were counted ONLY because they
    were JD-mentioned (i.e. they would have been filtered out under the
    pre-Fix-A rule). The caller logs them for diagnostic visibility —
    see PIPELINE_ANALYSIS §5 / PR2b Step 3 spec.
    """
    if not isinstance(project_techs, list):
        return 0, []
    count = 0
    jd_rescued: list[str] = []
    for t in project_techs:
        if not t:
            continue
        original = str(t)
        c = _canonical(original)
        if not c:
            continue
        is_base = c in _BASE_TECH_CANON
        in_jd = c in jd_skill_canon
        if is_base and in_jd:
            count += 1
            jd_rescued.append(original)
            continue
        if is_base:
            continue  # base tech, JD didn't ask for it → don't count
        if in_jd:
            count += 1
    return count, jd_rescued


def _scan_for_jd_skills_in_profile_text(
    data: dict, jd_skills: list[str], already_in_list_canon: set[str],
) -> list[str]:
    """Find JD must-have / nice-to-have skills that aren't in the
    candidate's formal skills list but ARE mentioned (word-boundary)
    inside their experience/project/certification descriptions.

    This surfaces JD-aligned skills like TensorFlow / MLflow /
    Hugging Face that the user's DEPI experience description mentions
    but their skills field doesn't list. The recruiter is going to
    keyword-scan for these, so we want them on the resume.

    Only returns skills with at least one word-boundary match — never
    invents claims.
    """
    if not jd_skills:
        return []
    # Bundle every text-bearing chunk of the profile into a single
    # lowercase search corpus. JSON-dump preserves field boundaries
    # which gives the regex word-boundary something to anchor on.
    corpus_obj = {
        'experiences': data.get('experiences') or [],
        'projects': data.get('projects') or [],
        'certifications': data.get('certifications') or [],
        'volunteering': data.get('volunteering') or data.get('volunteer_experience') or [],
    }
    corpus = json.dumps(corpus_obj, default=str).lower()
    found: list[str] = []
    for skill in jd_skills:
        if not skill:
            continue
        c = _canonical(skill)
        if not c or c in already_in_list_canon:
            continue
        pattern = rf"(?<!\w){re.escape(skill.lower())}(?!\w)"
        try:
            if re.search(pattern, corpus):
                found.append(skill)
        except re.error:
            # Defensive — re.escape should make this unreachable.
            if skill.lower() in corpus:
                found.append(skill)
    return found


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

    # Backfill: JD must-have / nice-to-have skills that are mentioned in
    # the candidate's experience / project / certification descriptions
    # but never made it into their formal skills list. The gap analyzer
    # often misses these (e.g. "TensorFlow" appears in a DEPI bullet but
    # not in skills). Recruiters keyword-scan for these, so surface them
    # — but only with a real word-boundary text match (no fabrication).
    text_jd_skills = _scan_for_jd_skills_in_profile_text(
        data, must_jd + nice_jd, seen_canon,
    )
    for skill in text_jd_skills:
        if len(skills_to_list) >= _MAX_SKILLS_LIST:
            break
        _add(skill)
    if text_jd_skills:
        logger.info(
            "inclusion_planner: backfilled %d JD skill(s) from profile text: %s",
            len(text_jd_skills), text_jd_skills,
        )

    # Round 1.5: backfill from the candidate's FORMAL skills list (not
    # just bullet text). Often the gap analyzer misses skills like
    # "Kubernetes" or "Terraform" because the JD says "container
    # orchestration tools" rather than the exact word — but the
    # candidate has the skill listed and the recruiter is going to
    # keyword-scan for the exact term. Pull any candidate skill whose
    # name overlaps a JD must-have / nice-to-have token (3+ chars) and
    # isn't already on the list.
    jd_tokens: set[str] = set()
    for s in (must_jd + nice_jd):
        if not s:
            continue
        for tok in s.lower().split():
            t = tok.strip(',.()/').strip()
            if len(t) >= 3:
                jd_tokens.add(t)
    profile_skills_backfilled: list[str] = []
    for entry in (data.get('skills') or []):
        if len(skills_to_list) >= _MAX_SKILLS_LIST:
            break
        name = entry.get('name') if isinstance(entry, dict) else str(entry or '')
        name = (name or '').strip()
        if not name:
            continue
        c = _canonical(name)
        if c in seen_canon:
            continue
        # Multi-word match: any token in the skill name overlaps a JD token.
        skill_tokens = {t.strip(',.()/').strip()
                        for t in name.lower().split() if t}
        if skill_tokens & jd_tokens:
            _add(name)
            profile_skills_backfilled.append(name)
    if profile_skills_backfilled:
        logger.info(
            "inclusion_planner: backfilled %d profile skill(s) by JD-token match: %s",
            len(profile_skills_backfilled), profile_skills_backfilled,
        )

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

    # ---- Projects: rank by tech-overlap + retrieval hits -----------------
    # Tech overlap is the strong signal: a project with TensorFlow / MLflow
    # / Pandas in its tech list is JD-relevant for a Data Scientist role
    # even if its description never re-mentioned those words. Retrieval
    # hits are the weak signal: "preprocessing" / "classification" in a
    # project description gives chunk matches, but those terms appear in
    # off-topic projects too (Brain Tumor CNN, Traffic Sign Detection).
    # Combine: relevance_score = max(retrieval_hits, discriminating_tech * 2).
    jd_skill_canon = {
        _canonical(s) for s in (must_jd + nice_jd + matched_must + matched_nice + matched_v1)
        if s
    }
    project_candidates: list[ProjectPlan] = []
    proj_disc_overlap: dict[int, int] = {}  # for the keep filter + tie-break
    for i, proj in enumerate(data.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        sid = f'project:{i}'
        anchor_chunks = sorted(by_source.get(sid, set()))
        retrieval_score = relevance.get(sid, 0)
        disc, jd_rescued = _discriminating_tech_overlap(
            proj.get('technologies'), jd_skill_canon,
        )
        proj_disc_overlap[i] = disc
        # Fix #5 — depth bonus. Capped, additive, never dominant: the
        # base relevance (disc*2 or retrieval) stays the lead signal.
        # Reach: see _project_depth_bonus docstring.
        depth_bonus, depth_tokens = _project_depth_bonus(proj, jd_skill_canon)
        combined = max(retrieval_score, disc * 2) + depth_bonus
        # PR2b Fix A — log when a project's score was raised by the
        # JD-aware base-tech rescue. Helps future debugging trace why a
        # project was kept (e.g. "Brain Tumor App" surviving on AI Dev
        # JD because TensorFlow was JD-mentioned, even though TensorFlow
        # would normally be in _BASE_TECH_CANON).
        if jd_rescued:
            logger.info(
                "inclusion_planner: project %r disc_overlap raised to %d via "
                "JD-tech %s (was filtered out by _BASE_TECH_CANON)",
                (proj.get('name') or proj.get('title') or '').strip(),
                disc, jd_rescued,
            )
        if depth_bonus:
            logger.info(
                "inclusion_planner: project %r depth_bonus=+%d (url=%s, system_tokens=%s)",
                (proj.get('name') or proj.get('title') or '').strip(),
                depth_bonus, 'Y' if proj.get('url') else 'N', depth_tokens,
            )
        project_candidates.append(ProjectPlan(
            profile_index=i,
            name=(proj.get('name') or proj.get('title') or '').strip(),
            url=(proj.get('url') or '').strip(),
            relevance_score=combined,
            evidence_anchored_chunk_ids=anchor_chunks,
        ))
    # Sort: relevance desc, then disc desc (Fix #5 tie-breaker — when two
    # projects tie on the new score, the one more JD-aligned by raw
    # discriminating tech wins), then profile_index asc (preserve insertion
    # order on a true tie). The _MIN_PROJECTS top-up below iterates this
    # list, so it picks up depth-bonused projects in the new order too.
    project_candidates.sort(
        key=lambda p: (
            -p.relevance_score,
            -proj_disc_overlap.get(p.profile_index, 0),
            p.profile_index,
        )
    )

    # Filter: KEEP only projects that pass at least one of:
    #   (a) discriminating tech overlap >= 1 (project's declared tech
    #       intersects the JD on a non-base skill — strong signal), OR
    #   (b) retrieval score >= 3 (description-only matches need to be
    #       multi-skill to count — single weak hits were what kept
    #       Brain Tumor and Apotheosis in v1).
    # Fall back to the _MIN_PROJECTS floor if too few projects pass so
    # we never emit an empty section.
    def _passes_jd_filter(p: ProjectPlan) -> bool:
        disc = proj_disc_overlap.get(p.profile_index, 0)
        if disc >= 1:
            return True
        retrieval_only = relevance.get(f'project:{p.profile_index}', 0)
        return retrieval_only >= 3

    relevant = [p for p in project_candidates if _passes_jd_filter(p)]
    projects_plan = relevant[:_MAX_PROJECTS]
    if len(projects_plan) < _MIN_PROJECTS:
        # Top up from the remaining candidates in rank order.
        kept_ids = {p.profile_index for p in projects_plan}
        needed = _MIN_PROJECTS - len(projects_plan)
        for p in project_candidates:
            if needed <= 0:
                break
            if p.profile_index in kept_ids:
                continue
            projects_plan.append(p)
            kept_ids.add(p.profile_index)
            needed -= 1

    # ---- Certifications: keep only matched ones --------------------------
    # PR2b Fix B — strict canonical equality misses obvious matches like
    # "NLP" (canon='nlp') vs "Natural Language Processing in TensorFlow"
    # (canon='naturallanguageprocessingintensorflow'). We now also accept
    # substring containment in either direction with a 4-char minimum on
    # the SKILL canon so 2-letter skills like "AI"/"ML" don't trigger
    # off accidental letter overlap. Keep the (name, canon) pairs so the
    # log can show which skill matched.
    matched_skill_pairs: list[tuple[str, str]] = []
    for raw in (matched_must + matched_nice + matched_v1):
        c = _canonical(raw)
        if c:
            matched_skill_pairs.append((raw, c))
    matched_cert_canon = {c for _, c in matched_skill_pairs}

    def _fuzzy_skill_match(cert_canon: str) -> tuple[bool, str]:
        """Return (matched, skill_name). The 4-char minimum on the skill
        canon side prevents short acronyms from triggering off letter
        overlap (the "AI" in "Air Conditioning" risk)."""
        for skill_name, skill_canon in matched_skill_pairs:
            if len(skill_canon) < 4:
                continue
            if skill_canon in cert_canon or cert_canon in skill_canon:
                return True, skill_name
        return False, ''

    certs_plan: list[str] = []
    for cert in (data.get('certifications') or []):
        if not isinstance(cert, dict):
            continue
        name = (cert.get('name') or '').strip()
        if not name:
            continue
        # Three acceptance paths now:
        #   1. exact canon match (original rule, kept for cheap fast path)
        #   2. fuzzy substring match (PR2b Fix B)
        #   3. cert chunk surfaced in per-skill retrieval (original
        #      fallback — matches when SOMETHING JD-relevant lives in
        #      the cert's description/issuer text)
        c = _canonical(name)
        cert_sid = name  # indexer uses cert name as source_id
        if c in matched_cert_canon:
            certs_plan.append(name)
            continue
        fuzzy_ok, matched_skill = _fuzzy_skill_match(c)
        if fuzzy_ok:
            certs_plan.append(name)
            logger.info(
                "inclusion_planner: cert %r kept via fuzzy match on "
                "skill %r (canon overlap)",
                name, matched_skill,
            )
            continue
        if relevance.get(cert_sid, 0) > 0:
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
