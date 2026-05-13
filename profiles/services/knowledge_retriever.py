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
    """
    if not role:
        return "software_engineer"
    r = role.lower()
    if "frontend" in r or "front-end" in r or "front end" in r:
        return "frontend"
    if "backend" in r or "back-end" in r or "back end" in r:
        return "backend"
    if "fullstack" in r or "full-stack" in r or "full stack" in r:
        return "fullstack"
    if "mobile" in r or "android" in r or "ios" in r or "flutter" in r:
        return "mobile"
    if "devops" in r or "sre" in r or "platform" in r:
        return "devops"
    if "data engineer" in r or "data eng" in r:
        return "data_engineer"
    if "data scien" in r or "analytics" in r:
        return "data_scientist"
    if "ml " in r or "machine learning" in r or "ai/ml" in r or "ai engineer" in r:
        return "ml_engineer"
    if "qa" in r or "quality" in r or "test" in r:
        return "qa"
    if "design" in r:
        return "designer"
    if "product manager" in r or "pm" == r.strip():
        return "product_manager"
    return "software_engineer"


def _facet_matches(stored_values: Iterable[str], wanted: str) -> bool:
    """Treat ['all'] as a wildcard — KB authors use it when a doc applies
    universally within its category."""
    if not stored_values:
        return False
    if "all" in stored_values:
        return True
    return wanted in stored_values


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
    role_tag: str,
    seniority: str,
    region: str,
    top_n: int,
    over_fetch: int = 4,
) -> List[KnowledgeChunk]:
    """Top-N role-specific chunks after the facet filter.

    Over-fetches a bit, then applies Python-side facet matching because
    JSONField-array containment queries are inconsistent across backends.
    Cheap — the candidate set is ~25 chunks max after the type filter.
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
    role_tag = _normalize_role(classification.primary_role)
    role_specific = (
        _query_role_specific(
            jd_embedding,
            role_tag=role_tag,
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

    logger.info(
        "knowledge_retriever: jd_chars=%d role=%s seniority=%s region=%s "
        "→ %d chunks (universal=%d role_specific=%d)",
        len(jd_text or ""), role_tag, classification.seniority, classification.region,
        len(out), len(universal), len(role_specific),
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
