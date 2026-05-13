"""Index the curated RAG knowledge base into the `KnowledgeChunk` table.

Walks `profiles/knowledge/<category>/*.md` (skipping README.md), parses YAML
frontmatter via `python-frontmatter`, extracts the body + the
`## Concrete rule for SmartCV` section + the `sources:` list, embeds the
combined text with `sentence-transformers/all-MiniLM-L6-v2`, and upserts one
row per file keyed on the frontmatter `id:` field.

Re-running the command refreshes embeddings and content for any file whose
markdown changed. Files that disappear are NOT pruned — pruning is a manual
flag (`--prune`) to avoid surprises when running from a half-checked-out tree.

Usage:
    python manage.py build_knowledge_index
    python manage.py build_knowledge_index --dry-run
    python manage.py build_knowledge_index --category mena_context
    python manage.py build_knowledge_index --prune
"""
from __future__ import annotations

import re
import time
from datetime import date as date_cls
from pathlib import Path

import frontmatter
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from profiles.models import KnowledgeChunk
from profiles.services.embeddings import embed_texts


KB_ROOT = Path(settings.BASE_DIR) / "profiles" / "knowledge"

# Frontmatter `type:` values, mapped from the directory name. Keeps the
# directory the source of truth even if a stray frontmatter field disagrees.
_DIR_TO_TYPE = {
    "ats_rules": "ats_rule",
    "action_verbs": "action_verb",
    "bullet_patterns": "bullet_pattern",
    "industry_norms": "industry_norm",
    "seniority_norms": "seniority_norm",
    "mena_context": "mena_context",
    "banned_patterns": "banned_pattern",
}


_CONCRETE_RULE_HEADING = re.compile(r"^##\s+Concrete rule for SmartCV\s*$", re.IGNORECASE)
_SOURCES_HEADING = re.compile(r"^---\s*\nsources:\s*\n", re.MULTILINE)


def _extract_concrete_rule(body: str) -> str:
    """Pull the text of the `## Concrete rule for SmartCV` section.

    Returns everything between that heading and the next top-level
    heading / horizontal rule, stripped. Empty string when the heading is
    missing (KB files SHOULD have it; older drafts may not).
    """
    lines = body.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if _CONCRETE_RULE_HEADING.match(ln):
            start = i + 1
            break
    if start is None:
        return ""
    out = []
    for ln in lines[start:]:
        s = ln.strip()
        if s.startswith("## ") or s.startswith("---"):
            break
        out.append(ln)
    return "\n".join(out).strip()


def _extract_sources_block(raw: str) -> list[str]:
    """Pull URLs from the trailing `--- sources:` YAML-ish list. Best-effort;
    KB files use a mix of `- url` and `- url  (accessed YYYY-MM-DD)` shapes."""
    match = _SOURCES_HEADING.search(raw)
    if not match:
        return []
    tail = raw[match.end():]
    out = []
    for ln in tail.splitlines():
        s = ln.strip()
        if not s.startswith("-"):
            continue
        cleaned = s.lstrip("-").strip()
        # Strip the trailing "(accessed YYYY-MM-DD)" annotation.
        cleaned = re.sub(r"\s*\(accessed[^)]*\)\s*$", "", cleaned).strip()
        if cleaned:
            out.append(cleaned)
    return out


def _parse_seniority(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _coerce_last_updated(value) -> date_cls | None:
    if value is None:
        return None
    if isinstance(value, date_cls):
        return value
    try:
        return date_cls.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        return None


class Command(BaseCommand):
    help = "Index profiles/knowledge/**/*.md into KnowledgeChunk for RAG retrieval."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Parse + report counts but don't touch the database.",
        )
        parser.add_argument(
            "--category",
            help="Only index a single category (directory name, e.g. mena_context).",
        )
        parser.add_argument(
            "--prune", action="store_true",
            help="Delete KnowledgeChunk rows whose source .md file no longer exists.",
        )

    def handle(self, *args, **opts):
        if not KB_ROOT.exists():
            raise CommandError(f"Knowledge base directory not found: {KB_ROOT}")

        category_filter = opts.get("category")
        dry_run = bool(opts.get("dry_run"))
        prune = bool(opts.get("prune"))

        files = sorted(self._iter_md_files(category_filter))
        if not files:
            self.stdout.write(self.style.WARNING("No knowledge files found."))
            return

        self.stdout.write(f"Indexing {len(files)} files from {KB_ROOT}")

        parsed_rows: list[tuple[Path, dict, str, str, list[str]]] = []
        for path in files:
            try:
                row = self._parse_file(path)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"  parse-fail: {path} — {exc}"))
                continue
            parsed_rows.append(row)

        # Compose the text we actually embed: title + concrete rule + body
        # (body is included so retrieval can still match files whose
        # concrete-rule sentence doesn't lexically overlap with the JD).
        texts_to_embed = [
            f"{meta.get('title', '')}\n\n{concrete}\n\n{body}".strip()
            for (_path, meta, body, concrete, _sources) in parsed_rows
        ]

        t0 = time.perf_counter()
        if dry_run:
            self.stdout.write(self.style.NOTICE("--dry-run: skipping embedding + DB writes."))
            embeddings: list[list[float] | None] = [None] * len(texts_to_embed)
        else:
            self.stdout.write(f"  embedding {len(texts_to_embed)} chunks…")
            embeddings = embed_texts(texts_to_embed)
        embed_seconds = round(time.perf_counter() - t0, 2)

        by_category: dict[str, int] = {}
        new_count = 0
        updated_count = 0

        for (path, meta, body, concrete, sources), vec in zip(parsed_rows, embeddings):
            kb_id = (meta.get("id") or "").strip()
            if not kb_id:
                self.stderr.write(self.style.WARNING(f"  skip (no id): {path}"))
                continue

            doc_type = _DIR_TO_TYPE.get(path.parent.name, meta.get("type") or "")
            row_kwargs = {
                "title": str(meta.get("title", "")).strip(),
                "body": body,
                "concrete_rule": concrete,
                "sources": sources,
                "type": doc_type,
                "roles": list(meta.get("roles") or []),
                "seniority": _parse_seniority(meta.get("seniority")),
                "industries": list(meta.get("industries") or []),
                "region": str(meta.get("region") or "global").strip() or "global",
                "weight": str(meta.get("weight") or "medium").strip() or "medium",
                "last_updated": _coerce_last_updated(meta.get("last_updated")),
                "source_path": str(path.relative_to(KB_ROOT.parent)).replace("\\", "/"),
                "embedding": vec,
            }

            by_category[doc_type] = by_category.get(doc_type, 0) + 1

            if dry_run:
                continue

            obj, created = KnowledgeChunk.objects.update_or_create(
                kb_id=kb_id, defaults=row_kwargs,
            )
            if created:
                new_count += 1
            else:
                updated_count += 1

        # Reporting
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("--- index summary ---"))
        for cat in sorted(by_category):
            self.stdout.write(f"  {cat:20s} {by_category[cat]:4d}")
        self.stdout.write(f"  embedding wall: {embed_seconds}s")
        if not dry_run:
            self.stdout.write(f"  new: {new_count}   updated: {updated_count}")

        if prune and not dry_run:
            kept_paths = {row[0].relative_to(KB_ROOT.parent).as_posix() for row in parsed_rows}
            stale = KnowledgeChunk.objects.exclude(source_path__in=kept_paths)
            n_stale = stale.count()
            if n_stale:
                self.stdout.write(self.style.WARNING(f"  pruning {n_stale} orphan rows…"))
                stale.delete()

    # ------------------------------------------------------------------ helpers

    def _iter_md_files(self, category_filter: str | None):
        for sub in sorted(KB_ROOT.iterdir()):
            if not sub.is_dir():
                continue
            if category_filter and sub.name != category_filter:
                continue
            for md in sorted(sub.glob("*.md")):
                if md.name.lower() == "readme.md":
                    continue
                yield md

    def _parse_file(self, path: Path) -> tuple[Path, dict, str, str, list[str]]:
        raw = path.read_text(encoding="utf-8")
        post = frontmatter.loads(raw)
        body = post.content
        concrete = _extract_concrete_rule(body)
        sources = _extract_sources_block(raw)
        return (path, dict(post.metadata), body, concrete, sources)
