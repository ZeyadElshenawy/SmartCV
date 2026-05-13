---
id: ats_rules_009_contact_info_block
type: ats_rule
title: Contact Information Block — Layout and Required Fields
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# Contact Information Block — Layout and Required Fields

The contact block is the first thing every ATS parser tries to extract, and it's the most frequent extraction failure. Jobscan's 2026 audit identifies "contact information in headers/footers" as one of the top five ATS-killing mistakes — most parsers explicitly skip the document header and footer regions, so a name and email placed there often produce a "no contact info" record.

**Required fields:**
- Full name (first + last; middle initial optional).
- Email address (one professional address — not a school address that will deactivate).
- Phone number (with country code if applying internationally).
- Location (city + country, or city + state for US). A full street address is no longer expected and signals an outdated resume style.
- LinkedIn URL (optional but expected for technical roles).

**Optional fields:**
- Personal portfolio / website (one URL only).
- GitHub URL (for engineering roles).
- A second-language professional URL (Stack Overflow, Behance, Dribbble, depending on role).

**Layout rules:**
- Place the entire contact block in the document body, in the first 1.5 inches of page 1, above any horizontal divider or section header.
- Use plain text — no icons, emoji, or graphical phone/email markers.
- Each piece of contact info on its own line, OR all on one line separated by a single character (`|` or `·` or `–`).
- Hyperlinks should display the human-readable URL (`linkedin.com/in/firstlast`), not just the word "LinkedIn" — some parsers extract only the visible text, not the underlying href.

**Phone number format:**
- US: `(415) 555-0123` or `+1 415 555 0123`.
- International / MENA: `+20 100 123 4567` (Egypt), `+971 50 123 4567` (UAE), `+966 50 123 4567` (Saudi). Use the international `+` format for any application going through a multi-country ATS.
- Avoid extensions or "x123" suffixes — the parser may attach them to the number incorrectly.

**Email format:**
- Use a professional address: `firstname.lastname@gmail.com`, `firstinitiallastname@outlook.com`. Avoid handles like `xxcoolguy93xx@hotmail.com`.
- One email only. Multiple email addresses confuse the contact-extraction step in older Taleo deployments.
- Don't include a school email that will deactivate after graduation — the recruiter may try to reach you 6 months after applying.

**LinkedIn URL:**
- Use the full clean URL: `linkedin.com/in/firstlast` or `https://www.linkedin.com/in/firstlast`.
- Avoid the auto-generated URL with random digits (`linkedin.com/in/john-smith-7a8b9c12`) — set a custom URL in your LinkedIn settings first.

## Concrete rule for SmartCV

Render the contact block as plain text in the first lines of the document body, never in a header or footer. Use the layout: `Full Name` on line 1, then a single line containing email, phone with country code, city + country, LinkedIn URL, and (for engineering roles) GitHub URL, separated by `|`. Never use icons, emoji, or graphical contact markers. Display URLs as their human-readable form, not as cloaked link text.

---
sources:
  - https://www.jobscan.co/blog/ats-formatting-mistakes/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/20-ats-friendly-resume-templates/  (accessed 2026-05-12)
  - https://en.wikipedia.org/wiki/R%C3%A9sum%C3%A9  (accessed 2026-05-12)
