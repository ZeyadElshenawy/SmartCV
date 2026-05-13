---
id: mena_context_003_photo_norms
type: mena_context
title: Photograph Norms on MENA CVs
roles: [all]
seniority: [all]
industries: [all]
region: mena
weight: high
last_updated: 2026-05-12
---

# Photograph Norms on MENA CVs

A professional photograph is a routine, expected element of CVs in the Middle East and North Africa. Wikipedia's résumé article confirms the regional split: "many Middle East and African countries... require personal data (e.g., photograph, gender, marital status, children) while this is not accepted in the UK, U.S., and some European countries."

The expected photo follows passport-style conventions adapted for a slightly more candid, professional register:

- **Framing:** head-and-shoulders, centred, plain (usually light) background.
- **Attire:** business formal — suit jacket and shirt for men, modest professional dress for women. In Saudi Arabia, the UAE, and other Gulf states, traditional dress (thobe / abaya) is also common and signals local identity positively.
- **Expression:** neutral or slight smile; direct eye contact with the camera.
- **Resolution:** sharp, recent (within ~2 years), colour, no filters.
- **Placement:** top-right or top-left of the first page, sized roughly 2.5 cm × 3 cm (passport scale).

Gender-specific considerations exist in conservative markets. Female candidates targeting Saudi Arabia historically had to weigh whether including a photo helped or hurt the application; the country's accelerated workforce reforms under the Saudization (Nitaqat) framework — formally established by "Ministerial Resolution no. (4040)" in June 2011 — have shifted norms, but household-name conservative employers may still differ from multinational subsidiaries.

Photos are essentially incompatible with US-style ATS pipelines (Workday, Greenhouse) where the photo can trigger anti-bias filters that strip the image and disrupt OCR.

## Concrete rule for SmartCV

If the candidate has supplied a photo and the target job is in MENA, render it in the top-right corner of page 1, sized ~2.5 cm × 3 cm, with no decorative border. If the candidate has supplied a photo but the target job is in the US, UK, EU, or any role posted through a Workday/Greenhouse/Lever URL, omit the photo entirely. Do NOT auto-generate, alter, or filter a candidate-supplied photo. If no photo was supplied and the target is MENA, leave the slot empty rather than inserting a placeholder — silence is preferable to a generic avatar.

## What public sources document

Photo on the CV is described as **expected** for Saudi Arabia and "common practice" for Gulf-region CVs across multiple regional CV-advice publishers (visualcv.com, kudoswall.com, jobera.com, resume-example.com, accessed 2026-05-12 via search excerpts — direct fetches against visualcv.com returned HTTP 429). The cross-source consensus on the photo specification matches the file's headline rule:

- **Format:** professional headshot, neutral background, business attire (Saudi sources, accessed 2026-05-12).
- **Attire for men:** thobe and ghutra acceptable for Saudi-cultural-norm candidates; Western business attire (suit / collared shirt) equally appropriate.
- **Attire for women (Saudi-targeted):** professional photo with or without hijab is acceptable; hijab is "common but not universally required, especially for international companies" (jobera.com / resume-example.com, via search excerpt 2026-05-12).

Bayt's own profile-guidance content (per Bayt blog "Your Bayt.com Profile Guide from Zero to 100", retrieved via search excerpt 2026-05-12) explicitly says a profile picture is **not strictly mandatory** and that high profile-strength scores are achievable without one, but adds that more than four in five job seekers on the platform do include a picture. Bayt also publishes a separate blog post titled "Should You Put Your Photo on Your CV in the GCC?" — surfacing the question as one Bayt itself treats as live and debatable rather than settled.

I was unable to verify Wuzzuf's specific UI banner text around photo upload (HTTP 403 from this client).

## Still needed from the author

- The literal Wuzzuf-UI prompt around photo upload at signup, and whether the profile-strength score caps below 100% without a photo.
- Cairo recruiter behaviour on the phone — do they ever mention the photo verbally?
- KSIU (King Salman International University) and AUC career-services photo guidance — what advisors actually tell students.
- The dual-CV pattern (with-photo / no-photo) — how common is it among the author's classmates?
- Cairo studios specialising in CV photography — names, neighbourhoods, price ranges, and whether KSIU students share recommendations among themselves.
- Gendered patterns specifically: how do female grads at KSIU / Cairo / AUC actually decide whether to include the photo?

---
sources:
  - https://en.wikipedia.org/wiki/R%C3%A9sum%C3%A9  (accessed 2026-05-12)
  - https://en.wikipedia.org/wiki/Saudization  (accessed 2026-05-12)
  - https://www.bayt.com/en/blog/32214/should-you-put-your-photo-on-your-cv-in-the-gcc/  (accessed 2026-05-12; title only, direct fetch blocked)
  - https://www.bayt.com/en/blog/8648/your-bayt-com-profile-guide-from-zero-to-100/  (accessed 2026-05-12; via search excerpt, direct fetch HTTP 403)
  - https://www.visualcv.com/international/saudi-arabia-cv/  (accessed 2026-05-12; via search excerpt, direct fetch HTTP 429)
  - https://jobera.com/saudi-cv-writing-guide/  (accessed 2026-05-12; via search excerpt)
  - https://resume-example.com/cv/saudi-arabia-country  (accessed 2026-05-12; via search excerpt)
