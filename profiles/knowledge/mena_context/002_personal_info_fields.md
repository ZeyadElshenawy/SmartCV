---
id: mena_context_002_personal_info_fields
type: mena_context
title: Personal Information Fields Expected in MENA CVs
roles: [all]
seniority: [all]
industries: [all]
region: mena
weight: high
last_updated: 2026-05-12
---

# Personal Information Fields Expected in MENA CVs

CVs submitted to employers in the Middle East and North Africa routinely include personal data that Anglo-American hiring guides actively warn against. Wikipedia's résumé article states the convention plainly: "many Middle East and African countries and some parts of Asia require personal data (e.g., photograph, gender, marital status, children) while this is not accepted in the UK, U.S., and some European countries."

Typical fields that appear on a MENA-targeted CV but would be removed from a US-targeted one include:

- **Date of birth** (or full Gregorian birth date plus age in years)
- **Place of birth** (city, country)
- **Nationality** (often a hard requirement on Gulf applications because of Saudization / Emiratisation quota reporting — see the Saudization article: companies must "fill their workforce with Saudi nationals up to certain levels," which requires nationality data per applicant)
- **Marital status** (Single / Married / Divorced)
- **Number of children** (Gulf especially)
- **Gender**
- **Religion** (more common in some Gulf and Levant applications)
- **Photograph** (head-and-shoulders, formal)
- **National ID number** (Egyptian National ID, Saudi Iqama, Emirates ID — usually only at offer/onboarding stage, but sometimes on the CV)
- **Driving licence status** (frequent for Gulf roles where commuting expectations are high)

This contrasts with the US/UK norm, where US Title VII guidance and UK Equality Act 2010 culture lead candidates to omit photo, age, marital status, and religion to reduce discrimination risk.

## Concrete rule for SmartCV

When the target market is MENA, generate a "Personal Information" or "Personal Details" block near the top of the CV (after Contact, before Summary) containing: Date of Birth, Nationality, Marital Status, and (if the candidate has supplied them) a professional photo. Do NOT include religion or number of children unless the candidate has explicitly added them — these are accepted in some Gulf contexts but unwelcome at multinationals; defer to the candidate. For US/UK/EU target markets, suppress this entire block. Always treat "should I include personal info" as a function of the **target job's region**, not the candidate's region.

## What public sources document

The Bayt.com profile-builder requires, per Bayt's own "Your Bayt.com Profile Guide from Zero to 100" blog (retrieved via search excerpt; direct fetch returned HTTP 403): **full name as it appears on the passport, date of birth, gender, primary nationality, additional nationalities (if any), country of residence, visa status, marital status, number of dependents, and country where the driving licence was issued**. The same source notes a profile photo is not strictly mandatory but materially increases visibility ("more than four in five job seekers have a picture on their CV", per Bayt blog excerpt, accessed 2026-05-12).

Bayt's employer-side CV-search tool lets recruiters filter candidates **by nationality** as an explicit search facet, including "Include" and "Not in this list" exclusion (Bayt Support, accessed 2026-05-12). This confirms that nationality is not a cosmetic field but an indexed routing key on the employer side.

External Gulf-CV guidance sites converge on the same field list: Egyptian passport pages (per Wikipedia's Egyptian passport article, accessed 2026-05-12) carry Full Name, Date of Birth, Place of Birth, Nationality, Sex, National ID number, Profession, Military Status (males only), Husband's Name and Nationality (married females only), and Address — which is the natural source of the fields candidates copy onto their CVs.

I was unable to fetch wuzzuf.net directly (HTTP 403) to verify Wuzzuf's required-vs-optional field list. The Wuzzuf Help Center articles "Career Interests Section" and "Professional Info Section" exist but redirect to the gated `/careers` URL.

## Still needed from the author

- Wuzzuf's actual required-vs-optional field split in the live 2026 signup flow.
- Whether Egyptian local-employer recruiters ever verbally probe for date of birth or marital status when a CV omits them.
- The Egyptian convention on the National ID: is it ever placed on the CV itself, or always reserved for the onboarding folder?
- Gendered patterns the author has observed among female KSIU / Cairo-University / AUC grads on which fields they choose to omit.
- Multinational subsidiaries in Egypt (Vodafone Egypt, Microsoft Egypt, P&G, Unilever) — do they explicitly instruct candidates to drop demographic fields, and through what channel?

---
sources:
  - https://en.wikipedia.org/wiki/R%C3%A9sum%C3%A9  (accessed 2026-05-12)
  - https://en.wikipedia.org/wiki/Saudization  (accessed 2026-05-12)
  - https://www.bayt.com/en/blog/8648/your-bayt-com-profile-guide-from-zero-to-100/  (accessed 2026-05-12; direct fetch HTTP 403, content retrieved via search excerpt)
  - https://support.bayt.com/en/articles/6695857-filter-cvs-by-nationality  (accessed 2026-05-12)
  - https://en.wikipedia.org/wiki/Egyptian_passport  (accessed 2026-05-12)
