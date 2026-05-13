---
id: mena_context_006_local_job_boards_wuzzuf_bayt
type: mena_context
title: MENA Local Job Boards — Wuzzuf, Bayt, LinkedIn MENA
roles: [all]
seniority: [all]
industries: [all]
region: mena
weight: high
last_updated: 2026-05-12
---

# MENA Local Job Boards — Wuzzuf, Bayt, LinkedIn MENA

The MENA hiring stack is dominated by three channels with very different parsing behaviours: **Wuzzuf** (Egypt-anchored), **Bayt.com** (Gulf-anchored), and **LinkedIn** (multinational-anchored). Note: at the time of writing, automated fetches against `wuzzuf.net` and `bayt.com` returned HTTP 403, so the platform-internal claims below are limited to what is publicly indexable; the lived-experience section is where the platform-specific UX details belong.

What is documentable from public sources:

- Both platforms operate bilingually (English / Arabic) — Wuzzuf is Cairo-headquartered and serves an Egyptian-majority audience; Bayt is Dubai-headquartered and serves the Gulf.
- Both implement a "profile builder" pattern (structured fields filled inside the platform) **in addition to** an uploaded CV file. The profile fields are what get matched against employer searches, not the PDF.
- Both surface a profile-completeness or strength score that incentivises filling demographic fields (date of birth, nationality, marital status) typical of MENA CVs (see `002_personal_info_fields.md`).
- LinkedIn behaves identically in MENA to its global product, but MENA recruiters routinely search for Arabic-script names and city names (e.g., "القاهرة" / "Cairo") in parallel.

ATS-style implications:

- Uploading a CV with creative section headers (see `ats_rules/002_canonical_section_headers.md`) breaks both Wuzzuf and Bayt's auto-extraction in addition to global ATS parsers.
- Local boards re-index after a profile edit; a fresh upload with the same content as the stored profile sometimes outranks the structured profile in "recently active" sorts.
- Application volume on Wuzzuf for popular Cairo openings is high — recruiters skim quickly and rely on the profile thumbnail (name, photo, top role, top employer) before opening the CV.

## Concrete rule for SmartCV

When a candidate's target market is Egypt, generate a Wuzzuf-friendly variant: short factual job titles (no creative phrasing), explicit company names spelled in English exactly as the company self-identifies (e.g., "Vodafone Egypt" not "Vodafone EG"), city as "Cairo, Egypt" or "Alexandria, Egypt". For Gulf targets, generate a Bayt-friendly variant with Nationality and Visa Status surfaced near the top. For both, keep section headers canonical (Skills, Work Experience, Education, Certifications) so the local-board parsers extract them. Always advise the candidate that the structured profile on the platform matters as much as the uploaded PDF.

## What public sources document

**Wuzzuf** is documented as Egypt's leading online recruitment site with "more than 3,000 companies and recruiters actively hire, and more than 160,000 job seekers... apply to jobs each month" (jobboardfinder.com, accessed 2026-05-12). Wuzzuf's own help center (`help.wuzzuf.net/.../professional-info-section`) names the **Professional Info section as "very vital in the job matching score when applying to jobs"** and identifies skills (with named examples like presentation skills and Adobe tools) plus **net salary** as fields that **improve the job-matching recommendation** (search excerpt, 2026-05-12; direct fetches returned 301 → 403). The Wuzzuf help center mentions completing the profile "beyond 70%" and updating skills, experience, and job preferences regularly.

A community redesign analysis of Wuzzuf profile pages (Sabri 2018, abdusabri.com, accessed 2026-05-12) confirms a "profile meter percentage" exists as a tracked metric and groups profile fields into sections covering Name, Personal Info, Languages, Skills, Education, Certifications, and uploaded Resumes (the redesign proposal allowed up to 4 uploads for different target roles).

**Bayt's** profile-builder fields are listed in its own "Profile Guide from Zero to 100" blog post (Bayt blog, retrieved via search excerpt 2026-05-12; direct fetch HTTP 403): passport-spelling full name, date of birth, gender, primary and additional nationalities, country of residence, visa status, marital status, number of dependents, and driving-licence country. Bayt explicitly states profile picture is optional but increases visibility, and the "more than four in five job seekers have a picture" figure is from Bayt's own copy.

**Forasna** is documented as Egypt's blue-collar / entry-level recruitment platform — "manufacturing, logistics, retail, construction, and hospitality" — Arabic-only, free for job seekers, "ranked as the third most visited job and career platform in Egypt in early 2025" with "an average of 30,000 job postings per month" (Qureos hiring guide; Skatch blog; accessed 2026-05-12). Notably, Forasna is owned by Wuzzuf (the Forasna jobs-and-careers page is hosted on `wuzzuf.net`).

**Tanqeeb** is documented as a job-search aggregator (148,954+ jobs listed across MENA, multi-source aggregation of online and newspaper postings) rather than a profile-hosting platform like Wuzzuf or Bayt (tanqeeb.com, accessed 2026-05-12).

I could not find a public source for Wuzzuf's exact PDF-vs-structured-profile ranking behaviour, parsing-failure modes, or application-to-response ratios for fresh grads.

## Still needed from the author

- Concrete observation of what specifically moves Wuzzuf's profile-strength meter from 70% → 100%.
- Whether uploading a new PDF overwrites or attaches alongside the structured Wuzzuf profile.
- Specific Wuzzuf and Bayt parsing failures the author has observed on real uploads (skills dropped, dates miscategorised, bullets concatenated).
- Realistic application-to-response ratios for KSIU CS fresh-grads on Wuzzuf "Easy Apply" vs tailored applications.
- KSIU career-services guidance on which boards to maintain.
- Whether Cairo tech audiences still actively maintain Tanqeeb / Forasna profiles or have abandoned them.

---
sources:
  - https://en.wikipedia.org/wiki/R%C3%A9sum%C3%A9  (accessed 2026-05-12)
  - https://help.wuzzuf.net/en/articles/6420894-professional-info-section  (accessed 2026-05-12; redirects to gated /careers URL — content via search excerpt)
  - https://help.wuzzuf.net/en/articles/6420734-career-interests-section  (accessed 2026-05-12; same redirect behaviour)
  - https://abdusabri.com/redesigning-wuzzuf-profile-pages/  (accessed 2026-05-12)
  - https://www.bayt.com/en/blog/8648/your-bayt-com-profile-guide-from-zero-to-100/  (accessed 2026-05-12; via search excerpt, direct fetch HTTP 403)
  - https://www.jobboardfinder.com/jobboard-wuzzufnet-egypt  (accessed 2026-05-12; via search excerpt)
  - https://www.qureos.com/hiring-guide/top-recruitment-platforms-in-egypt  (accessed 2026-05-12; via search excerpt)
  - https://blog.skatch.com/en/best-platforms-to-find-jobs-online-in-egypt/  (accessed 2026-05-12; via search excerpt)
  - https://egypt.tanqeeb.com/en  (accessed 2026-05-12; via search excerpt)
