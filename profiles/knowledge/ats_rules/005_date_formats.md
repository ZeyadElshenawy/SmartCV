---
id: ats_rules_005_date_formats
type: ats_rule
title: Date Format Conventions That Survive ATS Parsing
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Date Format Conventions That Survive ATS Parsing

ATS parsers extract employment dates to compute a candidate's total years of experience and to filter against minimum-experience requirements. Wrong date formats cause two failure modes: (1) the role appears with no dates at all in the recruiter view, and (2) the system miscalculates total years of experience, sometimes filtering the candidate out of search results with a "minimum 3 years" filter even when they qualify.

Jobscan's 2026 ATS-mistakes audit explicitly lists "Incorrect Date Formatting" as failure #1.

**Formats that fail:**
- `Jan '21 – Mar '23` — apostrophes confuse the year extractor (parsed as a literal string).
- `2021 – 2023` — no month means the system can't compute month-precision tenure; some systems default to January, inflating tenure; others reject the role entirely.
- `1/2021 – 3/2023` — single-digit months trip date-pattern regexes that expect two digits.
- `Spring 2021 – Fall 2023` — season names don't map to months.
- `Q3 2021 – Q1 2023` — quarter notation is not in any standard ATS dictionary.
- `Started 2021` — open-ended text without an explicit end date or "Present" marker.

**Formats that work universally:**
- `Jan 2021 – Mar 2023` (three-letter month abbreviation + 4-digit year)
- `January 2021 – March 2023` (full month name + 4-digit year)
- `01/2021 – 03/2023` (two-digit month + 4-digit year, slash-separated)

For the current role, use the literal word `Present` (not "Now", "Current", "Today", "Ongoing"): `Jan 2024 – Present`. Some parsers also accept `Jan 2024 – Current` but `Present` is universally safe.

For multiple roles at the same company, use one date range per role rather than one outer range with sub-ranges, otherwise the parser may attach all sub-roles to the outer range and lose the internal progression.

**Date placement.** Put dates on the same line as the job title and company, separated by a tab or a few spaces — not in a separate column or table cell. Example: `Senior Engineer | Acme Corp | Jan 2022 – Present`. The pipe or em-dash separator does not interfere with date extraction in any major parser.

For education, use `Sep 2018 – May 2022` for completed degrees, or `Expected May 2026` for in-progress degrees. The word "Expected" is recognized by Workday and Greenhouse as a future-graduation marker.

## Concrete rule for SmartCV

Always generate dates in the format `<3-letter month> <4-digit year>` joined by an en-dash or hyphen, with `Present` for the current role. Example: `Jan 2022 – Present`. Never use apostrophe-shortened years, season names, quarter notation, or year-only ranges. For education, use `Expected <Month> <Year>` for in-progress degrees and `<Month> <Year>` for graduated ones.

---
sources:
  - https://www.jobscan.co/blog/ats-formatting-mistakes/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/taleo-popular-ats-ranks-job-applications/  (accessed 2026-05-12)
