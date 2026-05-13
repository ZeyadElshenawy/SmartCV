---
id: ats_rules_010_bullet_marker_safety
type: ats_rule
title: Safe Bullet Markers and List Formatting
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: medium
last_updated: 2026-05-12
---

# Safe Bullet Markers and List Formatting

ATS parsers detect bullet lists by looking for known marker characters at the start of each line. Unrecognized markers either get extracted as part of the bullet text (cluttering search results with stray characters) or cause the parser to skip the line entirely. Jobscan's "Anatomy of an ATS Friendly Resume" recommends "standard solid or open circles or squares" as the universally safe bullet markers.

**Safe bullet markers:**
- `•` (U+2022 bullet) — universally safe across Word, Google Docs, and PDF exports.
- `◦` (U+25E6 white bullet) — safe but visually subtle.
- `■` (U+25A0 black square) and `□` (U+25A1 white square) — safe.
- `-` (hyphen-minus, U+002D) — safe in plain text and markdown-derived exports, but visually less polished than `•`.
- The default Word "Bullet List" style — Word converts these to `•` on export to PDF and DOCX.

**Bullet markers to avoid:**
- `→`, `►`, `✓`, `★`, `❯`, `◆` — non-standard arrow / checkmark / star characters that some parsers strip and others extract as literal text.
- Custom image bullets (PNG/SVG inserted as the marker) — invisible to parsers.
- Emoji bullets (`🔵`, `✅`, `📌`) — same problem.
- Fancy decorative bullets from Word's "Define New Bullet" feature using a Wingdings or symbol-font glyph — these often render as `[NULL]` or as an unrelated Latin character.

**Indentation.** One consistent indent level per section. Avoid nested bullets — parsers flatten them. Blank line before the bullet block, none between consecutive bullets in the same role.

**Line length.** 1–2 lines per bullet, 100–200 characters. Avoid 3+ line wraps (paragraph-like) and single-keyword one-liners.

**Period at end?** Pick one convention (all periods or none) consistently across the document.

**Bullet count per role.**
- 3–5 for the two most recent roles.
- 2–3 for older roles (5+ years back).
- 1–2 for roles older than 10 years.
- More than 6 per role adds no signal.

## Concrete rule for SmartCV

Use the `•` (U+2022) character as the bullet marker for every bullet in generated resumes. Never use arrows, checkmarks, stars, or emoji as bullet markers. Maintain one indent level only — no nested bullets. Generate 3–5 bullets per role for the two most recent positions and 2–3 for older ones. Each bullet should be 100–200 characters; if a bullet would wrap to a third line, either tighten the wording or split it into two bullets.

---
sources:
  - https://www.jobscan.co/blog/20-ats-friendly-resume-templates/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/ats-formatting-mistakes/  (accessed 2026-05-12)
  - https://capd.mit.edu/resources/resumes/  (accessed 2026-05-12)
