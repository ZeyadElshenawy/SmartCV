---
id: banned_patterns_003_vague_phrasing
type: banned_pattern
title: Vague Phrasing — Phrases That Add Words Without Adding Information
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Vague Phrasing — Phrases That Add Words Without Adding Information

Vague phrasing is the failure mode of bullets that pass buzzword and AI-tell filters but convey little signal. Test: would this bullet remain accurate pasted into any other resume in the same field? `prompt_guards.py` rule 3: "A bullet that could be pasted into any other resume in the same field is too generic."

**Vague verb phrases (replace):**
"Worked on" / "Helped with" / "Was involved in" / "Participated in" / "Played a key role in" / "Was part of a team that..." / "Contributed to" (no scope) / "Took ownership of" / "Drove" (no object) → name the specific action and scope.

**Vague outcome phrases (replace):**
"Improved efficiency" / "Enhanced productivity" / "Optimized performance" / "Streamlined processes" / "Increased customer satisfaction" / "Drove business value" / "Delivered impactful results" / "Made a meaningful contribution" / "Generated positive ROI" → name the metric.

**Vague qualifier phrases (replace):**
"A variety of" / "Multiple projects" / "Various technologies" / "Several initiatives" / "Numerous improvements" / "Extensive experience" / "Deep expertise" / "Strong understanding of" → use the actual count or evidence.

**Vague closer phrases (replace):**
"to ensure success" / "to drive growth" / "to support business objectives" / "to enable scalability" / "to facilitate collaboration" → name the outcome and metric.

**Worked examples:**

- VAGUE: "Worked on a variety of projects to improve efficiency across multiple teams."
- SPECIFIC: "Built a shared CI workflow library used by 22 repos; flake rate dropped from 9% to 1.2% across the org over Q3."

- VAGUE: "Played a key role in optimizing performance and enhancing user experience."
- SPECIFIC: "Cut LCP on the marketing homepage from 4.1s to 1.8s by deferring 6 third-party scripts and switching hero to AVIF; bounce rate fell from 47% to 38%."

**Why this is harder than the buzzword filter.** Vague phrasing uses valid English; what's missing is specific evidence. The validator rule is semantic: a bullet is vague if it contains the patterns above AND lacks both a concrete artifact name AND a quantified metric.

**The "any other resume" test.** Strip the concrete words — does the remainder still claim something? "Worked on multiple projects to improve efficiency" still reads as a complete sentence with no specifics — flag it. "Refactored the auth service from session cookies to JWT, cutting p95 from 280ms to 90ms" depends entirely on its specifics — keep it.

## Concrete rule for SmartCV

The bullet validator must flag any bullet that contains a vague-verb pattern (Worked on, Helped with, Was involved in, Played a role in, Contributed to without scope, Drove without object) AND lacks both a concrete artifact name and a quantified metric. Flag any bullet using vague outcome phrases (Improved efficiency, Enhanced productivity, Optimized performance) without a specific metric. Flag any vague qualifier (a variety of, multiple, various, several, numerous, extensive, deep) used as a substitute for a specific count.

---
sources:
  - https://capd.mit.edu/resources/resumes/  (accessed 2026-05-12)
  - https://www.indeed.com/career-advice/resume-mistakes-to-avoid/  (accessed 2026-05-12)
