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


_CLASSIFY_PROMPT = """You are classifying a GitHub repository as ORIGINAL WORK or TUTORIAL/COURSE-FOLLOWING / DERIVED.

You are STRICT and SKEPTICAL by default. The cost of mislabeling a course repo as "original" (overclaim on a resume → reader catches the candidate inflating) far exceeds the cost of underselling a real original project. When in doubt, classify as "unsure" → the system maps that to tutorial-derived.

STRONG TUTORIAL / DERIVED signals (any one of these is decisive — classify TUTORIAL):
- "following along with …", "based on the course / book", "from the tutorial", "walkthrough", "guided project", "as taught in", "from the bootcamp".
- A named course or platform: DataCamp, Coursera, Udemy, Udacity, Kaggle Learn, fast.ai, freeCodeCamp, edX, Pluralsight, LinkedIn Learning, "Andrew Ng", "Stanford CS229", "CS231n", "Hands-On Machine Learning" (book), "Deep Learning Specialization", "ng-mlspecialization", any "Specialization" name.
- Repo name patterns that signal a course: "datacamp-…", "coursera-…", "udemy-…", "andrew-ng-…", "ng-…specialization", "<book-name>-exercises", "<course-code>-…" (e.g. CS231n).
- Re-implementation of a published paper's method without explicit novel modification, OR notebook(s) that read as step-by-step "do exactly these cells" rather than an open investigation.
- Repo metadata `is_fork: true` (this repo is a fork of someone else's — the work isn't the candidate's original output).
- A "Case Study" or "Project N" framing common to course curricula, especially when paired with a public-dataset name (Titanic / Iris / IBM HR / Mall Customers / Boston Housing).

WEAK TUTORIAL signals (combine with at least one other signal before classifying TUTORIAL — not decisive alone):
- The project is purely "applied X technique to <famous public dataset>" with no original problem framing.
- The README is a step-by-step "how I did this" rather than "what I built and why".

ORIGINAL signals (positive evidence of the candidate's own work):
- A custom architecture, a novel dataset the candidate collected/scraped, or a problem framed by the candidate (not a textbook problem).
- "I built / designed / shipped …", clear ownership of decisions and tradeoffs.
- Production deployment, real users, an integration with another service, a non-trivial scale signal.
- README explains DESIGN choices (why this stack, why this model) — not just steps.
- A test suite the candidate authored, a CI pipeline, an architecture diagram of the candidate's own design.

FAIL-SAFE (load-bearing — read this twice):
- Strong tutorial signal present  → "tutorial"
- Clear original signals AND no tutorial signals  → "original"
- Mixed, weak, or unclear signals → "unsure" (the system maps "unsure" → tutorial-derived). NEVER guess "original" when you are not confident; the safer default is "unsure". A polished README alone is NOT an original signal — many course repos have polished READMEs.

REPO METADATA:
{metadata_block}

README:
{readme_text}

Return:
{{
  "classification": "original" | "tutorial" | "unsure",
  "reasoning": "<one sentence — name the specific signal>"
}}
"""


def _coerce_fork_flag(metadata: dict):
    """Return True if metadata clearly says this repo is a fork.
    Accepts ``is_fork``, ``fork`` (the native GitHub API field name),
    or ``fork_of`` (a non-empty source-repo pointer). Returns None when
    the field is absent so the caller can fall back to prose signals."""
    if not isinstance(metadata, dict):
        return None
    for k in ("is_fork", "fork"):
        v = metadata.get(k)
        if isinstance(v, bool):
            return v
    fork_of = metadata.get("fork_of")
    if fork_of:  # non-empty string / dict → derived from another repo
        return True
    return None


def _classify_repo_with_llm(
    readme_text: str, metadata: dict
) -> _RepoClassification:
    """Classification entry point.

    Two-stage:
      1. **Deterministic short-circuit** on fork status. If
         ``metadata['is_fork']`` (or ``fork``, or a non-empty
         ``fork_of``) is True, return ``tutorial`` immediately — a
         fork of someone else's repo cannot be the candidate's
         original work, regardless of the README's polish. No LLM
         call needed (saves a Groq round-trip).
      2. **LLM judgment** on README prose + repo metadata for
         everything else, with the strengthened prompt above. The
         prompt is biased toward "unsure" when signals are mixed;
         "unsure" maps to tutorial-derived downstream
         (``_reliability_from_classification``).

    Fork-flag coverage: ``github_aggregator.RepoSnapshot`` does NOT
    currently capture the per-repo ``fork: bool`` field from the
    GitHub REST API. The aggregator's source at
    ``profiles/services/github_aggregator.py:188`` has a
    commented-out filter ``# repos = [r for r in repos if not r.get('fork')]``
    that proves the API exposes the boolean — extending RepoSnapshot
    to store it is a one-line aggregator change that would plug into
    the short-circuit above immediately. Until then this function
    falls back to prose-only classification when metadata is silent
    about fork status."""
    if _coerce_fork_flag(metadata) is True:
        logger.info(
            "fact_extractor: classifier short-circuited on is_fork=True "
            "→ tutorial_derived (no LLM call)"
        )
        return _RepoClassification(
            classification="tutorial",
            reasoning="metadata.is_fork=True (forked repo — not candidate's original work)",
        )
    metadata_lines = []
    fork_seen = False
    for k in ("repo_url", "name", "language", "stars", "forks",
              "fork_of", "is_fork", "fork", "description"):
        v = metadata.get(k)
        if v is None or v == "":
            continue
        if k in ("is_fork", "fork"):
            fork_seen = True
        metadata_lines.append(f"- {k}: {v}")
    if not fork_seen:
        # One DEBUG-level note per call when the aggregator hasn't
        # captured fork status. Quiet (DEBUG) so production logs aren't
        # spammed; visible if someone goes looking.
        logger.debug(
            "fact_extractor: classifier metadata lacks `is_fork` field "
            "(github_aggregator.RepoSnapshot doesn't store it yet); "
            "relying on prose signals only."
        )
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

1. EVIDENCE_QUOTE COMPLIANCE (load-bearing — read this twice):
   - `evidence_quote` MUST be copied VERBATIM as a CONTIGUOUS SPAN from the README text below. Same characters, same word order, same punctuation. NO paraphrase. NO reordering. NO summarizing. NO rephrasing into a sentence the README does not contain.
   - Quote the MINIMAL exact span that contains the metric or claim. Shorter quotes that exactly match the source are preferable to longer ones you have to "fix up".
   - If you cannot find a metric stated verbatim in the README, DO NOT emit that metric fact. A number that is not verbatim-groundable MUST be dropped — that is the correct behavior, not a failure.
   - Your output is programmatically verified by a substring check (case-insensitive, whitespace-tolerant only). Paraphrases FAIL this check and the fact is DROPPED. Be exact.

   EXAMPLES — given README text: ``silhouette peaks at k=3 (0.351)``
     ✓ CORRECT: evidence_quote="silhouette peaks at k=3 (0.351)"
     ✓ CORRECT: evidence_quote="k=3 (0.351)"
     ✓ CORRECT: evidence_quote="0.351"
     ✗ WRONG (paraphrased):  evidence_quote="silhouette score of 0.351 for k=3"
     ✗ WRONG (reordered):    evidence_quote="k=3 silhouette 0.351"
     ✗ WRONG (synthesized):  evidence_quote="The silhouette score for k=3 is 0.351."

   Same rule for percentages, currencies, counts: copy the substring that actually appears.

2. Extract only what is STATED in the README. Do not infer. Do not paraphrase. Do not synthesize a metric from non-metric text.

3. Fact types you may emit (LIMITED — the structured profile layer already covers skills and project skeletons; do NOT emit "skill" or "project" facts here):
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
        # NARROWING (2026-06-01): the README extractor is restricted to
        # METRIC + ACHIEVEMENT. SKILL facts come from Layer B's
        # ``skills[]`` (deduped across all sources) and project
        # skeletons come from Layer B's ``projects_enriched[]`` (where
        # ``source_url`` matches this extractor's ``repo_url``, so
        # metrics join structurally onto the same entity). Emitting
        # skill/project from the README too would re-introduce the
        # double-entity bug the smoke test exposed.
        parsed_type = _parse_fact_type(getattr(raw, "type", ""))
        if parsed_type in _README_NARROWED_OUT_TYPES:
            logger.info(
                "fact_extractor[github_readme]: narrowed-out fact "
                "type=%s claim=%r (Layer B handles skills/project skeleton)",
                parsed_type.value if parsed_type else "?",
                (getattr(raw, "claim", "") or "")[:60],
            )
            continue
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


# Narrowing set for the README extractor: SKILL + PROJECT come from
# the structured-profile reader (Layer B), not from README prose.
_README_NARROWED_OUT_TYPES: set = {FactType.SKILL, FactType.PROJECT}


# ---------------------------------------------------------------------------
# Profile-README rebinding — when a fact extracted from {user}/{user}
# unambiguously references a specific repo (e.g. a badge URL containing
# the repo name), rebind the fact to THAT repo's entity_id so the v2
# generator's metrics_for(<repo>) sees it. Unambiguous-match only:
# never guess.
# ---------------------------------------------------------------------------


_REPO_NAME_BOUNDARY = re.compile(r"[A-Za-z0-9_.-]+")


def _unambiguous_repo_match(text: str, known_repos: list[dict]) -> Optional[dict]:
    """Return the SINGLE known repo whose name or URL appears in
    ``text``. Returns None when:
      - no known repo matches, OR
      - more than one known repo matches (ambiguous — do not guess).

    Match rules (in priority order, but used together to decide
    "matches" vs "no match"):
      - URL substring: any known repo's ``url`` appearing in ``text``
        is a strong signal.
      - Repo-name word-boundary: the repo's ``name`` (e.g. "SmartCV")
        appearing as a standalone token in ``text``. The character
        class ``[A-Za-z0-9_.-]+`` is used as the boundary so a
        sub-string of a longer identifier doesn't count.

    Returns the matched repo dict or None. The caller is responsible
    for actually performing the rebind."""
    if not text or not known_repos:
        return None
    haystack = text.lower()
    matched: list[dict] = []
    for repo in known_repos:
        if not isinstance(repo, dict):
            continue
        url = (repo.get("url") or "").lower().strip()
        name = (repo.get("name") or "").strip()
        if not name:
            continue
        if url and url in haystack:
            matched.append(repo)
            continue
        # Word-boundary match on the repo name. Using a custom boundary
        # so e.g. "SmartCV" inside "SmartCV-clone" still counts as a
        # match (good for "/SmartCV#section"-style URLs), but "SmartCV"
        # inside "MySmartCVDemo" does NOT (would be a false positive).
        pattern = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        if pattern.search(text):
            matched.append(repo)
    # De-dupe (a repo can match both via URL and via name).
    seen_urls = set()
    unique: list[dict] = []
    for r in matched:
        u = (r.get("url") or r.get("name") or "").lower()
        if u and u not in seen_urls:
            seen_urls.add(u)
            unique.append(r)
    if len(unique) != 1:
        return None
    return unique[0]


def _rebind_fact_to_repo(fact: FactRecord, target_repo: dict) -> FactRecord:
    """Return a new FactRecord identical to ``fact`` except for
    ``entity_id`` / ``entity_display`` swapped to ``target_repo``.
    Skill facts keep ``entity_id=""`` (the cross-source-dedup policy
    — never bound to a single entity)."""
    if fact.type == FactType.SKILL:
        return fact   # policy: skills aren't entity-bound
    target_url = (target_repo.get("url") or "").strip()
    target_display = (
        target_repo.get("display_name")
        or target_repo.get("name")
        or target_url
    )
    if not target_url:
        return fact
    return fact.model_copy(update={
        "entity_id": target_url,
        "entity_display": target_display,
    })


def rebind_profile_readme_facts(
    facts: list[FactRecord], known_repos: list[dict],
) -> list[FactRecord]:
    """Rebind facts extracted from a {user}/{user} profile README to
    the specific repo they unambiguously reference.

    Many profile READMEs contain badges that link to a specific repo
    (e.g. ``[![Tests](.../tests-337%20passing)](.../SmartCV#tests)``).
    Without rebinding, those facts sit on the profile-README entity
    and a downstream ``store.metrics_for("<SmartCV URL>")`` query
    won't see them — they're stranded.

    For each fact:
      - Inspect ``evidence_quote`` + ``claim`` for a known-repo
        reference (URL substring or repo-name word-boundary).
      - If EXACTLY ONE known repo matches → rebind ``entity_id`` to
        that repo's URL.
      - If ZERO or MULTIPLE repos match → leave the fact on its
        original entity. The unambiguous-match guard is the same
        safety principle as the role-identity guard in v1: never
        guess onto an entity.

    SKILL facts are exempt — they're already entity-less per the
    cross-source-dedup policy.

    Arguments:
      facts: the list returned by ``extract_from_github_readme`` for
        the profile README.
      known_repos: list of ``{url, name, display_name?}`` dicts —
        the user's own top_repos.

    Returns: a NEW list of FactRecords (rebound where applicable);
    the input list is not mutated."""
    if not known_repos:
        return list(facts)
    out: list[FactRecord] = []
    rebound_count = 0
    for fact in facts:
        if fact.type == FactType.SKILL:
            out.append(fact)
            continue
        text = (fact.evidence_quote or "") + " " + (fact.claim or "")
        target = _unambiguous_repo_match(text, known_repos)
        if target is None:
            out.append(fact)
            continue
        rebound = _rebind_fact_to_repo(fact, target)
        if rebound.entity_id != fact.entity_id:
            rebound_count += 1
            logger.info(
                "fact_extractor[github_readme]: rebound profile-README "
                "fact to repo %r (claim=%r)",
                target.get("name") or target.get("url"),
                fact.claim[:80],
            )
        out.append(rebound)
    if rebound_count:
        logger.info(
            "fact_extractor[github_readme]: rebound %d profile-README "
            "fact(s) to specific repos via unambiguous match.",
            rebound_count,
        )
    return out


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
    """[DEPRECATED 2026-06-01] Use ``extract_from_structured_profile()``.

    The structured profile (``data_content['experiences']`` /
    ``['education']`` / ``['skills']``) is the canonical CV-derived
    metadata — written by the CV parser + LLM validator on upload
    (see ``profiles/services/llm_validator.py``). Re-extracting from
    ``cv_text`` here was wasteful LLM work that produced facts the
    structured reader already covers.

    Numeric evidence in CV bullets now flows through the structured
    reader's ACHIEVEMENT facts (whose ``claim`` text carries the same
    numbers, pooled by the v2 generator's number-grounding guard).

    Signature kept for back-compat; the dispatch registry still routes
    ``source_type='old_cv'`` here. Returns ``[]`` and logs a single
    deprecation note. The internal ``_extract_cv_with_llm`` /
    ``_CVExtraction`` helpers are preserved for tests that exercise
    the historic prompt shape."""
    logger.info(
        "fact_extractor[old_cv]: deprecated path — returning [] "
        "(use extract_from_structured_profile() to read Layer B "
        "metadata). cv_text len=%d profile_owner=%r",
        len(cv_text or ""), profile_owner,
    )
    return []


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
    """[DEPRECATED 2026-06-01] Use ``extract_from_structured_profile()``.

    LinkedIn metadata (skills, experience entries, certifications) is
    folded into ``data_content`` by
    ``profiles/services/signal_merger.py`` and read by the structured
    reader. Re-extracting from ``linkedin_signals`` prose with an LLM
    was redundant — the structured reader covers it without an LLM
    call.

    Signature kept for back-compat; the dispatch registry still routes
    ``source_type='linkedin'`` here. Returns ``[]`` and logs a single
    deprecation note. The internal ``_extract_linkedin_with_llm`` /
    ``_LinkedInExtraction`` helpers are preserved for tests that
    exercise the historic prompt shape."""
    logger.info(
        "fact_extractor[linkedin]: deprecated path — returning [] "
        "(use extract_from_structured_profile() to read Layer B "
        "metadata). profile_url=%r snapshot len=%d",
        profile_url, len(profile_text or ""),
    )
    return []


# ===========================================================================
# Structured-profile reader (Layer B) — the NEW primary metadata input.
#
# The structured profile is what ``profiles/services/llm_validator.py``
# writes to ``UserProfile.data_content`` after CV parsing, augmented by
# ``profiles/services/signal_merger.py`` (LinkedIn / GitHub additions)
# and ``profiles/services/profile_rebuilder.py`` (projects_enriched).
# v1's resume generator (``resumes/services/resume_generator.py:1249``)
# consumes this layer exclusively.
#
# This reader is the v2 metadata path: it pulls roles/education/
# credentials/skills/projects directly from the parsed arrays — no LLM
# call, no evidence-quote substring guard (for parsed metadata the
# entity IS the fact; there is no prose to substring-match). The
# Layer-A README extractor above stays narrowed to METRIC + ACHIEVEMENT,
# producing the evidence-grounded numeric facts that THIS reader does
# not. Together they cover the input surface that the old multi-source
# LLM extraction stack used to.
# ===========================================================================


def extract_from_structured_profile(*, data_content: dict) -> list[FactRecord]:
    """Emit FactRecords directly from Layer B (the structured profile).

    Layer B is the canonical normalized profile that v1's
    ``resume_generator`` consumes exclusively. Its structured arrays
    (experiences/education/certifications/skills/projects_enriched)
    are already deduped across CV + LinkedIn + GitHub by the
    signal_merger. This reader emits one FactRecord per parsed entity
    so the v2 planner / generator can read from the same canonical
    layer that v1 reads.

    Calls NO LLM. Runs NO evidence-quote substring guard — for parsed
    metadata the entity is the fact, and Layer B's bullet text is
    LLM-RESTRUCTURED (per ``llm_validator.VALIDATION_SYSTEM_PROMPT``),
    NOT verbatim source text, so substring-grounding against it would
    be misleading. To keep that distinction visible in the store,
    structurally-sourced facts carry ``source='structured_profile'``
    (or ``'structured_profile:<sub>'`` for projects).

    Reliability: every structured entry maps to ``USER_ORIGINAL``.
    Layer B's per-entry ``source`` field (``'cv'`` / ``'linkedin'`` /
    ``'github'`` / manual edits) is self-stated or parser-derived in
    every current case; a future task can downgrade specific sources
    if needed.

    Coverage:
      - ``experiences[]``            -> ROLE + ACHIEVEMENT per bullet
      - ``education[]``              -> EDUCATION
      - ``certifications[]``         -> CREDENTIAL
      - ``skills[]``                 -> SKILL (entity-less per
        cross-source dedup policy)
      - ``projects_enriched[]`` (or
        ``projects[]`` fallback)     -> PROJECT + ACHIEVEMENT per
        bullet. ``entity_id`` is the project's URL (``source_url``
        from ``EnrichedProject``) so README metrics from
        ``extract_from_github_readme`` JOIN onto the same entity.

    Metrics are NOT emitted from this reader — they live in Layer A's
    verbatim source text (README excerpts, raw_text) and require the
    substring-grounding guard the narrowed README extractor provides.
    """
    if not isinstance(data_content, dict):
        return []
    facts: list[FactRecord] = []
    seen_ids: set[str] = set()

    def _emit(fr: FactRecord) -> None:
        if fr.id in seen_ids:
            return
        seen_ids.add(fr.id)
        facts.append(fr)

    # --- experiences[] -> ROLE + ACHIEVEMENT --------------------------------
    for exp in (data_content.get("experiences") or []):
        if not isinstance(exp, dict):
            continue
        title = (exp.get("title") or "").strip()
        company = (exp.get("company") or "").strip()
        if not (title or company):
            continue
        entity_id = "cv:role|{c}|{t}".format(
            c=_normalize_entity_token(company),
            t=_normalize_entity_token(title),
        )
        entity_display = f"{title} @ {company}".strip(" @")
        start = (exp.get("start_date") or "").strip()
        end = (exp.get("end_date") or "").strip()
        date_range = ""
        if start and end:
            date_range = f"{start} - {end}"
        elif end:
            date_range = end
        elif start:
            date_range = start
        role_evidence = entity_display + (f" — {date_range}" if date_range else "")
        role_claim = title + (f" at {company}" if company else "")
        try:
            _emit(FactRecord(
                id=_gen_fact_id(
                    "structured_profile", entity_id,
                    FactType.ROLE.value, role_claim,
                ),
                type=FactType.ROLE,
                claim=role_claim,
                entity_id=entity_id,
                entity_display=entity_display,
                source="structured_profile",
                source_reliability=SourceReliability.USER_ORIGINAL,
                evidence_quote=role_evidence,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fact_extractor[structured_profile]: ROLE rejected (%s) "
                "for entity=%r", type(exc).__name__, entity_display,
            )
            continue
        # Defensive read-side guard (mirrors v1's
        # resume_generator.py:2908 pattern): if a future writer drifts
        # back to emitting description as a string, this guard ensures
        # we split on newlines into a sensible list rather than
        # char-iterating the string. The signal_merger's
        # _coerce_description_to_bullets is the load-bearing fix at
        # the cause; this is defense-in-depth.
        raw_desc = exp.get("description") or []
        if isinstance(raw_desc, str):
            raw_desc = [
                line.strip()
                for line in raw_desc.split("\n")
                if line.strip()
            ]
        for bullet in raw_desc:
            if not isinstance(bullet, str):
                continue
            bullet = bullet.strip()
            if not bullet:
                continue
            try:
                _emit(FactRecord(
                    id=_gen_fact_id(
                        "structured_profile", entity_id,
                        FactType.ACHIEVEMENT.value, bullet,
                    ),
                    type=FactType.ACHIEVEMENT,
                    claim=bullet,
                    entity_id=entity_id,
                    entity_display=entity_display,
                    source="structured_profile",
                    source_reliability=SourceReliability.USER_ORIGINAL,
                    evidence_quote=bullet,
                ))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fact_extractor[structured_profile]: ACHIEVEMENT "
                    "rejected (%s) bullet=%r",
                    type(exc).__name__, bullet[:80],
                )

    # --- education[] -> EDUCATION ------------------------------------------
    for edu in (data_content.get("education") or []):
        if not isinstance(edu, dict):
            continue
        institution = (edu.get("institution") or "").strip()
        degree = (edu.get("degree") or "").strip()
        if not (institution or degree):
            continue
        entity_id = "cv:edu|{i}|{d}".format(
            i=_normalize_entity_token(institution),
            d=_normalize_entity_token(degree),
        )
        field = (edu.get("field") or "").strip()
        year = (
            edu.get("graduation_year") or edu.get("year") or ""
        )
        if not isinstance(year, str):
            year = str(year)
        year = year.strip()
        gpa = (edu.get("gpa") or "").strip()
        parts: list[str] = []
        if degree:
            parts.append(degree)
        if field:
            parts.append(f"in {field}")
        if institution:
            parts.append(f"at {institution}")
        if year:
            parts.append(f"({year})")
        claim = " ".join(parts) or f"{degree} {institution}".strip()
        evidence = claim + (f"; GPA {gpa}" if gpa else "")
        try:
            _emit(FactRecord(
                id=_gen_fact_id(
                    "structured_profile", entity_id,
                    FactType.EDUCATION.value, claim,
                ),
                type=FactType.EDUCATION,
                claim=claim,
                entity_id=entity_id,
                entity_display=f"{degree} @ {institution}".strip(" @"),
                source="structured_profile",
                source_reliability=SourceReliability.USER_ORIGINAL,
                evidence_quote=evidence,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fact_extractor[structured_profile]: EDUCATION rejected "
                "(%s) claim=%r", type(exc).__name__, claim[:80],
            )

    # --- certifications[] -> CREDENTIAL ------------------------------------
    for cert in (data_content.get("certifications") or []):
        if not isinstance(cert, dict):
            continue
        name = (cert.get("name") or "").strip()
        if not name:
            continue
        issuer = (cert.get("issuer") or "").strip()
        date = (cert.get("date") or "").strip()
        entity_id = "cv:cred|{n}|{i}".format(
            n=_normalize_entity_token(name),
            i=_normalize_entity_token(issuer),
        )
        claim = name + (f" — {issuer}" if issuer else "")
        evidence = claim + (f" ({date})" if date else "")
        try:
            _emit(FactRecord(
                id=_gen_fact_id(
                    "structured_profile", entity_id,
                    FactType.CREDENTIAL.value, claim,
                ),
                type=FactType.CREDENTIAL,
                claim=claim,
                entity_id=entity_id,
                entity_display=name,
                source="structured_profile",
                source_reliability=SourceReliability.USER_ORIGINAL,
                evidence_quote=evidence,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fact_extractor[structured_profile]: CREDENTIAL rejected "
                "(%s) name=%r", type(exc).__name__, name,
            )

    # --- skills[] -> SKILL (entity-less, cross-source-dedup policy) --------
    skills_seen: set[str] = set()
    for s in (data_content.get("skills") or []):
        name = ""
        if isinstance(s, str):
            name = s.strip()
        elif isinstance(s, dict):
            name = (s.get("name") or "").strip()
        if not name:
            continue
        key = _normalize_for_substring(name)
        if key in skills_seen:
            continue
        skills_seen.add(key)
        try:
            _emit(FactRecord(
                id=_gen_fact_id(
                    "structured_profile", "",
                    FactType.SKILL.value, name,
                ),
                type=FactType.SKILL,
                claim=name,
                entity_id="",
                entity_display="",
                source="structured_profile",
                source_reliability=SourceReliability.USER_ORIGINAL,
                evidence_quote=name,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fact_extractor[structured_profile]: SKILL rejected (%s) "
                "name=%r", type(exc).__name__, name,
            )

    # --- projects -> PROJECT + ACHIEVEMENT ---------------------------------
    # Prefer projects_enriched (has source_url, which matches the
    # GitHub README extractor's entity_id, so README metrics join
    # onto the same project entity). Fall back to projects[] for
    # profiles where the enricher hasn't run yet.
    enriched = data_content.get("projects_enriched") or []
    base_projects = data_content.get("projects") or []
    project_rows: list[dict] = []
    if enriched:
        for ep in enriched:
            if not isinstance(ep, dict):
                continue
            project_rows.append({
                "name": (ep.get("name") or "").strip(),
                "url": (ep.get("source_url") or "").strip(),
                "tech": ep.get("tech_stack") or [],
                "summary": (ep.get("summary") or "").strip(),
                "bullets": ep.get("bullets") or [],
                "source": (ep.get("source") or "structured").strip(),
            })
    else:
        for p in base_projects:
            if not isinstance(p, dict):
                continue
            project_rows.append({
                "name": (p.get("name") or "").strip(),
                "url": (p.get("url") or "").strip(),
                "tech": p.get("technologies") or [],
                "summary": "",
                "bullets": p.get("description") or [],
                "source": (p.get("source") or "cv").strip(),
            })

    # Dedup by entity_id so the SAME project listed in both
    # projects_enriched and projects collapses to ONE entity — the
    # structural fix for the double-SmartCV bug the smoke test
    # exposed.
    seen_project_entities: set[str] = set()
    for row in project_rows:
        name = row["name"]
        url = row["url"]
        if not name and not url:
            continue
        entity_id = url or ("project:" + _normalize_entity_token(name))
        if entity_id in seen_project_entities:
            continue
        seen_project_entities.add(entity_id)
        entity_display = name or url
        tech_list = [
            t for t in row["tech"]
            if isinstance(t, str) and t.strip()
        ]
        tech_str = ", ".join(tech_list)
        summary = row["summary"] or name
        claim = (summary or name) + (
            f" (tech: {tech_str})" if tech_str else ""
        )
        evidence = (
            summary + (f" Tech: {tech_str}." if tech_str else "")
        ) or name
        source_tag = "structured_profile:" + (row["source"] or "project")
        try:
            _emit(FactRecord(
                id=_gen_fact_id(
                    source_tag, entity_id, FactType.PROJECT.value, claim,
                ),
                type=FactType.PROJECT,
                claim=claim,
                entity_id=entity_id,
                entity_display=entity_display,
                source=source_tag,
                source_reliability=SourceReliability.USER_ORIGINAL,
                evidence_quote=evidence,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fact_extractor[structured_profile]: PROJECT rejected "
                "(%s) name=%r", type(exc).__name__, name,
            )
            continue
        for bullet in row["bullets"]:
            if not isinstance(bullet, str):
                continue
            bullet = bullet.strip()
            if not bullet:
                continue
            try:
                _emit(FactRecord(
                    id=_gen_fact_id(
                        source_tag, entity_id,
                        FactType.ACHIEVEMENT.value, bullet,
                    ),
                    type=FactType.ACHIEVEMENT,
                    claim=bullet,
                    entity_id=entity_id,
                    entity_display=entity_display,
                    source=source_tag,
                    source_reliability=SourceReliability.USER_ORIGINAL,
                    evidence_quote=bullet,
                ))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fact_extractor[structured_profile]: project "
                    "ACHIEVEMENT rejected (%s) bullet=%r",
                    type(exc).__name__, bullet[:80],
                )

    logger.info(
        "fact_extractor[structured_profile]: emitted %d fact(s) from "
        "Layer B (no LLM, no evidence-quote guard).", len(facts),
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
    "structured_profile": extract_from_structured_profile,
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
