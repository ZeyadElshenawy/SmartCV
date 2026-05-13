---
id: ats_rules_003_file_format_pdf_vs_docx
type: ats_rule
title: File Format Choice — PDF vs DOCX for ATS Submission
roles: [all]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# File Format Choice — PDF vs DOCX for ATS Submission

The two ATS-acceptable formats are .docx (Microsoft Word) and text-based .pdf. Image-based PDFs (scans, exports from design tools that flatten text into raster) are unreadable and parse to empty strings. Jobscan's "Anatomy of an ATS Friendly Resume" piece (2026) recommends submitting either ".docx file or a text-based PDF" as the universal safe pair.

The practical rule for testing whether a PDF is parseable: open the file, select all text (Ctrl+A), and copy into a plain Notepad / TextEdit window. If the text comes through in correct reading order with no merged words or `[NULL]` characters, an ATS will likely parse it. If the copy-paste produces garbled output, no ATS will succeed either. This is Jobscan's "plain text golden rule".

DOCX advantages:
- Universally parseable across all major ATS vendors (Taleo, Workday, iCIMS, Greenhouse, Lever).
- Easier for the ATS to extract structured fields because Word styles map directly to section headers.
- Some legacy systems (older Taleo deployments, in particular) treat DOCX as the highest-fidelity input.

PDF advantages:
- Preserves visual formatting exactly across machines — what the recruiter sees when downloading is what you generated.
- Modern parsers (Greenhouse, Lever, Workday post-2023) handle text-based PDFs cleanly.
- No risk of font substitution or layout shifts on the recruiter's screen.

PDF risks to avoid:
- PDFs exported from Canva, Figma, or design tools often embed text as outlined paths (i.e., as vector shapes), making them unparseable. Always export "with text" or "selectable text" enabled.
- Password-protected or encrypted PDFs fail every ATS.
- Multi-page PDFs with embedded form fields can confuse parsers — the parser may try to extract the form structure rather than the text.

When the job application form lets you choose, pick DOCX for any application going through a system you don't recognize, and PDF for ones where you specifically want format preservation (e.g., Greenhouse-hosted application forms that show a parsed preview).

## Concrete rule for SmartCV

Default to DOCX export. Offer PDF as a secondary option but always export PDF using a text-based renderer (xhtml2pdf, ReportLab, or `wkhtmltopdf` with text mode), never an image-flattening renderer. Never password-protect generated files. Verify before delivery that selecting all text in the PDF and pasting into a plain text editor reproduces the resume content in the correct order.

---
sources:
  - https://www.jobscan.co/blog/20-ats-friendly-resume-templates/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/convert-your-resume-to-an-ats-friendly-format/  (accessed 2026-05-12)
  - https://www.jobscan.co/blog/ats-formatting-mistakes/  (accessed 2026-05-12)
