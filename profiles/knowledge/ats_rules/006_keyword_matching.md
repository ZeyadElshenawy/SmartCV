---
id: ats_rules_006_keyword_matching
type: ats_rule
title: Keyword Matching — Acronyms, Variants, and Spelled-Out Forms
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Keyword Matching — Acronyms, Variants, and Spelled-Out Forms

ATS keyword search varies by vendor. Oracle Taleo's standard search is the strictest documented case: it does not recognize plurals, verb tenses, abbreviations, or acronyms as variants of the same term. Searching "project manager" will not surface a resume that says only "project management"; searching "Certified Public Accountant" will not surface a resume that says only "CPA". Greenhouse and Lever use slightly more forgiving stemming, but no parser is consistent across all morphological variants.

This means the safe convention is to include both forms of any term that has a common acronym, ideally on first use. Examples:

- "Continuous Integration / Continuous Deployment (CI/CD)"
- "Amazon Web Services (AWS)"
- "Search Engine Optimization (SEO)"
- "Application Programming Interface (API)"
- "Customer Relationship Management (CRM)"
- "Master of Business Administration (MBA)"
- "Certified Kubernetes Administrator (CKA)"

After the spelled-out + acronym appears once, subsequent uses can be just the acronym.

For verb tense and plural variants, no parser is good. The mitigation is to use the form that appears in the job description. If the JD says "managed projects", use "managed" rather than "manage" or "managing". If the JD says "deployments", include the plural at least once. SmartCV's role classifier already extracts JD-specific keywords; the resume generator should mirror those exact strings rather than inventing synonyms.

**Skills section vs. body.** Skills listed only in a "Skills" section without appearing anywhere in work-experience bullets get downweighted by Taleo's Suggested Candidates ranker, which prioritizes skills mentioned in context. The fix: every claimed top-tier skill should appear in at least one experience bullet showing how it was used. Listing "React" in Skills but never mentioning it in a job bullet is a weak signal.

**Boolean search compatibility.** Recruiters often run Boolean searches like `(Python OR Java) AND (Kubernetes OR Docker) AND ("machine learning" OR "ML")`. To rank well, ensure the resume contains the literal strings the recruiter is likely to type. Quoted phrases must appear verbatim — "machine learning" must be present as a contiguous phrase, not split across "machine" and "learning" in different bullets.

**Avoid keyword stuffing.** Inserting hidden white-text keywords or padding the bottom of the resume with a comma-separated list of every technology the candidate ever touched gets the resume flagged as spam by Greenhouse and Lever's anti-stuffing filters introduced in 2023. Keywords must appear in plausible context.

## Concrete rule for SmartCV

When a JD-extracted skill has a common acronym, generate it in the resume using the format `<spelled-out form> (<acronym>)` on its first appearance. Mirror the exact tense, plural form, and casing used in the job description rather than inventing synonyms. Ensure every Top-5 skill from the JD appears in at least one work-experience bullet, not only in the Skills section. Never include a hidden keyword block or a comma-separated technology dump.

---
sources:
  - https://www.jobscan.co/blog/taleo-popular-ats-ranks-job-applications/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/20-ats-friendly-resume-templates/  (accessed 2026-05-12)
