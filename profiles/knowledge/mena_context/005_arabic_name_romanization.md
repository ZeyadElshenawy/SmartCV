---
id: mena_context_005_arabic_name_romanization
type: mena_context
title: Arabic-Name Romanization on CVs
roles: [all]
seniority: [all]
industries: [all]
region: mena
weight: high
last_updated: 2026-05-12
---

# Arabic-Name Romanization on CVs

A single Arabic given name produces multiple valid Latin-script spellings, which has direct ATS and recruiter-search consequences. Wikipedia's romanisation overview explains the root cause: "written Arabic is normally unvocalized; i.e., many of the vowels are not written out." Different transliteration systems (BGN/PCGN 1956, ALA-LC 1991, ISO 233, DIN 31635) make different vowel and consonant choices, which is why **Muhammad / Mohammed / Mohamed / Mohamad** all encode the same Arabic name (محمد). Wikipedia's chart of "the changing English romanization of the Arabic short vowels between the 19th and 20th centuries" uses Muhammad as the canonical example.

Arabic name structure (per Wikipedia's Arabic Name article) layers up to five components: **ism** (personal name), **nasab** (patronymic, with "ibn" / "bint" — though "ibn and bint were omitted 'in almost all Arab countries'" in modern practice), **laqab** (epithet/family), **nisbah** (origin/profession, e.g., "al-Halabi" denotes someone "originally from Aleppo"), and **kunya** (teknonym with Abu / Umm). In modern Egypt and the Gulf, names simplify on legal documents to: **personal name + father's name + grandfather's name + family name** (typically four parts on a passport).

ATS and recruiter-search implications:

- The same candidate appearing as "Mohamed Ahmed" in one system and "Mohammed Ahmad" in another will not deduplicate.
- Boolean searches by recruiters often miss spelling variants. Including a parenthesised alternate ("Mohamed (Mohammed) Ahmed") increases recall.
- Passports lock in the official spelling for visa, payroll, and background-check purposes; the CV should match the passport exactly.

## Concrete rule for SmartCV

For Arabic-named candidates, always render the name on the CV exactly as it appears on the candidate's passport (the candidate must enter this verbatim). Do NOT silently re-transliterate. If the candidate's stored full legal name uses a different spelling than their LinkedIn / GitHub handle, generate the CV with the passport spelling but include a single parenthetical alternate next to the name on first appearance only — for example, "Mohamed (Mohammed) Ahmed Hassan". For the Education and Experience sections, preserve whatever spelling the original institution or employer used. Never invent a third spelling not seen in the candidate's data.

## What public sources document

The Wikipedia article on the Egyptian passport (accessed 2026-05-12) confirms the passport information page carries a **single "Full Name"** field plus separate Date of Birth, Place of Birth, Nationality, Sex, National ID number (in Arabic), Profession, Husband's Name and Nationality (married females only), Military Status (males only), and Address (in Arabic). The article does **not** specify how the multi-part Egyptian name (personal / father / grandfather / family) is laid out within "Full Name", and I was unable to find an authoritative public source for the 3-vs-4-part convention on the printed page.

FamilySearch's *Egypt Naming Customs* (accessed 2026-05-12) documents the broader Arabic-naming pattern: a personal name followed by paternal lineage using "ibn" / "bin" (or "bint" for daughters), and family names placed at the end, often prefixed with "AL-" or "EL-" (e.g., "AL-TIKRITI", derived from a city of origin). The example "Saleh ibn Tariq ibn Khalid al-Fulan" translates as "Saleh, son of Tariq, son of Khalid; of the family of al-Fulan." The article also notes "Muhammad" is commonly abbreviated as "Md.", "Mohd.", "Muhd.", or "M." in informal records.

The Keesing platform's article on transliteration of Arabic names in machine-readable travel documents (MRTDs, accessed 2026-05-12 via search excerpt) and academic work on Saudi-Arabia romanisation (Al-Ghamdi, *SAEANT*, accessed 2026-05-12) confirm that **no single Latin-script standard governs Arabic-name romanisation**, and that the same Arabic name routinely produces multiple valid English spellings — explicitly listed examples: Mohammad / Muhammad / Muhammed / Mohamed / Mohamad, and Youssef / Yusuf / Yousef.

Egyptian passports since 2008 are machine-readable (ICAO 9303, 96.7% conformance), and ICAO uses a fixed transliteration that may differ from the candidate's preferred Latin spelling (Wikipedia, Egyptian passport, accessed 2026-05-12).

I could not find a public source documenting whether Wuzzuf or Bayt deduplicate across Latin spelling variants of the same Arabic name.

## Still needed from the author

- Concrete Egyptian-passport practice: does the printed page show 3 name parts or 4? Where does the family name sit?
- Wuzzuf / Bayt actual behaviour on near-duplicate profiles with spelling variants — does the system merge, flag, or treat as separate?
- Whether Cairo and Riyadh recruiters Boolean-OR spelling variants in their searches, or expect exact match.
- KSIU and AUC student conventions on LinkedIn vs passport spelling — short Westernised form or full legal form?
- "Abdel-" / "El-" / "Al-" / "Abu-" — how are these handled in practice (hyphen, space, collapsed)?
- Where the Arabic-script version of the name appears on bilingual CVs in the author's experience.

---
sources:
  - https://en.wikipedia.org/wiki/Arabic_name  (accessed 2026-05-12)
  - https://en.wikipedia.org/wiki/Romanization_of_Arabic  (accessed 2026-05-12)
  - https://en.wikipedia.org/wiki/Egyptian_passport  (accessed 2026-05-12)
  - https://www.familysearch.org/en/wiki/Egypt_Naming_Customs  (accessed 2026-05-12)
  - https://platform.keesingtechnologies.com/transliteration-of-arabic-in-mrtds/  (accessed 2026-05-12; via search excerpt)
  - https://mghamdi.me/SAEANT.pdf  (Al-Ghamdi, Saudi Arabia romanization experience; accessed 2026-05-12; via search excerpt)
  - https://primer.ai/blog/solving-arabic-name-transliteration  (accessed 2026-05-12; via search excerpt)
