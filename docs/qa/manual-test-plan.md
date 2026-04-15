# SmartCV — Manual Test Plan

End-to-end scenarios to walk through SmartCV in a browser. Work top-to-bottom on a fresh database for the cleanest run, or skip to the sections you want to verify. Check the box when a scenario passes.

## Pre-flight

- [ ] **P1.** Run `python manage.py migrate` — no migrations pending.
- [ ] **P2.** Run `npm run build:css` — `static/css/output.css` rebuilds without warnings.
- [ ] **P3.** Run `python manage.py test` — **204 tests pass**.
- [ ] **P4.** Run `python manage.py runserver` — server starts on `127.0.0.1:8000`; no console errors.
- [ ] **P5.** Open the site in a browser that supports Alpine.js (modern Chrome/Firefox/Edge). DevTools Network tab open so you can watch API calls.

---

## 1. Unauthenticated visitors

- [ ] **U1.** Visit `/` — landing page renders. No console errors. CTA "Sign up" / "Sign in" links visible.
- [ ] **U2.** Click "Sign up" — `/accounts/register/` loads with email + password form.
- [ ] **U3.** Click "Sign in" — `/accounts/login/` loads with email + password form.
- [ ] **U4.** Visit `/profiles/dashboard/` directly — redirects to login with `?next=/profiles/dashboard/`.
- [ ] **U5.** Visit `/agent/` directly — redirects to login.
- [ ] **U6.** Visit `/insights/` directly — redirects to login.
- [ ] **U7.** Visit `/applications/` directly — redirects to login.
- [ ] **U8.** Visit a non-existent URL like `/does-not-exist/` — the custom 404 page renders.

---

## 2. Registration & welcome

- [ ] **R1.** On `/accounts/register/`, submit a new email + password pair. On success, redirects to `/welcome/`.
- [ ] **R2.** Welcome page shows "Meet your agent" copy and 3 paths (Upload CV / Build by form / Skip tour).
- [ ] **R3.** Refresh the welcome page — second visit short-circuits to `/profiles/dashboard/` (the welcome flag persists).
- [ ] **R4.** Clicking "Skip tour" on welcome (before it auto-dismisses) lands on the dashboard.
- [ ] **R5.** Re-register attempt with the same email fails with a friendly error; no duplicate user created.
- [ ] **R6.** Weak password / password mismatch is rejected inline.
- [ ] **R7.** Log out (from dashboard top-right), log back in — lands on dashboard, NOT on welcome.

---

## 3. Profile creation — CV upload

- [ ] **P3-1.** From welcome (or `/profiles/setup/upload/`), upload a PDF CV. The loading message reads "Your agent will …" (not "We'll …").
- [ ] **P3-2.** LLM parses successfully; lands on `/profiles/setup/review/` with skills, experiences, education pre-filled.
- [ ] **P3-3.** Upload a DOCX CV — same flow succeeds.
- [ ] **P3-4.** Upload a corrupt / empty file — user-friendly error, not a 500.
- [ ] **P3-5.** Upload a huge file (>10 MB) — rejected cleanly.
- [ ] **P3-6.** On the review screen, edit a skill's name, add a new skill, reorder — Save persists the change (reload to verify).
- [ ] **P3-7.** Edit an experience description; line-breaks round-trip correctly after save.

## 4. Profile creation — manual form

- [ ] **P4-1.** From `/profiles/setup/review/` (blank profile), manually fill name, email, at least one experience, one education, five skills. Save → dashboard.
- [ ] **P4-2.** Add a summary ≥40 characters. Save → persists on reload.
- [ ] **P4-3.** Add a project with description; persists.
- [ ] **P4-4.** Add a certification; persists.

## 5. Profile — external signals

- [ ] **S1.** From `/insights/`, click **Connect GitHub**. Enter a real GitHub username → page refreshes with `public_repos`, `total_stars`, and language breakdown shown.
- [ ] **S2.** Connect GitHub with a bad username → error tile ("couldn't be found" or similar); no crash.
- [ ] **S3.** Connect Google Scholar (author ID) → citation count + h-index appear.
- [ ] **S4.** Connect Kaggle (username) → competition / dataset counts appear.
- [ ] **S5.** Add a LinkedIn URL on the profile edit page; appears on the profile.
- [ ] **S6.** Refresh a signal that already exists — `fetched_at` timestamp updates.

---

## 6. Job pipeline

- [ ] **J1.** Visit `/jobs/input/` — input form shows URL and manual paste tabs.
- [ ] **J2.** Paste a LinkedIn job URL → scraper fetches title/company/description → review screen renders.
- [ ] **J3.** Submit a job that LinkedIn blocks / URL fails → friendly error, not a 500.
- [ ] **J4.** Paste plain-text job description manually → LLM extracts skills → review screen renders with `extracted_skills` list.
- [ ] **J5.** After a job is saved it appears on `/applications/` kanban under **Saved**.
- [ ] **J6.** Drag (or click-to-move, whichever the UI uses) a job from Saved → Applied → Interviewing → Offer. Stage badges update.
- [ ] **J7.** Delete a job — confirmation prompt, then it disappears from kanban.
- [ ] **J8.** Refresh `/applications/` — the `total_applications` count matches visually.

---

## 7. Gap analysis

- [ ] **G1.** From a saved job, click "Run gap analysis" (or navigate to `/analysis/gap/<job-id>/`). LLM returns matched / partial / missing skill lists.
- [ ] **G2.** Every skill in `job.extracted_skills` appears in exactly one bucket (matched/partial/missing). None are dropped.
- [ ] **G3.** Running gap analysis again uses the cached result instantly (no second LLM call).
- [ ] **G4.** On a profile with GitHub signals, the evidence-confidence indicator on the gap analysis page reflects signal-backed skills.

## 8. Learning path

- [ ] **L1.** From gap analysis page, click "Build learning path" → `/analysis/learning-path/<job-id>/` loads with step-by-step modules.
- [ ] **L2.** Header reads "Your agent · learning path" (not "AI coach").
- [ ] **L3.** Loading copy uses agent voice ("Your agent is mapping…").
- [ ] **L4.** Empty / zero-missing case still renders gracefully.

## 9. Salary negotiation

- [ ] **N1.** On a job in **Offer** status, visit `/analysis/salary/<job-id>/`. LLM returns a negotiation script anchored in profile strengths.
- [ ] **N2.** Header copy uses agent voice.

---

## 10. Resume generation

- [ ] **R-1.** From a job with gap analysis complete, click "Generate tailored résumé" → `/resumes/generate/<job-id>/`. LLM produces a résumé.
- [ ] **R-2.** Preview page renders cleanly — header, experiences, skills, education.
- [ ] **R-3.** Edit a bullet in an experience description — reload the page — change persists.
- [ ] **R-4.** Bracket characters (`[`, `]`) in bullets don't corrupt the textarea contents after Save (this was a previous bug class).
- [ ] **R-5.** Export to PDF via `xhtml2pdf` — downloads a PDF with the tailored content.
- [ ] **R-6.** ATS score displays on the preview; regenerating updates the score.
- [ ] **R-7.** Keyword-stuffing warnings appear in the console log for profiles with repeated terms (check dev server output, not browser).

## 11. Cover letter

- [ ] **CL-1.** From a job page, click "Generate cover letter" → LLM produces a letter grounded in profile + job.
- [ ] **CL-2.** Edit the letter, save, reload — edits persist.
- [ ] **CL-3.** Agent-voice copy on the generation screen.

## 12. Outreach

- [ ] **O1.** On a job page, click "Generate outreach" → `/profiles/outreach/<job-id>/`. Returns LinkedIn + email templates.
- [ ] **O2.** Agent-voice loading copy.

---

## 13. Dashboard — career stage hero

Pipe a few jobs through different stages before running these.

- [ ] **D1.** Brand-new user (no profile) → stage **Getting started**. Primary CTA: "Upload your CV".
- [ ] **D2.** Profile filled, no jobs → stage **Ready to look**. Primary: "Show your agent a job". Secondary chips: evidence, edit profile.
- [ ] **D3.** Add only **Saved** jobs → stage **Actively applying**. Primary: "Add a new job". Secondary chips include Cover letter + Outreach for the most recent saved job.
- [ ] **D4.** Move a job to **Interviewing** → stage **In interviews**. Primary label says "Prep for {company}". Secondary chips: "Review the gap analysis", **"Ask agent about this role"**, "See pipeline".
- [ ] **D5.** Move a job to **Offer** → stage **Offer in hand**. Primary: "Negotiate {company}". Secondary chips: Open negotiator, Write thank-you, Review other offers.
- [ ] **D6.** With only rejected jobs → stage **Regrouping**. Primary: "Build a learning path".
- [ ] **D7.** Primary CTA button is clickable; secondary chips are clickable and land on the correct deep-linked URL.

## 14. Dashboard — profile strength ring

- [ ] **PS1.** Profile strength ring visible in the sidebar showing a number + tier (Weak/Developing/Solid/Strong).
- [ ] **PS2.** A new empty profile shows **Weak**; ring is mostly empty.
- [ ] **PS3.** After filling ≥5 skills + name + email, the score increases and tier may advance to **Developing**.
- [ ] **PS4.** Nudge text under the tier shows the top action ("Connect GitHub · +14 points" or similar).
- [ ] **PS5.** Clicking the ring navigates to `/insights/#profile-strength` and scrolls to the breakdown section.

## 15. Insights hub

- [ ] **I1.** Visit `/insights/`. Near the top: **Profile strength** card with big score + tier badge + three component bars (Completeness / Evidence depth / External signals).
- [ ] **I2.** Click "See details" — per-component item list expands. Met items are line-through-green-dotted; unmet items show a `+N` badge.
- [ ] **I3.** Top-3 CTA chips are visible under the component bars. Clicking one navigates to its target (`/profiles/setup/review/` or `/insights/`).
- [ ] **I4.** Evidence-confidence tile still renders below (or next to) the strength card — they coexist.
- [ ] **I5.** Top skills across applications list populated from job pipeline.
- [ ] **I6.** Recent gap analyses list + recent tailored résumés list both populated when data exists.
- [ ] **I7.** All three external signal tiles (GitHub, Scholar, Kaggle) show either "connected + data" or a "Connect" CTA.
- [ ] **I8.** A stale signal (`fetched_at` > 90 days old) surfaces a refresh nudge or is caught by the "Refresh your external signals" action in the strength card.

---

## 16. Agent chat — global

- [ ] **AG1.** Top nav has "Ask agent" link (both desktop and mobile menu).
- [ ] **AG2.** `/agent/` loads; scope pill **absent** (this is general chat). Four general seed prompts visible.
- [ ] **AG3.** Click a seed → message sends, typing indicator animates, reply arrives. DevTools shows `POST /agent/api/` with `job_id: null`.
- [ ] **AG4.** Type a custom message + press Enter (or click send) — same behavior.
- [ ] **AG5.** "New chat" button clears the transcript.
- [ ] **AG6.** Trigger an LLM error (temporarily break `GROQ_API_KEY` or disconnect internet); an error bubble appears — no crash.

## 17. Agent chat — job-aware

Prerequisite: at least one job exists in your pipeline, ideally one in Interviewing status so it has a gap analysis.

- [ ] **AJ1.** Visit `/agent/?job=<valid-owned-job-id>`. Scope pill visible: "Talking about: {company} · {title}". Four job-scoped seed prompts visible (prep / gap / negotiate / projects).
- [ ] **AJ2.** DevTools: the POST body includes `job_id: "<uuid>"`.
- [ ] **AJ3.** Agent reply references the job's actual company/title/skills.
- [ ] **AJ4.** Click the `×` in the scope pill → returns to general `/agent/`; pill gone.
- [ ] **AJ5.** Visit `/agent/?job=totally-bogus-uuid` → redirects to `/agent/` with a "That job couldn't be found." message.
- [ ] **AJ6.** In another logged-in account (or via impersonation), try `/agent/?job=<other-user's-job-id>` — redirects same as AJ5 (no info leak).
- [ ] **AJ7.** From the dashboard interviewing-stage chip "Ask agent about this role" — click — lands on `/agent/?job=<id>` pre-scoped.
- [ ] **AJ8.** When gap analysis exists for the job, ask "What's my biggest gap on this role?" — reply mentions specific missing skills from the gap.

## 18. Per-job chatbot (narrow, legacy)

- [ ] **C1.** Visit `/profiles/chatbot/<job-id>/`. Different from `/agent/?job=` — this is the mock-interview prep chatbot.
- [ ] **C2.** Conversation persists until you hit "Complete" or cancel.
- [ ] **C3.** Completing the chatbot offers "save to this job only" vs "save to master profile"; both behave correctly.

---

## 19. Edge cases

- [ ] **E1.** User with no UserProfile row: dashboard doesn't crash — `get_or_create` handles it.
- [ ] **E2.** User with `data_content={}`: profile strength returns score=0, tier=Weak, 3 components, top_actions populated with highest-point gaps.
- [ ] **E3.** Job with `extracted_skills=[]`: gap analysis page still loads; shows "no skills extracted" state.
- [ ] **E4.** Delete a job while agent chat is open scoped to it → next message returns 403 with an error bubble; user navigates back to `/agent/` manually.
- [ ] **E5.** Two browser tabs: upload CV in tab A, drag a job in tab B — both complete without interfering.
- [ ] **E6.** Database connection hiccup (simulate via `sqlite` break or Supabase pause): pages surface user-friendly errors, not raw 500 stack traces in prod.

## 20. Accessibility & mobile

- [ ] **A1.** Dashboard on a 375×812 viewport (iPhone-ish) — layout reflows; hero CTA remains tappable.
- [ ] **A2.** Agent chat scope pill hidden on `<sm` breakpoints (intentional); general chat is usable.
- [ ] **A3.** Tab through the dashboard with keyboard — focus rings visible on buttons and links.
- [ ] **A4.** Dark mode toggle (if present): all pages remain legible; no white flashes on navigation.
- [ ] **A5.** Screen-reader: the × dismiss on the agent scope pill announces "Back to general chat".

## 21. Security smoke tests

- [ ] **SEC1.** CSRF: a POST to `/agent/api/` without the CSRF token returns 403.
- [ ] **SEC2.** Ownership: a GET on `/analysis/gap/<other-user's-job-id>/` returns 404 or 403, not the data.
- [ ] **SEC3.** SQL-ish inputs in a manual job paste (e.g., `"; DROP TABLE jobs;--"`) are stored safely; no error.
- [ ] **SEC4.** Script tags in profile fields render as escaped text, not as executed JS.

---

## Smoke-test sign-off

When every scenario above is checked (or knowingly skipped with a note), the build is green for a release/review cycle.

**Tested by:** _______________________
**Build / commit SHA:** _______________________
**Date:** _______________________
**Notes:**
