---
id: ats_rules_001_what_is_ats_parsing
type: ats_rule
title: How Applicant Tracking Systems Parse Resumes
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# How Applicant Tracking Systems Parse Resumes

An Applicant Tracking System (ATS) is software that ingests submitted resumes, extracts structured fields (contact, work history, education, skills), and lets recruiters search and rank candidates. According to Jobscan's 2026 reporting, 99.7% of recruiters use an ATS to filter candidates, so a resume that does not parse cleanly is effectively invisible regardless of how strong the underlying experience is.

Major ATS vendors include Oracle Taleo, Workday, iCIMS, Greenhouse, Lever, and SmartRecruiters. Each has its own parser. Some (like Taleo's Suggested Candidates feature) layer machine-learning matching on top of keyword search; others rely strictly on literal keyword matches. Taleo specifically cannot cross-match plurals, verb tenses, or acronyms in its standard search — searching "project manager" will not surface "project management", and "Certified Public Accountant" will not surface "CPA". This means the resume must include both the spelled-out term and the acronym when space allows.

Parsers extract data in a fixed reading order (top-down, left-to-right). Anything that breaks that linear flow — multi-column layouts, sidebars, text inside images, content embedded in headers/footers, floating text boxes — gets dropped, garbled, or attached to the wrong section. Custom decorative fonts can render as `[NULL]` characters. Emoji icons used as section markers (phone, email symbols) confuse the field detector and cause the contact block to fail extraction entirely.

The parser then maps extracted text to known section headers ("Work Experience", "Education", "Skills"). Creative headings like "My Journey" or "The Toolkit" cause whole sections to be miscategorized or unindexed.

After parsing, the ATS scores the resume against the job requisition. Taleo, for example, awards 0–3 stars across four axes: Profile (job title match), Education, Experience, and Skills. Recruiters can configure auto-rejection rules that filter out anyone below a configured score or missing required certifications.

## Concrete rule for SmartCV

Generate resumes as a single-column, top-down document with canonical section headers ("Professional Summary", "Work Experience", "Education", "Skills", "Projects", "Certifications") and contact info in the body — never in a header/footer. Use only web-safe fonts (Arial, Calibri, Garamond, Georgia, Helvetica, Times New Roman). Never use icons, emoji, decorative dividers, or text inside images. When the source CV contains an acronym, also include the spelled-out form once (e.g., "Certified Kubernetes Administrator (CKA)") so single-pass keyword parsers like Taleo find both forms.

---
sources:
  - https://www.jobscan.co/blog/ats-formatting-mistakes/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/taleo-popular-ats-ranks-job-applications/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/20-ats-friendly-resume-templates/  (accessed 2026-05-12)
