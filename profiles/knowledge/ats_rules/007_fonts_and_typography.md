---
id: ats_rules_007_fonts_and_typography
type: ats_rule
title: Fonts, Sizes, and Typography Rules for ATS Compatibility
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: medium
last_updated: 2026-05-12
---

# Fonts, Sizes, and Typography Rules for ATS Compatibility

ATS parsers extract text by reading the embedded character glyphs in the document. Custom fonts that aren't installed on the parser's machine, or fonts where the glyph-to-character mapping is non-standard, produce garbled output or `[NULL]` placeholder characters. Decorative ligature-heavy display fonts are the worst offenders.

**Web-safe fonts that always parse correctly:**
- Arial
- Calibri
- Cambria
- Garamond
- Georgia
- Helvetica
- Tahoma
- Times New Roman
- Verdana

These are the fonts Jobscan's 2026 audit explicitly recommends as universally safe across Taleo, Workday, iCIMS, Greenhouse, and Lever.

**Fonts to avoid:**
- Any font marked "decorative" or "display" in the font picker.
- Light or thin weight variants (e.g., Helvetica Neue Ultralight) — strokes can be too thin for the parser's text-detection pass.
- Comic Sans, Papyrus, and similar novelty fonts (also a credibility issue with humans).
- Fonts with non-Latin character sets used for Latin text (some Arabic-localized fonts substitute lookalike glyphs for Latin letters).

**Font sizes.**
- Body text: 10–12 pt.
- Section headers: 14–16 pt.
- Name (top of page): 18–24 pt.

Going below 10pt to fit content on one page makes the resume hard to read for human recruiters and risks parser issues with line spacing detection.

**Bold, italic, underline.**
- Bold is safe for company names, job titles, and section headers.
- Italic is safe but should be used sparingly (typically for school names or publication titles).
- Underline is generally safe but is visually associated with hyperlinks; avoid underlining anything that isn't a link.
- Strikethrough is unsafe — some parsers include strikethrough text in extraction (so a deleted phrase still shows up), others exclude it.

**Color.**
- Black text on white background is universally safe.
- Dark blue or dark gray for headers is acceptable in modern parsers but adds zero value over plain black for ranking purposes.
- White text (used for hidden keyword stuffing) gets the resume flagged as spam.

**Spacing.**
- Single line spacing within bullets, 1.15× between bullets.
- 0.5–1.0 inch margins on all sides.
- A blank line between sections improves both human readability and parser section detection.

## Concrete rule for SmartCV

Use Arial, Calibri, Garamond, or Helvetica for all generated resumes. Body text 10–11pt, section headers 14pt bold, name 20pt. Black text only — no colored highlights, no hidden white text. Keep margins between 0.5 and 1.0 inch on all sides, with a blank line separating each section. Never use font weights below regular (400) or above bold (700).

---
sources:
  - https://www.jobscan.co/blog/ats-formatting-mistakes/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/20-ats-friendly-resume-templates/  (accessed 2026-05-12)
