---
id: ats_rules_004_columns_tables_graphics
type: ats_rule
title: Why Columns, Tables, and Graphics Break ATS Parsing
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Why Columns, Tables, and Graphics Break ATS Parsing

ATS parsers read documents top-down and left-to-right as a single linear stream. Multi-column layouts, tables, and graphics each break that assumption in different ways, with predictable failure modes.

**Multi-column layouts.** A two-column resume (skills sidebar on the left, experience on the right) typically gets parsed by reading the entire left column down to the bottom, then the entire right column. The result: skills are concatenated with the candidate's name and contact info into one blob, and the work experience block is missing its associated skills context. Some modern parsers (post-2023 Workday, Greenhouse) detect column boundaries, but Taleo and older iCIMS deployments do not. Jobscan's 2026 audit confirms multi-column layouts as one of the top five ATS-killing mistakes.

**Tables.** Even single-row tables used purely for alignment (e.g., to put job title on the left and dates on the right) can fragment text. The parser reads cell-by-cell, often producing output like "Senior Engineer 2022–Present Acme Corp Built X did Y..." with no whitespace. More complex tables (skill matrices with rows for skill name and columns for proficiency) typically lose all column-header context, so the parser sees "Python Java SQL Expert Intermediate Beginner" as one undifferentiated string.

**Graphics, charts, infographics.** Skill-level bar graphs, donut charts of proficiency percentages, and progress bars are entirely invisible to ATS — they parse to nothing. The same applies to logos, profile photos, decorative dividers, and icons used in section headers. Jobscan recommends replacing skill bars with text proficiency labels: "Java (Expert)" or "Python — 4 years" rather than a 90% filled bar.

**Headers and footers.** Most ATS parsers explicitly skip the header and footer regions of Word and PDF documents. Putting the candidate name, email, or phone number in the header is the single most common cause of "ATS extracted no contact info" failures. The fix is to repeat all critical info in the body (top of page 1).

**Text inside images.** Infographic resumes built in Canva or Figma often render the entire resume as one image embedded in a PDF. Result: the parser extracts zero characters.

## Concrete rule for SmartCV

Generate resumes as a strict single-column layout. Never use tables, even invisibly for alignment — use plain text with tab stops or simple inline formatting. Never include charts, skill bars, donut graphs, profile photos, or icons. Express skill proficiency as text: "Python (4 years, expert)", not as a graphical bar. Place all contact info in the document body, never in a header or footer region. Maintain at least 0.5 inch margins so parsers have a clear text region to work with.

---
sources:
  - https://www.jobscan.co/blog/ats-formatting-mistakes/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/20-ats-friendly-resume-templates/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/convert-your-resume-to-an-ats-friendly-format/  (accessed 2026-05-12)
