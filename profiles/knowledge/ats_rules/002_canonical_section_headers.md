---
id: ats_rules_002_canonical_section_headers
type: ats_rule
title: Canonical Section Headers ATS Parsers Recognize
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Canonical Section Headers ATS Parsers Recognize

ATS parsers identify resume sections by matching against a known dictionary of header strings. When a header is unrecognized, the parser either skips the entire block or merges it with the previous section. Jobscan's 2026 audit lists "creative section headings" as one of the top five reasons resumes fail ATS extraction.

The canonical headers that all major parsers (Taleo, Workday, Greenhouse, Lever, iCIMS) recognize are:

- **Contact** / **Contact Information** (or no header at all — the parser detects email + phone patterns at the top of the document)
- **Professional Summary** / **Summary** / **Profile**
- **Work Experience** / **Professional Experience** / **Experience** / **Employment History**
- **Education**
- **Skills** / **Technical Skills** / **Core Competencies**
- **Projects** / **Personal Projects** (for engineering / student resumes)
- **Certifications** / **Licenses**
- **Publications** (for academic / research resumes)
- **Awards** / **Honors**
- **Languages**
- **Volunteer Experience**

Headers that frequently break parsing include "My Journey", "Where I've Been", "What I Bring", "The Toolkit", "My Superpowers", "Where I Studied", "Things I'm Proud Of". These cause the parser to either ignore the section or attach its content to whatever standard header preceded it.

Subtle quirks: Workday and Greenhouse generally accept "Experience" and "Work Experience" interchangeably, but iCIMS prefers "Work Experience" specifically. "Career History" works in most parsers but not all — "Work Experience" is universally safe. Avoid prefixing or suffixing headers ("Relevant Work Experience" sometimes works, "Most Recent Work Experience" usually does not).

Format the header itself as a single line in bold or all-caps, with a blank line before and after. Do not put it inside a table cell, do not use a horizontal rule as a substitute, and do not combine two sections under one header ("Education & Certifications" causes Taleo to put certifications under the Education extraction field, which then fails downstream filters that look at Certifications separately).

## Concrete rule for SmartCV

Use exactly these section names, in this order, when generating resumes: "Professional Summary", "Skills", "Work Experience", "Projects" (if applicable), "Education", "Certifications" (if applicable). Never combine two categories under one header. Never invent creative headers — even for personality. If the source CV uses a creative header, normalize it to the canonical equivalent during generation.

---
sources:
  - https://www.jobscan.co/blog/ats-formatting-mistakes/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/20-ats-friendly-resume-templates/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/convert-your-resume-to-an-ats-friendly-format/  (accessed 2026-05-12)
