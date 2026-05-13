---
id: ats_rules_011_resume_length
type: ats_rule
title: Resume Length — Page Count Conventions by Career Stage
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: medium
last_updated: 2026-05-12
---

# Resume Length — Page Count Conventions by Career Stage

The "one-page resume" rule has become more nuanced. The Wikipedia overview of résumés (2026) states the universal convention is one or two pages. MIT's Career Advising & Professional Development guidance is "stick to one page, unless you have extensive experience or an advanced degree". The practical breakdown by experience level:

**Students, interns, new grads (0–2 years):** strictly one page.
- Anything longer signals padding or inability to prioritize.
- If projects + coursework + activities won't fit, cut activities first.

**Junior to mid-level (2–10 years):** one page, with two pages acceptable for technical roles where projects and a deep tech stack genuinely require it.
- A second page must add new signal — never repeat the same role's responsibilities at multiple levels of granularity.
- Recruiter heuristic: initial scans are extremely brief, so the first 1/3 of page 1 must contain the strongest signal.

**Senior (10+ years):** two pages standard, three pages acceptable for highly senior roles (Principal Engineer, Director+).
- Truncate roles older than 10–15 years to a single line: title + company + dates only.
- Older roles can be grouped under an "Earlier Experience" header.

**Academic / research / medical CVs:** no page limit. These are CVs, not resumes — they list every publication, presentation, grant, and committee role. This convention does not apply to industry resumes.

**MENA region note:** Egypt, Saudi Arabia, UAE, and most Gulf countries follow the international 1–2 page convention for industry roles, but personal-data sections (photo, marital status, nationality, sometimes religion) are commonly included, which adds visual mass without page count. See `mena_context/` for the full local-norms breakdown.

**ATS-side considerations.** ATS parsers don't penalize page count directly — they extract the same fields whether the resume is one page or three. But Taleo's Suggested Candidates ranker downweights candidates whose resume contains a high noise-to-signal ratio (lots of generic content with few quantified accomplishments). A two-page resume of weak bullets ranks worse than a one-page resume of strong bullets.

**Common mistakes that bloat length:**
- Listing every technology ever touched in a 30-item Skills section.
- Including a 2-paragraph "Objective" or "About Me" section above the Summary.
- Repeating the same accomplishment under multiple roles.
- Adding a "References" section (use "References available upon request" only if asked, otherwise omit entirely).
- Adding hobbies or interests unless directly relevant.

## Concrete rule for SmartCV

Generate one-page resumes for candidates with under 7 years of experience and two-page resumes for senior candidates with deeper history. For roles older than 10 years, output only a single-line summary (title + company + dates). Do not generate "Objective", "References", or "Hobbies" sections by default. If the source CV contains content that won't fit, drop the oldest roles and the lowest-relevance projects first — never sacrifice quantification or specificity from recent roles to save space.

---
sources:
  - https://capd.mit.edu/resources/resumes/  (accessed 2026-05-12)
  - https://en.wikipedia.org/wiki/R%C3%A9sum%C3%A9  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/20-ats-friendly-resume-templates/  (accessed 2026-05-12)
