"""RAG knowledge-base retriever — faceted filter + dense semantic top-K.

Reads from `profiles.models.KnowledgeChunk` (populated by the
`build_knowledge_index` management command). At resume-generation time,
`retrieve_chunks(jd_text, classification)` returns a small list of the most
relevant KB documents to inject as a STANDARDS block in the LLM prompt.

Two-bucket retrieval (per §3.4 of the master plan):
  1. UNIVERSAL — `ats_rules` + `banned_patterns` always apply, so we ignore
     the role/seniority facets and pick the top-N by semantic similarity to
     the JD.
  2. ROLE-SPECIFIC — the remaining categories are filtered by the
     classification's role/seniority/region facets before the semantic sort.

Result is the merged set, deduped on `kb_id`. Default split is 3+3 for k=6.
"""
from __future__ import annotations

import logging
from typing import Iterable, List, Optional

from pgvector.django import CosineDistance

from profiles.models import KnowledgeChunk
from profiles.services.embeddings import embed_text
from profiles.services.role_classifier import RoleClassification

logger = logging.getLogger(__name__)


# Categories whose chunks apply regardless of role.
UNIVERSAL_CATEGORIES = ("ats_rule", "banned_pattern")
# Categories whose chunks apply only when the role facet matches.
ROLE_SPECIFIC_CATEGORIES = (
    "action_verb",
    "bullet_pattern",
    "industry_norm",
    "seniority_norm",
    "mena_context",
)


def _normalize_role(role: str) -> str:
    """Map free-text role labels from the LLM classifier onto the canonical
    role tags used in the KB frontmatter.

    KB tags: software_engineer, frontend, backend, fullstack, mobile, devops,
             data_engineer, data_scientist, ml_engineer, qa, designer,
             product_manager, all

    Ordering note: more-specific checks run FIRST. "Data Scientist with ML
    focus" should still resolve to `data_scientist`, not `ml_engineer`, so
    the data_scientist / data_engineer checks intentionally precede the
    broad ml-family aliases. The ml-family substring matches (PR2a Fix 1)
    are added AFTER those — they only catch JD titles that would otherwise
    have fallen through to the `software_engineer` default.
    """
    if not role:
        out = "software_engineer"
        logger.info("knowledge_retriever._normalize_role: '' -> %r", out)
        return out
    r = role.lower()
    # Alphanum-collapsed form lets us match "ai/ml" / "AI / ML" / "AIML"
    # with a single check instead of enumerating spacings.
    r_collapsed = ''.join(ch for ch in r if ch.isalnum())
    if "frontend" in r or "front-end" in r or "front end" in r:
        out = "frontend"
    elif "backend" in r or "back-end" in r or "back end" in r:
        out = "backend"
    elif "fullstack" in r or "full-stack" in r or "full stack" in r:
        out = "fullstack"
    elif "mobile" in r or "android" in r or "ios" in r or "flutter" in r:
        out = "mobile"
    elif "devops" in r or "sre" in r or "site reliability" in r or "platform" in r:
        out = "devops"
    elif "data engineer" in r or "data eng" in r:
        out = "data_engineer"
    elif (
        "data scien" in r
        or "analytics" in r
        # PR for Issue 3 (2026-05-20) — analyst variants. The author
        # already intended analyst-tagged work to route here (the
        # "analytics" substring above), but the linguistic gap between
        # "analytics" (plural) and "analyst" (singular) meant "Junior
        # Data Analyst" was falling through to software_engineer default.
        # Narrowed list: NOT a bare "analyst" (catches Quality/Financial/
        # Security Analyst). Routes Business/Reporting Analysts here too —
        # data_scientist KB chunks (SQL / Power BI / EDA) are reasonable
        # grounding for those roles given the alternative is the
        # software_engineer default.
        or "data analyst" in r
        or "business analyst" in r
        or "reporting analyst" in r
    ):
        out = "data_scientist"
    elif (
        # Existing ml signals.
        "ml " in r
        or "machine learning" in r
        or "ai/ml" in r
        or "aiml" in r_collapsed       # catches "AI/ML" with slash removed
        or "ai engineer" in r
        # PR2a Fix 1 — AI / GenAI / LLM / MLOps / Prompt / CV / NLP / DL.
        or "ai developer" in r
        or "genai" in r_collapsed       # "GenAI" / "Gen AI" / "gen-ai"
        or "generative ai" in r
        or "llm engineer" in r
        or "llm developer" in r
        or "prompt engineer" in r
        or "mlops" in r_collapsed       # "MLOps" / "ML Ops" / "ml-ops"
        or "computer vision engineer" in r
        or "nlp engineer" in r
        or "deep learning engineer" in r
    ):
        out = "ml_engineer"
    elif "qa" in r or "quality" in r or "test" in r:
        out = "qa"
    elif "design" in r:
        out = "designer"
    elif "product manager" in r or "pm" == r.strip():
        out = "product_manager"
    else:
        out = "software_engineer"
    logger.info("knowledge_retriever._normalize_role: %r -> %r", role, out)
    return out


def _facet_matches(stored_values, wanted) -> bool:
    """Treat ['all'] as a wildcard — KB authors use it when a doc applies
    universally within its category.

    ``wanted`` may be a single string OR an iterable of accepted strings
    (PR2a Fix 3 — ``retrieve_chunks`` passes both JD-derived and
    profile-derived role tags). When an iterable, the chunk matches if
    ANY of the wanted values is in ``stored_values``.
    """
    if not stored_values:
        return False
    if "all" in stored_values:
        return True
    # str is iterable but we don't want to iterate per character.
    if isinstance(wanted, str):
        return wanted in stored_values
    try:
        wanted_list = list(wanted)
    except TypeError:
        return False
    return any(w in stored_values for w in wanted_list)


def _query_universal(jd_embedding: List[float], top_n: int) -> List[KnowledgeChunk]:
    """Top-N closest universal-category chunks. No role/seniority filter."""
    qs = (
        KnowledgeChunk.objects.filter(type__in=UNIVERSAL_CATEGORIES)
        .exclude(embedding__isnull=True)
        .order_by(CosineDistance("embedding", jd_embedding))
    )
    return list(qs[:top_n])


def _query_role_specific(
    jd_embedding: List[float],
    role_tag,
    seniority: str,
    region: str,
    top_n: int,
    over_fetch: int = 4,
) -> List[KnowledgeChunk]:
    """Top-N role-specific chunks after the facet filter.

    Over-fetches a bit, then applies Python-side facet matching because
    JSONField-array containment queries are inconsistent across backends.
    Cheap — the candidate set is ~25 chunks max after the type filter.

    ``role_tag`` may be a single string OR a list of acceptable role
    tags (PR2a Fix 3 — JD-role and profile-role union retrieval). A
    chunk matches when ANY of its `roles` values is in the
    acceptable-list.

    NOTE: kept for back-compat with any external caller; ``retrieve_chunks``
    now uses ``_query_role_specific_diversified`` (PR 3b) which fixes the
    failure mode where 3 chunks of the same category type crowded out
    other categories on a JD where one category was semantically loud.
    """
    pool = (
        KnowledgeChunk.objects.filter(type__in=ROLE_SPECIFIC_CATEGORIES)
        .exclude(embedding__isnull=True)
        .order_by(CosineDistance("embedding", jd_embedding))
    )
    chunks = list(pool[: top_n * over_fetch])

    filtered: List[KnowledgeChunk] = []
    for c in chunks:
        if not _facet_matches(c.roles, role_tag):
            continue
        if not _facet_matches(c.seniority, seniority):
            continue
        # Region: 'global' chunks always pass; non-global chunks must match.
        if c.region not in ("global", region):
            continue
        filtered.append(c)
        if len(filtered) >= top_n:
            break
    return filtered


def _query_role_specific_diversified(
    jd_embedding: List[float],
    role_tag,
    seniority: str,
    region: str,
    top_n: int,
    per_category_over_fetch: int = 3,
) -> List[KnowledgeChunk]:
    """Top-N role-specific chunks with category diversity (PR 3b).

    Replaces pure top-N by cosine distance, which empirically returned
    3 chunks of the same category type (3 industry_norms, 0 bullet_patterns)
    when one category was semantically loudest for a given JD. The
    Zeyad audit (2026-05-16) found this failure mode killing retrieval
    of the new RAG / fine-tuning bullet-pattern chunks for an AI Developer
    JD, because the older broad ``industry_norms/009_ml_engineering`` chunk
    won the cosine contest against multiple narrower siblings.

    Two-pass algorithm:
      1. First pass — at most one chunk per category type, picking each
         category's closest chunk that passes the facet filter. Guarantees
         category breadth.
      2. Second pass — fill any remaining slots by global cosine distance
         across all the per-category candidates we already fetched.

    Falls back gracefully to pure top-N behavior when the role bucket
    has chunks in only one or two categories.
    """
    # Per-category candidates: for each category, fetch the closest
    # ``per_category_over_fetch * top_n`` chunks, then python-side filter.
    per_category: dict[str, List[KnowledgeChunk]] = {}
    for category in ROLE_SPECIFIC_CATEGORIES:
        pool = (
            KnowledgeChunk.objects.filter(type=category)
            .exclude(embedding__isnull=True)
            .order_by(CosineDistance("embedding", jd_embedding))
        )
        candidates = list(pool[: per_category_over_fetch * max(top_n, 1)])
        kept: List[KnowledgeChunk] = []
        for c in candidates:
            if not _facet_matches(c.roles, role_tag):
                continue
            if not _facet_matches(c.seniority, seniority):
                continue
            if c.region not in ("global", region):
                continue
            kept.append(c)
        if kept:
            per_category[category] = kept

    return _diversify_per_category(per_category, top_n)


def _diversify_per_category(
    per_category: dict[str, list],
    top_n: int,
) -> list:
    """Pure two-pass diversification — extracted so it's unit-testable
    without the DB. Takes a dict mapping category -> ordered candidate
    list (each candidate must have ``kb_id``) and returns up to ``top_n``
    chunks with category diversity preserved.

    Pass 1: walk ``ROLE_SPECIFIC_CATEGORIES`` in canonical order, take
    the closest chunk in each category that hasn't been picked.
    Pass 2: fill remaining slots by per-category rank (the closer-to-JD
    candidates are earlier in their per-category list, so we interleave
    by per-category rank to approximate global cosine).
    """
    final: list = []
    seen_ids: set[str] = set()
    for category in ROLE_SPECIFIC_CATEGORIES:
        if len(final) >= top_n:
            break
        chunks = per_category.get(category) or []
        for c in chunks:
            if c.kb_id not in seen_ids:
                final.append(c)
                seen_ids.add(c.kb_id)
                break

    if len(final) < top_n:
        leftovers: list[tuple[int, object]] = []
        for chunks in per_category.values():
            for rank, c in enumerate(chunks):
                if c.kb_id in seen_ids:
                    continue
                leftovers.append((rank, c))
        leftovers.sort(key=lambda pair: pair[0])
        for _, c in leftovers:
            if len(final) >= top_n:
                break
            final.append(c)
            seen_ids.add(c.kb_id)

    return final


def retrieve_chunks(
    jd_text: str,
    classification: RoleClassification,
    k: int = 6,
    universal_share: int = 3,
) -> List[KnowledgeChunk]:
    """Faceted-filter + semantic-top-K retrieval.

    Args:
      jd_text: JD body. Embedded once with sentence-transformers/all-MiniLM-L6-v2.
      classification: RoleClassification from `role_classifier.classify_for_jd`.
      k: total chunks to return (default 6).
      universal_share: how many of `k` come from the universal categories.

    Returns chunks in concatenation order: universal first, then role-specific.
    """
    if k <= 0:
        return []

    n_universal = max(0, min(universal_share, k))
    n_role = max(0, k - n_universal)

    jd_embedding = embed_text(jd_text or "")

    universal = _query_universal(jd_embedding, n_universal) if n_universal else []

    # PR2a Fix 3 — union the JD-derived role with the candidate's
    # profile-derived role (when ``classify_for_jd`` attached one via
    # the ``extra="allow"`` mechanism). The chunk pool then includes
    # both "reframe-as-target-role" and "translate-from-profile-role"
    # material. When both roles normalise to the same tag, we collapse
    # to a single tag so the log + downstream behaviour match the
    # pre-PR single-role path.
    jd_role_tag = _normalize_role(classification.primary_role)
    profile_role_raw = getattr(classification, 'profile_role', '') or ''
    profile_role_tag = _normalize_role(profile_role_raw) if profile_role_raw else ''

    role_tags: list[str] = [jd_role_tag]
    role_log_label = jd_role_tag
    if profile_role_tag and profile_role_tag != jd_role_tag:
        role_tags.append(profile_role_tag)
        role_log_label = f"{jd_role_tag}+{profile_role_tag}"

    role_specific = (
        _query_role_specific_diversified(
            jd_embedding,
            role_tag=role_tags,
            seniority=classification.seniority or "mid",
            region=classification.region or "global",
            top_n=n_role,
        )
        if n_role else []
    )

    # Dedupe by kb_id while preserving order.
    seen, out = set(), []
    for c in (*universal, *role_specific):
        if c.kb_id in seen:
            continue
        seen.add(c.kb_id)
        out.append(c)

    # Category breakdown for observability — PR 3b moved retrieval to
    # category-diversified, so it's worth surfacing in logs whether the
    # diversification actually delivered breadth on this query.
    category_counts: dict[str, int] = {}
    for c in role_specific:
        cat = getattr(c, 'type', None) or 'unknown'
        category_counts[cat] = category_counts.get(cat, 0) + 1
    category_summary = ", ".join(
        f"{cat}:{n}" for cat, n in sorted(category_counts.items())
    )

    logger.info(
        "knowledge_retriever: jd_chars=%d role=%s seniority=%s region=%s "
        "-> %d chunks (universal=%d role_specific=%d) categories=[%s]",
        len(jd_text or ""), role_log_label,
        classification.seniority, classification.region,
        len(out), len(universal), len(role_specific), category_summary,
    )
    return out


def format_standards_block(chunks: List[KnowledgeChunk]) -> str:
    """Render retrieved chunks as the prompt-injection block.

    Token-conscious: only `concrete_rule` is injected (not the full body).
    Returns empty string for empty input so the caller can drop the block.
    """
    if not chunks:
        return ""
    lines = [
        "STANDARDS, EXAMPLES & CONVENTIONS",
        "=================================",
        "(Apply these rules when writing the resume. Each rule is sourced",
        "from a curated knowledge base of ATS practices, action-verb families,",
        "and role-specific bullet patterns.)",
        "",
    ]
    for i, c in enumerate(chunks, start=1):
        rule_text = (c.concrete_rule or c.body or "").strip()
        if not rule_text:
            continue
        lines.append(f"[{i}] {c.title}")
        lines.append(rule_text)
        lines.append("")
    return "\n".join(lines).rstrip()
