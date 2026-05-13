"""Lazy parser for KB-derived constants used by the bullet validator.

The bullet validator's A2 rule (action-verb start) needs a concrete set of
known action verbs. Rather than duplicating the curated KB content into a
Python constant (which would drift over time), this module parses
`profiles/knowledge/action_verbs/*.md` at first use and returns a cached
frozenset of lowercase verb tokens.

Same pattern is reused for any other KB→Python constant we need later.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import frontmatter
from django.conf import settings

KB_ROOT = Path(settings.BASE_DIR) / "profiles" / "knowledge"

# `- verb` or `* verb`, optionally with annotation in parens / em-dash / colon.
# We accept multi-word entries up to 2 tokens (e.g. "stress-test") but reject
# anything with a sentence shape.
_BULLET_LINE = re.compile(r"^\s*[-*]\s+([A-Za-z][A-Za-z\-]+(?:\s+[A-Za-z][A-Za-z\-]+)?)", re.MULTILINE)

# Single-word verbs scattered inline in narrative paragraphs as `Spearheaded ...`
# or in comma-separated lists ("led, drove, owned"). We pull standalone capitalized
# verbs from `**Verb**` markdown bold patterns since the KB authors use those.
_BOLD_VERB = re.compile(r"\*\*([A-Za-z][A-Za-z\-]+)\*\*")

# Things to discard even if they slipped past the regex: articles, prepositions,
# common adjectives that show up in the same lists.
_STOPWORDS = frozenset({
    "the", "and", "or", "of", "to", "in", "on", "for", "with", "by", "from",
    "a", "an", "is", "was", "are", "were", "be", "been", "being", "as", "at",
    "that", "this", "these", "those", "it", "its", "if", "but", "not", "no",
    "such", "any", "all", "both", "each", "few", "more", "most", "some",
    "good", "great", "strong", "weak", "vague", "use", "uses", "using",
})


def _looks_like_verb(token: str) -> bool:
    """Permissive verb filter. The KB action-verb files are heavily curated,
    so we just need to drop obvious non-verbs (stopwords, all-caps acronyms,
    very short fragments).
    """
    t = token.strip().lower()
    if len(t) < 3:
        return False
    if t in _STOPWORDS:
        return False
    # All caps in the original means it's almost always an acronym (HTTP, API).
    if token.isupper():
        return False
    return True


@lru_cache(maxsize=1)
def get_action_verbs() -> frozenset[str]:
    """Walk `action_verbs/*.md`, extract verbs from bullet/bold patterns,
    return a lowercase frozenset.

    Cached at module level — subsequent calls are free. Re-run the Django
    process (or call `get_action_verbs.cache_clear()`) after editing KB
    files in dev.
    """
    verbs: set[str] = set()
    verbs_dir = KB_ROOT / "action_verbs"
    if not verbs_dir.exists():
        return frozenset()

    for md in sorted(verbs_dir.glob("*.md")):
        try:
            post = frontmatter.loads(md.read_text(encoding="utf-8"))
        except Exception:
            continue
        body = post.content
        for m in _BULLET_LINE.finditer(body):
            tok = m.group(1).strip()
            if _looks_like_verb(tok):
                verbs.add(tok.lower())
        for m in _BOLD_VERB.finditer(body):
            tok = m.group(1).strip()
            if _looks_like_verb(tok):
                verbs.add(tok.lower())

    # Always include a safety-net core verb list. Even if the KB parser
    # somehow misses everything, A2 still has signal.
    verbs.update({
        "built", "designed", "developed", "implemented", "created",
        "led", "drove", "owned", "shipped", "launched", "delivered",
        "improved", "increased", "reduced", "cut", "optimized", "scaled",
        "migrated", "refactored", "automated", "deployed", "integrated",
        "analyzed", "researched", "investigated", "modeled", "trained",
        "tested", "validated", "measured", "benchmarked", "profiled",
        "wrote", "documented", "mentored", "coached", "presented",
        "managed", "coordinated", "planned", "estimated", "prioritized",
    })

    return frozenset(verbs)
