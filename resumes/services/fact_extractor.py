"""Atomic-fact extractor — the "map" stage of the v2 evidence-first pipeline.

Pipeline shape (this module = step 2):

    ingest → EXTRACT atomic facts → FactStore → global plan
        → section generation → review/regen → assemble

This is **the first v2 component that calls the LLM** → the first
real fabrication surface. The load-bearing property is structural:

    every FactRecord's evidence_quote MUST be text that actually
    appears in the source.

The LLM is asked to return grounded facts; the **post-LLM evidence
guard** in this module verifies each returned fact's quote against
the source text by case-insensitive whitespace-normalized substring
match. Any fact whose quote isn't in the source is DROPPED and
logged — the LLM cannot launder an invented claim into a fact, just
as the role-identity guard in v1 prevents phantom companies from
landing on the resume.

This module is **isolated** from the v1 pipeline. Nothing in
resume_generator / views / inclusion_planner / normalizer depends
on it. It only writes into a ``FactStore`` instance the caller
provides.

Source coverage:
  - ``github_readme`` — full implementation (this task).
  - ``old_cv`` / ``kaggle`` / ``scholar`` / ``linkedin`` — stubs
    that raise ``NotImplementedError`` with the documented
    reliability rule for each. One at a time, after the pattern
    is proven.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from profiles.services.llm_engine import get_structured_llm
from resumes.services.fact_store import (
    FactRecord,
    FactStore,
    FactType,
    SourceReliability,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM-output schemas (NOT exposed publicly; we hydrate to FactRecord)
# ---------------------------------------------------------------------------


class _ExtractedFactRaw(BaseModel):
    """LLM-returned shape for one extracted fact.

    Separate from FactRecord because (a) the LLM doesn't set id/source/
    entity_id (we do, post-call, deterministically), and (b) keeping
    these decoupled means a malformed LLM payload fails on this
    Pydantic schema *before* it can construct a FactRecord with
    half-correct data."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(
        ..., description='One of: "skill" | "achievement" | "metric" | "project" | "credential".'
    )
    claim: str = Field(..., min_length=1)
    value: Optional[float] = None
    unit: Optional[str] = None
    evidence_quote: str = Field(
        ..., min_length=1,
        description=(
            "VERBATIM substring of the source text — will be verified "
            "against the source post-call. Paraphrasing causes the fact "
            "to be DROPPED."
        ),
    )
    hedged: bool = False


class _ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facts: list[_ExtractedFactRaw] = Field(default_factory=list)


class _RepoClassification(BaseModel):
    """Classifier output for original-work-vs-tutorial decision."""
    model_config = ConfigDict(extra="forbid")
    classification: str = Field(
        ..., description='One of: "original" | "tutorial" | "unsure".'
    )
    reasoning: str = Field(default="")


# ---------------------------------------------------------------------------
# Anti-fabrication guard — the structural check.
# Every returned fact's evidence_quote must be substring-present in
# the source after case/whitespace normalization. The prompt asks for
# this; the code enforces it.
# ---------------------------------------------------------------------------


_WS_RE = re.compile(r"\s+")


def _normalize_for_substring(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").strip().lower())


def _evidence_in_source(quote: str, source_text: str) -> bool:
    """Case-insensitive whitespace-normalized substring check.

    Slightly permissive on whitespace (the LLM tends to collapse newlines)
    but DOES NOT allow paraphrasing — every word the LLM cites must
    appear in the source in roughly that order."""
    n_q = _normalize_for_substring(quote)
    if not n_q:
        return False
    n_s = _normalize_for_substring(source_text)
    return n_q in n_s


# Hedging patterns — words/numerics the LLM may have missed flagging.
# Trips even when the LLM returned hedged=False so a sneaky "~89%"
# can't escape into a confident bullet later.
_HEDGE_TOKENS_RE = re.compile(
    r"~\s*\d|\babout\s+\d|\bapproximately\b|\baims?\s+to\b|"
    r"\baround\s+\d|\bnearly\s+\d|\bup\s+to\s+\d|\bapprox\.?\s+\d",
    re.IGNORECASE,
)


def _looks_hedged(quote: str) -> bool:
    return bool(_HEDGE_TOKENS_RE.search(quote or ""))


# ---------------------------------------------------------------------------
# Id generation — stable across runs.
# ---------------------------------------------------------------------------


def _gen_fact_id(source: str, entity_id: str, fact_type: str, claim: str) -> str:
    """Stable hash id from (source, entity_id, type, claim). Same logical
    fact gets the same id on every run, which means re-extracting the
    same README is idempotent at the store layer (dedup will collapse
    re-additions without churn)."""
    key = f"{source}|{entity_id}|{fact_type}|{claim}".lower()
    return hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _parse_fact_type(raw: str) -> Optional[FactType]:
    """Map LLM-returned type string to FactType. Unknown → None
    (the fact is dropped)."""
    if not isinstance(raw, str):
        return None
    try:
        return FactType(raw.strip().lower())
    except ValueError:
        return None


def _build_fact_from_raw(
    raw,
    *,
    source_text: str,
    source_tag: str,
    entity_id: str,
    entity_display: str,
    reliability: SourceReliability,
    surface: str,
) -> Optional[FactRecord]:
    """Run the shared per-fact pipeline (type parse → evidence guard →
    metric shape check → hedge detection → FactRecord construction) for
    one raw LLM-returned fact.

    Returns ``None`` when any guard drops the fact. Every drop is logged
    with the ``surface`` tag so a future log scan can tell where each
    fabrication slipped through.

    This is THE single chokepoint every source extractor routes through.
    Adding a new safety check here applies to all five sources at once."""
    fact_type = _parse_fact_type(getattr(raw, "type", ""))
    if fact_type is None:
        logger.warning(
            "fact_extractor[%s]: dropped fact with unknown type=%r claim=%r",
            surface, getattr(raw, "type", "?"),
            (getattr(raw, "claim", "") or "")[:80],
        )
        return None

    # POLICY: SKILL facts are profile-level — "Python" the user knows is
    # the same fact whether it came from a CV bullet, a GitHub README, or
    # a Kaggle bio. Binding skills to one source's entity_id would prevent
    # cross-source dedup-collapse in the store (the user's explicit
    # requirement: "same skill from CV (user_original) and GitHub repo
    # (tutorial_derived) collapses to ONE fact keeping user_original").
    # METRICS / PROJECTS / ROLES / ACHIEVEMENTS / EDUCATION / CREDENTIALS
    # stay bound to their entity — those are item-scoped claims and
    # cross-attachment is the bug we're preventing.
    if fact_type == FactType.SKILL:
        entity_id = ""
        entity_display = ""

    quote = getattr(raw, "evidence_quote", "") or ""
    if not _evidence_in_source(quote, source_text):
        logger.warning(
            "fact_extractor[%s]: dropped fabricated fact "
            "(evidence_quote not in source) type=%s claim=%r quote=%r "
            "entity=%r",
            surface, fact_type.value,
            (getattr(raw, "claim", "") or "")[:80],
            quote[:120], entity_id,
        )
        return None

    if fact_type == FactType.METRIC and getattr(raw, "value", None) is None:
        logger.warning(
            "fact_extractor[%s]: dropped metric fact with no numeric value "
            "claim=%r entity=%r",
            surface, (getattr(raw, "claim", "") or "")[:80], entity_id,
        )
        return None

    hedged = bool(getattr(raw, "hedged", False)) or _looks_hedged(quote)
    claim = (getattr(raw, "claim", "") or "").strip()

    try:
        return FactRecord(
            id=_gen_fact_id(source_tag, entity_id, fact_type.value, claim),
            type=fact_type,
            claim=claim,
            value=getattr(raw, "value", None),
            unit=getattr(raw, "unit", None),
            entity_id=entity_id,
            entity_display=entity_display,
            source=source_tag,
            source_reliability=reliability,
            evidence_quote=quote,
            hedged=hedged,
        )
    except Exception as exc:  # noqa: BLE001 — FactRecord validator rejected the fact
        logger.warning(
            "fact_extractor[%s]: FactRecord validation rejected fact (%s) "
            "type=%s claim=%r entity=%r",
            surface, type(exc).__name__,
            fact_type.value, claim[:80], entity_id,
        )
        return None


def _normalize_entity_token(s: str) -> str:
    """Lowercase whitespace-collapse — used for entity_id key parts so
    "Almansour Automotive" and "  ALMANSOUR  AUTOMOTIVE  " produce the
    same join key."""
    return _WS_RE.sub(" ", (s or "").strip().lower())


# ---------------------------------------------------------------------------
# Repo classification: original-work vs tutorial. Fail-safe → tutorial.
# ---------------------------------------------------------------------------


_CLASSIFY_PROMPT = """You are classifying a GitHub repository as ORIGINAL WORK or TUTORIAL/COURSE-FOLLOWING.

TUTORIAL signals (treat the repo as tutorial-derived when ANY apply):
- The README says "following along with", "based on the course", "from the tutorial", "guided project", "walkthrough".
- A named course / platform is mentioned: DataCamp, Coursera, Udemy, Udacity, Kaggle Learn, fast.ai, freeCodeCamp, "Andrew Ng", "CS229", "ng-mlspecialization", similar.
- Repo name itself signals a course: e.g. "datacamp-projects", "andrew-ng-cnn", "coursera-ml".
- The work is a re-implementation of a paper's published method without novel modification.
- Notebook(s) read as a step-by-step walkthrough rather than an investigation.

ORIGINAL signals:
- Custom architecture or novel dataset.
- "I built", "I designed", "we shipped"; clear personal ownership of decisions.
- Production deployment, paying users, or non-trivial scale.
- README explains DESIGN choices, not just steps.

When you cannot tell, return "unsure". The system defaults "unsure" to tutorial-derived — that is the safer label and is correct policy; DO NOT guess "original" when you are not confident.

REPO METADATA:
{metadata_block}

README:
{readme_text}

Return:
{{
  "classification": "original" | "tutorial" | "unsure",
  "reasoning": "<one sentence>"
}}
"""


def _classify_repo_with_llm(
    readme_text: str, metadata: dict
) -> _RepoClassification:
    """LLM call for the original-vs-tutorial classification. Isolated in
    its own function so tests can mock the network round-trip cleanly."""
    metadata_lines = []
    for k in ("repo_url", "name", "language", "stars", "forks", "fork_of"):
        v = metadata.get(k)
        if v is not None and v != "":
            metadata_lines.append(f"- {k}: {v}")
    metadata_block = "\n".join(metadata_lines) or "(none provided)"
    prompt = _CLASSIFY_PROMPT.format(
        metadata_block=metadata_block,
        readme_text=(readme_text or "")[:6000],
    )
    llm = get_structured_llm(
        _RepoClassification, temperature=0.0, max_tokens=400, task="fact_extractor",
    )
    return llm.invoke(prompt)


def _reliability_from_classification(cls: _RepoClassification) -> SourceReliability:
    """Map classifier output to SourceReliability with the fail-safe.

    'original'  → USER_ORIGINAL
    'tutorial'  → TUTORIAL_DERIVED
    'unsure'    → TUTORIAL_DERIVED   (fail-safe)
    anything else, including a malformed value → TUTORIAL_DERIVED.
    NEVER promote to USER_ORIGINAL on ambiguity."""
    raw = (getattr(cls, "classification", "") or "").strip().lower()
    if raw == "original":
        return SourceReliability.USER_ORIGINAL
    # Any other value, INCLUDING 'unsure' and any malformed input,
    # falls through to tutorial_derived.
    return SourceReliability.TUTORIAL_DERIVED


# ---------------------------------------------------------------------------
# Fact extraction — the actual LLM "map" call.
# ---------------------------------------------------------------------------


_EXTRACT_PROMPT = """You are extracting ATOMIC FACTS from a GitHub README so they can be used to build a resume.

You MUST follow these rules:

1. For each fact, your `evidence_quote` MUST be a VERBATIM substring of the README (a continuous span of text that actually appears, case-insensitive and whitespace-tolerant). Your output will be programmatically verified against the README; any fact whose evidence_quote is NOT a substring of the source will be DROPPED.

2. Extract only what is STATED in the README. Do not infer. Do not paraphrase. Do not synthesize a metric from non-metric text.

3. Fact types you may emit:
   - "project": one fact summarizing what the repo IS (a single project fact per README).
   - "skill": each named tool/language/library/framework. evidence_quote is the substring naming it.
   - "achievement": each concrete outcome (shipped, deployed, served users, integrated). NOT a vague claim like "demonstrates ML skill".
   - "metric": each NUMBER WITH A UNIT. Set value (float) and unit (string). If the source hedges the number ("~89%", "about 200ms", "approximately", "aims to", "up to"), set hedged=true.

4. If a metric appears without an explicit unit, infer the unit ONLY when it is unambiguous in context (e.g. "ROC-AUC of 0.89" → unit="ROC-AUC"). Otherwise leave unit null.

5. No achievement-flavored skills, no skill-flavored achievements. Atomic = one claim per fact.

README:
{readme_text}

Return a list of facts as JSON.
"""


def _extract_facts_with_llm(readme_text: str) -> _ExtractionResult:
    """LLM call for the atomic-fact map step. Mockable in tests."""
    prompt = _EXTRACT_PROMPT.format(readme_text=(readme_text or "")[:6000])
    llm = get_structured_llm(
        _ExtractionResult, temperature=0.1, max_tokens=2048, task="fact_extractor",
    )
    return llm.invoke(prompt)


# ---------------------------------------------------------------------------
# Public API — GitHub README extractor (reference implementation).
# ---------------------------------------------------------------------------


def _short_repo_id(repo_url: str) -> str:
    """Extract 'owner/name' from a GitHub URL for the source tag.
    Falls back to the raw URL when parsing fails."""
    if not isinstance(repo_url, str):
        return ""
    m = re.search(r"github\.com[/:]([^/]+/[^/?#]+?)(?:\.git)?(?:[/?#]|$)", repo_url)
    return m.group(1) if m else repo_url


def extract_from_github_readme(
    *,
    repo_url: str,
    repo_display: str,
    readme_text: str,
    metadata: Optional[dict] = None,
) -> list[FactRecord]:
    """Extract atomic facts from a GitHub README.

    Args:
      repo_url: the canonical repo URL — becomes the FactRecord's
        ``entity_id``. The stable join key the v2 generator will use
        to look up "what numbers can I cite for this project?".
      repo_display: human display name (e.g. "Healthcare Prediction
        (DEPI)"). Stored separately so display formatting never
        couples to the join key.
      readme_text: full README text.
      metadata: optional repo metadata (name, language, stars, etc.) —
        used by the classifier.

    Returns:
      A list of FactRecord instances. Each fact:
        - has entity_id = repo_url
        - has entity_display = repo_display
        - has source = "github_readme:<owner/name>"
        - has source_reliability from the classifier (original →
          USER_ORIGINAL; tutorial / unsure / malformed → TUTORIAL_DERIVED)
        - has a verified evidence_quote (substring-present in
          readme_text — facts that fail this check are DROPPED)

    Does NOT add to a store. Caller chooses which store to merge into
    (use ``extract_into_store`` for the one-shot combine).
    """
    metadata = metadata or {}
    source_tag = f"github_readme:{_short_repo_id(repo_url) or repo_url}"

    # Step 1 — classify the repo. Fail-safe to TUTORIAL_DERIVED on any
    # ambiguity or LLM hiccup.
    try:
        cls = _classify_repo_with_llm(readme_text, metadata)
        reliability = _reliability_from_classification(cls)
        logger.info(
            "fact_extractor: classified repo %r as %s -> reliability=%s",
            repo_url,
            getattr(cls, "classification", "?"),
            reliability.value,
        )
    except Exception as exc:  # noqa: BLE001 — classifier failure must not promote reliability
        logger.warning(
            "fact_extractor: repo classifier failed (%s); defaulting to "
            "tutorial_derived for repo %r",
            type(exc).__name__, repo_url,
        )
        reliability = SourceReliability.TUTORIAL_DERIVED

    # Step 2 — extract facts.
    try:
        extraction = _extract_facts_with_llm(readme_text)
    except Exception as exc:  # noqa: BLE001 — extraction failure → no facts (caller can retry)
        logger.warning(
            "fact_extractor: extraction failed (%s) for repo %r; returning "
            "no facts",
            type(exc).__name__, repo_url,
        )
        return []

    raw_facts = getattr(extraction, "facts", None) or []
    facts: list[FactRecord] = []
    seen_keys: set[tuple] = set()

    for raw in raw_facts:
        fact = _build_fact_from_raw(
            raw,
            source_text=readme_text, source_tag=source_tag,
            entity_id=repo_url, entity_display=repo_display,
            reliability=reliability, surface="github_readme",
        )
        if fact is None:
            continue
        local_key = (fact.type, _normalize_for_substring(fact.claim))
        if local_key in seen_keys:
            continue
        seen_keys.add(local_key)
        facts.append(fact)

    logger.info(
        "fact_extractor: extracted %d fact(s) from repo %r (reliability=%s)",
        len(facts), repo_url, reliability.value,
    )
    return facts


# ---------------------------------------------------------------------------
# Stubs — one at a time after the GitHub pattern is proven.
# ---------------------------------------------------------------------------


# ===========================================================================
# old_cv — uploaded CV / résumé text
# ===========================================================================


class _CVRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: str
    title: str
    facts: list[_ExtractedFactRaw] = Field(default_factory=list)


class _CVEducation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    institution: str
    degree: str
    facts: list[_ExtractedFactRaw] = Field(default_factory=list)


class _CVExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: list[_CVRole] = Field(default_factory=list)
    education: list[_CVEducation] = Field(default_factory=list)
    free_facts: list[_ExtractedFactRaw] = Field(
        default_factory=list,
        description="Skills and other facts not bound to a single role/education.",
    )


_CV_EXTRACT_PROMPT = """You are extracting ATOMIC FACTS from a user's CV / résumé text.

You MUST follow these rules:

1. Each fact's `evidence_quote` is a VERBATIM substring of the CV text
   (case-insensitive and whitespace-tolerant). Any fact whose quote is
   not in the source is DROPPED.

2. Output is STRUCTURED — roles/education are explicit entities that
   group their facts:
   - "roles[]" — each entry has company + title (verbatim from the CV)
     and its facts (achievement / metric / skill / etc.).
   - "education[]" — each entry has institution + degree (verbatim)
     and its facts.
   - "free_facts[]" — skills and other facts NOT attached to one role
     or one education entry.

3. Fact types per the schema:
   - "skill", "achievement", "role", "education", "metric", "project",
     "credential".
   - METRIC must have a numeric value. Hedged numbers ("~", "about",
     "aims to", "approximately", "up to") set hedged=true.

4. NEVER invent a role, company, institution, or degree that is not
   in the CV. The code verifies role/education entities against the
   source text and drops invented ones entirely.

CV TEXT:
{cv_text}
"""


def _extract_cv_with_llm(cv_text: str) -> _CVExtraction:
    prompt = _CV_EXTRACT_PROMPT.format(cv_text=(cv_text or "")[:8000])
    llm = get_structured_llm(
        _CVExtraction, temperature=0.1, max_tokens=2048, task="fact_extractor",
    )
    return llm.invoke(prompt)


def extract_from_old_cv(
    *, cv_text: str, profile_owner: str = "",
) -> list[FactRecord]:
    """Extract atomic facts from the user's uploaded CV / résumé.

    Reliability rule: ``USER_ORIGINAL`` — the CV is a self-stated
    artifact the user owns. Metrics in CV bullets ("Reduced load time
    40%") are still subject to the evidence guard, but their
    reliability is the user-original tier.

    Entity binding:
      - Roles → ``cv:role|<normalized_company>|<normalized_title>``
      - Education → ``cv:edu|<normalized_institution>|<normalized_degree>``
      - Skills / free facts → empty entity_id (not bound to a single
        item; metrics among free facts will be rejected by FactRecord
        because metrics require an entity).
    """
    source_text = cv_text or ""
    source_tag = "old_cv"
    try:
        extraction = _extract_cv_with_llm(source_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "fact_extractor[old_cv]: extraction failed (%s); returning no facts",
            type(exc).__name__,
        )
        return []

    facts: list[FactRecord] = []
    seen_keys: set[tuple] = set()
    surface = "old_cv"

    def _emit(raw, entity_id, entity_display):
        fact = _build_fact_from_raw(
            raw,
            source_text=source_text, source_tag=source_tag,
            entity_id=entity_id, entity_display=entity_display,
            reliability=SourceReliability.USER_ORIGINAL,
            surface=surface,
        )
        if fact is None:
            return
        key = (fact.type, _normalize_for_substring(fact.claim), entity_id)
        if key in seen_keys:
            return
        seen_keys.add(key)
        facts.append(fact)

    # Roles. Entity itself must be substring-present in the CV (verifies
    # the LLM didn't invent a role). Match on company OR title — either
    # substring is enough; entity is dropped only when neither appears.
    for role in (getattr(extraction, "roles", None) or []):
        company = (getattr(role, "company", "") or "").strip()
        title = (getattr(role, "title", "") or "").strip()
        c_ok = bool(company) and _evidence_in_source(company, source_text)
        t_ok = bool(title) and _evidence_in_source(title, source_text)
        if not (c_ok or t_ok):
            logger.warning(
                "fact_extractor[old_cv]: dropped invented role (neither "
                "company nor title found in CV): company=%r title=%r",
                company, title,
            )
            continue
        entity_id = "cv:role|{c}|{t}".format(
            c=_normalize_entity_token(company),
            t=_normalize_entity_token(title),
        )
        entity_display = f"{title} @ {company}".strip(" @")
        for raw in (getattr(role, "facts", None) or []):
            _emit(raw, entity_id, entity_display)

    # Education entities. Same substring check on institution / degree.
    for edu in (getattr(extraction, "education", None) or []):
        institution = (getattr(edu, "institution", "") or "").strip()
        degree = (getattr(edu, "degree", "") or "").strip()
        i_ok = bool(institution) and _evidence_in_source(institution, source_text)
        d_ok = bool(degree) and _evidence_in_source(degree, source_text)
        if not (i_ok or d_ok):
            logger.warning(
                "fact_extractor[old_cv]: dropped invented education entry: "
                "institution=%r degree=%r",
                institution, degree,
            )
            continue
        entity_id = "cv:edu|{i}|{d}".format(
            i=_normalize_entity_token(institution),
            d=_normalize_entity_token(degree),
        )
        entity_display = f"{degree} @ {institution}".strip(" @")
        for raw in (getattr(edu, "facts", None) or []):
            _emit(raw, entity_id, entity_display)

    # Free facts — skills, etc. No entity binding; metric facts here
    # are rejected by FactRecord (metric requires non-empty entity_id),
    # which is the desired behavior — a "metric" floating in the CV
    # without a role context can't be safely cited.
    for raw in (getattr(extraction, "free_facts", None) or []):
        _emit(raw, entity_id="", entity_display="")

    logger.info(
        "fact_extractor[old_cv]: extracted %d fact(s) from CV (owner=%r)",
        len(facts), profile_owner,
    )
    return facts


# ===========================================================================
# kaggle — competition ranks (platform-verified) + bio (user-original) +
# forked notebooks (tutorial-derived). Reliability is per-fact here.
#
# STARVED until ``profiles/services/kaggle_aggregator.py`` captures
# per-competition names, per-notebook titles, fork flags, and bio
# prose. The current ``kaggle_signals`` payload stores ONLY category
# counts + tier + medal totals (no names, no prose) — there is
# literally nothing for the LLM to ground evidence_quotes against.
# This extractor logic is correct and ready; the data isn't. When the
# caller passes a current-shape blob the extractor logs a starved
# warning and returns ``[]``. See the trace dated 2026-06-01.
# ===========================================================================


def _kaggle_metadata_is_starved(metadata) -> bool:
    """True when ``metadata`` carries the current kaggle_aggregator
    digest shape (no per-competition list, no per-notebook list).

    Detection key: in the digest, ``metadata['competitions']`` is a
    ``KaggleCategory`` dict ``{count, tier, medals}``. In the rich
    shape the extractor needs, it would be a list of competition
    dicts (each with a ``name`` field). Same idea for notebooks.

    When ``metadata`` is None or not a dict, returns False — the
    extractor proceeds with its existing LLM-on-text flow (the path
    the rich-blob tests exercise). The starved-bail fires only when
    the caller HAS handed us a payload AND it's the digest shape.
    """
    if not isinstance(metadata, dict):
        return False
    comp = metadata.get('competitions')
    nbs = metadata.get('notebooks')
    # Rich shape: at least one of competitions/notebooks is a list.
    if isinstance(comp, list) or isinstance(nbs, list):
        return False
    # Digest shape: counts-only dict for either.
    comp_is_digest = (
        isinstance(comp, dict) and 'count' in comp and 'medals' in comp
    )
    nbs_is_digest = (
        isinstance(nbs, dict) and 'count' in nbs and 'medals' in nbs
    )
    return comp_is_digest or nbs_is_digest


class _KaggleCompetition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    facts: list[_ExtractedFactRaw] = Field(default_factory=list)


class _KaggleNotebook(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    is_forked: bool = Field(
        default=False,
        description=(
            "TRUE when the notebook is a fork / copy of someone else's "
            "work, or when the notebook describes itself as following a "
            "tutorial. Forked notebook facts get TUTORIAL_DERIVED "
            "reliability — they are not the user's original output."
        ),
    )
    facts: list[_ExtractedFactRaw] = Field(default_factory=list)


class _KaggleExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    competitions: list[_KaggleCompetition] = Field(default_factory=list)
    notebooks: list[_KaggleNotebook] = Field(default_factory=list)
    profile_facts: list[_ExtractedFactRaw] = Field(
        default_factory=list,
        description=(
            "Profile-level facts: tier (Grandmaster/Master/Expert), bio "
            "prose, top skills. The code classifies each by FactType to "
            "decide reliability — CREDENTIAL → PLATFORM_VERIFIED; SKILL "
            "and ACHIEVEMENT and others → USER_ORIGINAL (bio prose)."
        ),
    )


_KAGGLE_EXTRACT_PROMPT = """You are extracting ATOMIC FACTS from a Kaggle profile / competition page.

Rules:

1. Each fact's `evidence_quote` is a VERBATIM substring of the source text. The code verifies this and DROPS any non-matching fact.

2. The output is STRUCTURED:
   - "competitions[]": one entry per competition the user participated in. `name` is the competition title (verbatim). `facts[]` are the things about THIS competition (rank, medal, score, dataset size).
   - "notebooks[]": one entry per notebook. `title` is the notebook title (verbatim). `is_forked` MUST be true if the notebook signals it is a fork, a copy of someone else's work, or a "follow along with this tutorial" walkthrough. When unsure, set is_forked=true (the safer label).
   - "profile_facts[]": tier (Grandmaster/Master/Expert/Contributor), bio prose, broad skills.

3. Fact-type guidance:
   - Ranks ("Top 3%", "Silver Medal"), tier (Grandmaster), gold/silver/bronze medals → type="credential".
   - Numeric scores (private leaderboard 0.84, dataset size 50k rows) → type="metric" with value+unit.
   - Bio claims and self-described skills → type="skill" or type="achievement" as appropriate.

4. NEVER invent a competition, notebook, rank, or medal that is not in the source. The code verifies competition names and notebook titles against the source text.

KAGGLE PROFILE/PAGE TEXT:
{profile_text}
"""


def _extract_kaggle_with_llm(profile_text: str) -> _KaggleExtraction:
    prompt = _KAGGLE_EXTRACT_PROMPT.format(profile_text=(profile_text or "")[:8000])
    llm = get_structured_llm(
        _KaggleExtraction, temperature=0.1, max_tokens=2048, task="fact_extractor",
    )
    return llm.invoke(prompt)


def _kaggle_reliability_for_fact(
    raw_type: str, is_forked: bool,
) -> SourceReliability:
    """Per-fact reliability for Kaggle.

    - Forked notebook → TUTORIAL_DERIVED for everything on it (not the
      user's original work).
    - Competition credential (rank / medal / tier) → PLATFORM_VERIFIED
      (Kaggle assigned it; externally confirmed).
    - Everything else → USER_ORIGINAL (bio prose / self-described).

    Fail-safe: an unknown / unparseable type from a non-forked context
    defaults to USER_ORIGINAL — not PLATFORM_VERIFIED — so an
    unrecognized fact can never silently promote to the highest tier.
    """
    if is_forked:
        return SourceReliability.TUTORIAL_DERIVED
    if (raw_type or "").strip().lower() == "credential":
        return SourceReliability.PLATFORM_VERIFIED
    return SourceReliability.USER_ORIGINAL


def extract_from_kaggle(
    *, profile_url: str, profile_text: str, metadata: Optional[dict] = None,
) -> list[FactRecord]:
    """Extract atomic facts from a Kaggle profile or competition page.

    Reliability rule (per-fact, NOT per-source):
      - Competition credentials (rank / medal / tier) → ``PLATFORM_VERIFIED``
      - Bio / notebook prose / broad skill claims      → ``USER_ORIGINAL``
      - Anything on a forked / copied notebook         → ``TUTORIAL_DERIVED``

    Fail-safe: notebooks where the LLM is unsure get ``is_forked=true``
    in the prompt, defaulting to ``TUTORIAL_DERIVED``. Unknown fact
    types on a non-forked context default to ``USER_ORIGINAL`` — not
    ``PLATFORM_VERIFIED``.

    Entity binding:
      - Competitions → ``kaggle:competition|<normalized_name>``
      - Notebooks    → ``kaggle:notebook|<normalized_title>``
      - Profile facts → empty entity_id.

    STARVED-BLOB GUARD: when ``metadata`` is the current
    ``kaggle_aggregator`` digest shape (counts/tiers only, no per-
    competition list), the extractor cannot ground any evidence_quote
    — the source has no prose. We log a clear "extend the aggregator"
    warning and return ``[]`` rather than silently succeed-with-zero-
    facts (which would look like a broken extractor).
    """
    if _kaggle_metadata_is_starved(metadata):
        logger.warning(
            "fact_extractor[kaggle]: STARVED — kaggle_signals payload "
            "carries only category counts + medal totals (no per-"
            "competition names, no per-notebook titles, no fork flags, "
            "no bio prose). Extend profiles/services/kaggle_aggregator.py "
            "to capture the rich Kaggle profile fields before this "
            "extractor can produce facts. Returning []. "
            "(See trace 2026-06-01.)"
        )
        return []
    source_text = profile_text or ""
    source_tag = "kaggle"
    try:
        extraction = _extract_kaggle_with_llm(source_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "fact_extractor[kaggle]: extraction failed (%s); returning no facts",
            type(exc).__name__,
        )
        return []

    facts: list[FactRecord] = []
    seen_keys: set[tuple] = set()
    surface = "kaggle"

    def _emit(raw, entity_id, entity_display, *, is_forked: bool):
        reliability = _kaggle_reliability_for_fact(
            getattr(raw, "type", ""), is_forked=is_forked,
        )
        fact = _build_fact_from_raw(
            raw,
            source_text=source_text, source_tag=source_tag,
            entity_id=entity_id, entity_display=entity_display,
            reliability=reliability, surface=surface,
        )
        if fact is None:
            return
        key = (fact.type, _normalize_for_substring(fact.claim), entity_id)
        if key in seen_keys:
            return
        seen_keys.add(key)
        facts.append(fact)

    # Competitions: verify name against source, then emit each fact.
    for comp in (getattr(extraction, "competitions", None) or []):
        name = (getattr(comp, "name", "") or "").strip()
        if not name or not _evidence_in_source(name, source_text):
            logger.warning(
                "fact_extractor[kaggle]: dropped invented competition: name=%r",
                name,
            )
            continue
        entity_id = f"kaggle:competition|{_normalize_entity_token(name)}"
        for raw in (getattr(comp, "facts", None) or []):
            _emit(raw, entity_id, name, is_forked=False)

    # Notebooks: verify title against source. Each notebook carries
    # its own is_forked flag, which drives reliability for its facts.
    for nb in (getattr(extraction, "notebooks", None) or []):
        title = (getattr(nb, "title", "") or "").strip()
        if not title or not _evidence_in_source(title, source_text):
            logger.warning(
                "fact_extractor[kaggle]: dropped invented notebook: title=%r",
                title,
            )
            continue
        is_forked = bool(getattr(nb, "is_forked", True))
        entity_id = f"kaggle:notebook|{_normalize_entity_token(title)}"
        for raw in (getattr(nb, "facts", None) or []):
            _emit(raw, entity_id, title, is_forked=is_forked)

    # Profile-level facts: no entity binding. Credentials here (tier
    # like "Grandmaster") are still PLATFORM_VERIFIED — those are
    # Kaggle-assigned.
    for raw in (getattr(extraction, "profile_facts", None) or []):
        _emit(raw, entity_id="", entity_display="", is_forked=False)

    logger.info(
        "fact_extractor[kaggle]: extracted %d fact(s) from profile %r",
        len(facts), profile_url,
    )
    return facts


# ===========================================================================
# scholar — citation counts platform-verified; publication facts require
# authorship-position context in evidence_quote (or hedged=True).
#
# STARVED until ``profiles/services/scholar_aggregator.py`` captures
# per-publication author lists. The current ``ScholarPublication``
# TypedDict stores only ``{title, venue, year, citations}`` — no
# authors anywhere. Without the authorship line, the position-aware
# hedge policy ( ``_scholar_authorship_is_lead``) cannot function: it
# would either drop every paper (authorship_line substring-check
# fails against absent text) or indiscriminately hedge every paper.
# When the caller passes a current-shape blob the extractor logs a
# starved warning and returns ``[]``. See the trace dated 2026-06-01.
# ===========================================================================


def _scholar_metadata_is_starved(metadata) -> bool:
    """True when ``metadata`` carries the current scholar_aggregator
    digest shape (publications WITHOUT per-paper author lists).

    Detection key: every ``top_publications[i]`` has a ``title`` /
    ``venue`` / ``year`` / ``citations`` but no ``authors`` or
    ``author_list`` field. That's exactly the current
    ``ScholarPublication`` TypedDict (file:line documented in the
    trace).

    When ``metadata`` is None or has no publications, returns False —
    nothing to flag yet, the extractor proceeds (which will then run
    its own LLM-on-text flow against ``profile_text``). The starved
    bail fires only when a real publication list is present and
    every entry is author-less.
    """
    if not isinstance(metadata, dict):
        return False
    pubs = metadata.get('top_publications')
    if not isinstance(pubs, list) or not pubs:
        return False
    return all(
        isinstance(p, dict)
        and 'authors' not in p and 'author_list' not in p
        for p in pubs
    )


class _ScholarPaper(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    authorship_line: str = Field(
        default="",
        description=(
            "The verbatim author list line from the profile, e.g. "
            "'A. Smith, B. Jones, et al.' The CODE checks the user's "
            "position in this line and sets hedged=true on the paper's "
            "facts when the position is non-first or unknown."
        ),
    )
    facts: list[_ExtractedFactRaw] = Field(default_factory=list)


class _ScholarExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    papers: list[_ScholarPaper] = Field(default_factory=list)
    profile_metrics: list[_ExtractedFactRaw] = Field(
        default_factory=list,
        description=(
            "Profile-level metrics — total citation count, h-index, "
            "i10-index. These are PLATFORM_VERIFIED."
        ),
    )


_SCHOLAR_EXTRACT_PROMPT = """You are extracting ATOMIC FACTS from a Google Scholar profile.

Rules:

1. Each fact's `evidence_quote` is a VERBATIM substring of the source text. The code verifies this and DROPS any non-matching fact.

2. STRUCTURED output:
   - "papers[]": one entry per publication. `title` is the paper title (verbatim). `authorship_line` is the verbatim author list line — REQUIRED, because authorship POSITION matters for resume use. Include it even when the user's position is unclear. `facts[]` are facts about this paper (citation count, venue, year).
   - "profile_metrics[]": total citations, h-index, i10-index. Each is type="metric" with value+unit (e.g. unit="citations", "h-index", "i10").

3. Fact types:
   - A paper itself: type="credential" (an external recognition).
   - Citation count for a paper: type="metric" with value+unit="citations".
   - Profile-level totals: type="metric".
   - Awards / fellowships: type="credential".

4. NEVER invent a paper or a citation count not in the source.

SCHOLAR PROFILE TEXT:
{profile_text}
"""


def _extract_scholar_with_llm(profile_text: str) -> _ScholarExtraction:
    prompt = _SCHOLAR_EXTRACT_PROMPT.format(profile_text=(profile_text or "")[:8000])
    llm = get_structured_llm(
        _ScholarExtraction, temperature=0.1, max_tokens=2048, task="fact_extractor",
    )
    return llm.invoke(prompt)


def _scholar_authorship_is_lead(authorship_line: str, profile_owner: str) -> bool:
    """Best-effort check whether the user is the FIRST author on the
    paper. Returns True only when we can identify the user as the
    leading name in the authorship line; False (defensive) otherwise.

    Cases:
      - No profile_owner provided → False (can't tell → safer)
      - profile_owner present and they're the first surname → True
      - profile_owner anywhere else, OR "et al." used → False
    """
    line = _normalize_for_substring(authorship_line)
    owner = _normalize_for_substring(profile_owner)
    if not line or not owner:
        return False
    # Use the user's last token as a surname-ish key (initials are
    # noisy; one-word last name is the most stable).
    owner_token = owner.split()[-1] if owner.split() else owner
    if not owner_token:
        return False
    # Split the line on common separators.
    parts = re.split(r"[,;]\s*|\s+and\s+", line)
    if not parts:
        return False
    first = parts[0].strip()
    return owner_token in first


def extract_from_scholar(
    *, profile_url: str, profile_text: str,
    profile_owner: str = "", metadata: Optional[dict] = None,
) -> list[FactRecord]:
    """Extract atomic facts from a Google Scholar profile.

    Reliability rule: ``PLATFORM_VERIFIED`` — citation counts, h-index,
    publication titles are externally aggregated by Scholar.

    Authorship policy (the load-bearing nuance):
      - Every publication fact's ``evidence_quote`` must include the
        authorship context. The prompt requires an
        ``authorship_line`` per paper; the code checks it is
        substring-present in the source.
      - When the user is NOT identifiable as the first author (or
        when ``profile_owner`` is empty so position is unknown), the
        paper's facts get ``hedged=True``. The v2 generator must
        treat hedged credentials cautiously — e.g. never present a
        4th-author paper as "led".

    Entity binding:
      - Papers → ``scholar:paper|<normalized_title>``
      - Profile-level metrics → empty entity_id (FactRecord rejects
        metrics without entity_id, so profile_metrics MUST be bound).
        The code wires them to a synthetic profile entity_id.

    STARVED-BLOB GUARD: when ``metadata`` is the current
    ``scholar_aggregator`` digest shape (publications without
    ``authors`` / ``author_list``), the position-aware hedge policy
    cannot function. We log a clear "extend the aggregator" warning
    and return ``[]`` rather than silently produce universally-hedged
    facts (which would look like a broken extractor).
    """
    if _scholar_metadata_is_starved(metadata):
        logger.warning(
            "fact_extractor[scholar]: STARVED — scholar_signals "
            "top_publications entries have no `authors`/`author_list` "
            "field. The position-aware hedge policy cannot function "
            "without authorship lines. Extend "
            "profiles/services/scholar_aggregator.py to capture the "
            "author list per publication before this extractor can "
            "produce facts. Returning []. (See trace 2026-06-01.)"
        )
        return []
    source_text = profile_text or ""
    source_tag = "scholar"
    try:
        extraction = _extract_scholar_with_llm(source_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "fact_extractor[scholar]: extraction failed (%s); returning no facts",
            type(exc).__name__,
        )
        return []

    facts: list[FactRecord] = []
    seen_keys: set[tuple] = set()
    surface = "scholar"
    reliability = SourceReliability.PLATFORM_VERIFIED

    def _emit(raw, entity_id, entity_display, *, force_hedged: bool = False):
        fact = _build_fact_from_raw(
            raw,
            source_text=source_text, source_tag=source_tag,
            entity_id=entity_id, entity_display=entity_display,
            reliability=reliability, surface=surface,
        )
        if fact is None:
            return
        if force_hedged and not fact.hedged:
            # Re-create with hedged=True (FactRecord is immutable-ish;
            # safer to construct a new one than mutate).
            try:
                fact = fact.model_copy(update={"hedged": True})
            except Exception:  # noqa: BLE001
                pass
        key = (fact.type, _normalize_for_substring(fact.claim), entity_id)
        if key in seen_keys:
            return
        seen_keys.add(key)
        facts.append(fact)

    for paper in (getattr(extraction, "papers", None) or []):
        title = (getattr(paper, "title", "") or "").strip()
        if not title or not _evidence_in_source(title, source_text):
            logger.warning(
                "fact_extractor[scholar]: dropped invented paper: title=%r", title,
            )
            continue
        authorship_line = (getattr(paper, "authorship_line", "") or "").strip()
        if authorship_line and not _evidence_in_source(authorship_line, source_text):
            logger.warning(
                "fact_extractor[scholar]: dropped paper with fabricated "
                "authorship_line: title=%r line=%r",
                title, authorship_line[:120],
            )
            continue
        is_lead = _scholar_authorship_is_lead(authorship_line, profile_owner)
        # Hedge when authorship is unknown / non-lead. The v2 generator
        # must NOT present a hedged publication as "led / drove".
        force_hedged = (not is_lead)
        entity_id = f"scholar:paper|{_normalize_entity_token(title)}"
        for raw in (getattr(paper, "facts", None) or []):
            _emit(raw, entity_id, title, force_hedged=force_hedged)

    # Profile-level metrics (total citations, h-index, i10) — bound
    # to a synthetic profile entity so FactRecord's metric-requires-
    # entity rule is satisfied.
    profile_entity = f"scholar:profile|{_normalize_entity_token(profile_url or 'unknown')}"
    for raw in (getattr(extraction, "profile_metrics", None) or []):
        _emit(raw, profile_entity, "Scholar profile (totals)")

    logger.info(
        "fact_extractor[scholar]: extracted %d fact(s) from profile %r",
        len(facts), profile_url,
    )
    return facts


# ===========================================================================
# linkedin — self-stated profile snapshot.
# ===========================================================================


class _LinkedInExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facts: list[_ExtractedFactRaw] = Field(default_factory=list)


_LINKEDIN_EXTRACT_PROMPT = """You are extracting ATOMIC FACTS from a LinkedIn profile snapshot.

Rules:

1. Each fact's `evidence_quote` is a VERBATIM substring of the snapshot. The code verifies this and DROPS any non-matching fact.

2. Fact types: skill, achievement, role, education, credential. Metric facts on LinkedIn are rare; emit them only if a clear numeric value with a unit is present in the snapshot.

3. NEVER invent skills, roles, companies, or institutions not in the snapshot.

LINKEDIN SNAPSHOT:
{snapshot}
"""


def _extract_linkedin_with_llm(snapshot: str) -> _LinkedInExtraction:
    prompt = _LINKEDIN_EXTRACT_PROMPT.format(snapshot=(snapshot or "")[:8000])
    llm = get_structured_llm(
        _LinkedInExtraction, temperature=0.1, max_tokens=2048, task="fact_extractor",
    )
    return llm.invoke(prompt)


def extract_from_linkedin(
    *, profile_url: str, profile_text: str, metadata: Optional[dict] = None,
) -> list[FactRecord]:
    """Extract atomic facts from a LinkedIn profile snapshot.

    Reliability rule: ``USER_ORIGINAL`` — LinkedIn content is
    self-stated. Skills, headlines, experience descriptions, and
    certifications are typed by the user.

    Entity binding: this extractor currently emits free-floating
    facts (no role-grouping). Metric facts will be rejected by
    FactRecord (no entity_id), which is the desired behavior — a
    floating metric on LinkedIn is ambiguous.

    FUTURE TASK (not implemented here, deliberately flagged):
      Cross-check LinkedIn claims against the uploaded CV for
      consistency — "Senior Engineer" on LinkedIn while the CV says
      "Intern" should surface as a regression-style finding so the
      user sees the conflict before sending. This belongs in the
      v2 planner or a dedicated consistency-checker; the extractor
      stays focused on per-source extraction.
    """
    source_text = profile_text or ""
    source_tag = "linkedin"
    try:
        extraction = _extract_linkedin_with_llm(source_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "fact_extractor[linkedin]: extraction failed (%s); returning no facts",
            type(exc).__name__,
        )
        return []

    facts: list[FactRecord] = []
    seen_keys: set[tuple] = set()
    surface = "linkedin"

    for raw in (getattr(extraction, "facts", None) or []):
        fact = _build_fact_from_raw(
            raw,
            source_text=source_text, source_tag=source_tag,
            entity_id="", entity_display="",
            reliability=SourceReliability.USER_ORIGINAL,
            surface=surface,
        )
        if fact is None:
            continue
        key = (fact.type, _normalize_for_substring(fact.claim), fact.entity_id)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        facts.append(fact)

    logger.info(
        "fact_extractor[linkedin]: extracted %d fact(s) from profile %r",
        len(facts), profile_url,
    )
    return facts


# ---------------------------------------------------------------------------
# Registry / dispatch.
# ---------------------------------------------------------------------------


EXTRACTORS = {
    "github_readme": extract_from_github_readme,
    "old_cv": extract_from_old_cv,
    "kaggle": extract_from_kaggle,
    "scholar": extract_from_scholar,
    "linkedin": extract_from_linkedin,
}


def extract_facts(source_type: str, **kwargs) -> list[FactRecord]:
    """Dispatch to a registered extractor by source_type.

    Raises:
      ValueError when source_type is not registered.
      NotImplementedError when the extractor for that source is still
        a stub (kaggle/scholar/linkedin/old_cv at this revision).
    """
    extractor = EXTRACTORS.get(source_type)
    if extractor is None:
        raise ValueError(
            f"Unknown source_type: {source_type!r}. "
            f"Registered: {sorted(EXTRACTORS.keys())}"
        )
    return extractor(**kwargs)


def extract_into_store(
    store: FactStore, source_type: str, **kwargs,
) -> list[str]:
    """Extract from one source and merge into the FactStore in one call.

    Returns the surviving fact ids (post-dedup). The store's
    ``(type, normalized_claim, entity_id)`` dedup applies; the
    extractor's own local dedup runs first to avoid even constructing
    duplicates.
    """
    facts = extract_facts(source_type=source_type, **kwargs)
    return store.add_many(facts)
