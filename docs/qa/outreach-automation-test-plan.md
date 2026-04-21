# SmartCV — Outreach Automation Manual Test Plan

End-to-end scenarios for the **browser-extension hybrid outreach automation** feature
(spec: `docs/superpowers/specs/2026-04-21-outreach-automation-design.md`,
implementation: commit `43ae31b`).

> ⚠️ **Use a throwaway LinkedIn account** for the live-execution tests in
> sections 8 and 9. LinkedIn may rate-limit, restrict, or ban accounts that
> automate the connect flow. Do NOT run the live tests with your primary
> LinkedIn account on the first pass.

---

## Pre-flight

- [ ] **OP1.** `python manage.py migrate` — `accounts.0002_user_outreach_token` and
  `profiles.0014_outreachcampaign_outreachaction` are applied.
- [ ] **OP2.** `python manage.py test profiles.tests_outreach` — **15 tests pass**.
- [ ] **OP3.** `python manage.py runserver` — server starts on `127.0.0.1:8000`; no startup errors.
- [ ] **OP4.** A logged-in test user exists with a `UserProfile` that has at least
  `full_name`, 5 skills, and a non-empty `summary`. (Required by the per-target draft prompt.)
- [ ] **OP5.** At least one `Job` exists for the test user, with a `company` set
  (e.g. "Stripe") and an optional public LinkedIn job `url`.
- [ ] **OP6.** Chrome/Edge installed with Developer mode enabled in `chrome://extensions`.
- [ ] **OP7.** Throwaway LinkedIn account logged in in the same browser profile.

---

## Test Scenario (E2E)

> **Goal:** Take one job from "no campaign" to "first invite sent on LinkedIn"
> using the full SmartCV → extension → LinkedIn loop. Each section below
> is one slice of the loop and can be run in isolation; running them in
> order gives you the full E2E walkthrough.

---

## 1. Pairing & token

- [ ] **OT1.1.** As the logged-in test user, visit `/profiles/extension/pair/`.
  Page renders the pairing UI; a **Your token** field shows a UUID.
- [ ] **OT1.2.** First-time visit (no prior token) auto-generates one — no "regenerated"
  banner shown.
- [ ] **OT1.3.** Click **Copy** — the token is copied to clipboard.
- [ ] **OT1.4.** Click **Regenerate token (revokes old one)** — page reloads, the
  green "Token regenerated" banner appears, and the displayed token UUID is different
  from the previous one.
- [ ] **OT1.5.** Verify in the Django shell: `User.objects.get(...).outreach_token`
  matches the on-page UUID.
- [ ] **OT1.6.** Logged-out access to `/profiles/extension/pair/` redirects to the login page.

## 2. Extension install + options

- [ ] **OT2.1.** In `chrome://extensions`, **Load unpacked** → select
  `extension-outreach/` → "SmartCV Outreach 0.1.0" appears with no warnings.
- [ ] **OT2.2.** Click the extension icon → popup shows red dot + "Not paired — open Options."
- [ ] **OT2.3.** Click **Options** → page renders with Host (defaults to
  `http://127.0.0.1:8000`) and Token fields.
- [ ] **OT2.4.** Paste the token from OT1.4, click **Save** — green "Saved." appears.
- [ ] **OT2.5.** Reopen the popup — green dot + "Paired with `http://127.0.0.1:8000`."

## 3. Discovery (server-side)

- [ ] **OT3.1.** Visit `/profiles/outreach/<job_id>/campaign/` for the test job —
  page renders breadcrumb, header, and the amber "Heads up" disclosure box.
- [ ] **OT3.2.** "1 · Targets" section shows the empty-state hint:
  *"No targets yet. Hit Find people + draft above…"* with a fallback Google search link.
- [ ] **OT3.3.** Click **Find people + draft** — full-page loading message appears
  ("Discovering people and drafting messages — 30–60s.").
- [ ] **OT3.4.** Page reloads with up to 10 unique targets in the table (rows have
  Name, Role, Source = `hiring_team` or `google`, "Open ↗" link to LinkedIn).
- [ ] **OT3.5.** When LinkedIn job page has no public hiring team **and** Google
  blocks the SERP, the table is empty and the user can fall back to the
  manual-search link without a 500.
- [ ] **OT3.6.** No duplicate handles appear across hiring-team and Google sources.

## 4. Per-target drafting

- [ ] **OT4.1.** After OT3.4, the "2 · Drafts" section is visible.
- [ ] **OT4.2.** Each selected target has an editable textarea pre-filled with a
  ≤300-char LinkedIn connect message that addresses the target by name and
  references their role.
- [ ] **OT4.3.** The character counter shows current length; turns amber when
  >280 chars.
- [ ] **OT4.4.** Anti-hallucination spot-check: pick one draft and confirm it does
  not invent skills, employers, metrics, or facts not in the user's profile.
- [ ] **OT4.5.** Edit a draft to be longer than 300 chars — the textarea
  `maxlength` clamps input at 300.

## 5. Campaign creation

- [ ] **OT5.1.** Uncheck a few targets in section 1 — the "Selected" counter and
  drafts list update reactively.
- [ ] **OT5.2.** Set **Daily invite cap** to 5; click **Start campaign** — button
  shows "Starting…" briefly, then the live status panel appears.
- [ ] **OT5.3.** Verify in the Django admin (or `OutreachCampaign.objects.last()`):
  one new `OutreachCampaign(status='running', daily_invite_cap=5)` and N
  `OutreachAction(status='queued', kind='connect')` rows.
- [ ] **OT5.4.** All `OutreachAction.payload` values are ≤300 chars.
- [ ] **OT5.5.** Clicking **Start campaign** with cap=0 or cap=26 returns a
  client-side or server-side rejection (no campaign created).
- [ ] **OT5.6.** Submitting with no targets selected — the **Start campaign** button
  is disabled.

## 6. Extension polling (offline LinkedIn — server-only)

> Goal: verify the extension/server contract without touching LinkedIn.

- [ ] **OT6.1.** Close any LinkedIn tabs. Click **Poll now** in the extension popup.
- [ ] **OT6.2.** With a queued action present, server logs show
  `GET /profiles/api/outreach/next 200`. The action's `status` flips to `in_flight`
  and `attempts` increments to 1.
- [ ] **OT6.3.** Without a queued action (or campaign paused), server logs show
  `GET /profiles/api/outreach/next 204`.
- [ ] **OT6.4.** Manually rotate the token (OT1.4) — within the next poll, the
  extension's request returns `401 Unauthorized` and the popup popup eventually
  reflects the breakage on next paired-status fetch.

## 7. Result reporting (manual via curl)

> Goal: verify status transitions without LinkedIn.

- [ ] **OT7.1.** Pick an `OutreachAction` in `in_flight` state (from OT6.2). Send:
  ```bash
  curl -X POST http://127.0.0.1:8000/profiles/api/outreach/result/<action_id>/ \
       -H "Authorization: Token <your-token>" \
       -H "Content-Type: application/json" \
       -d '{"status": "sent"}'
  ```
  Response: `{"ok": true, "status": "sent"}`. The action's `completed_at`
  is set; status panel in the browser updates within 5 s.
- [ ] **OT7.2.** Same flow with `{"status": "skipped", "error": "already_connected"}`
  — action transitions to `skipped` with `last_error` populated.
- [ ] **OT7.3.** Same flow with `{"status": "failed", "error": "not_found"}` on an
  action with `attempts < 3` — status flips to `failed` but `completed_at` stays null
  (so it can be retried).
- [ ] **OT7.4.** When ALL actions in a campaign reach a terminal state and at
  least one was `sent`/`accepted`, the campaign auto-flips to `done`. If all
  failed, it flips to `failed`.
- [ ] **OT7.5.** Posting `{"status": "weird"}` returns `400` and does not change
  the action.
- [ ] **OT7.6.** Posting with another user's token returns `404` (action lookup
  scoped to `campaign__user`).

## 8. Live LinkedIn execution (throwaway account!)

> ⚠️ Use a throwaway LinkedIn account. Have one queued action targeting a
> public LinkedIn profile you don't mind sending an invite to (e.g. a
> coworker who's expecting it).

- [ ] **OT8.1.** With one `queued` action and the throwaway LinkedIn tab logged
  in, click **Poll now** → background.js opens/refocuses the LinkedIn tab on
  `linkedin.com/in/<handle>/`.
- [ ] **OT8.2.** Within ~30 s, the **Connect** button is clicked, the modal
  opens, **Add a note** is clicked, the message is typed character-by-character
  with visible jitter, and **Send** is clicked.
- [ ] **OT8.3.** Server-side, the action transitions `in_flight → sent` and
  `completed_at` is populated.
- [ ] **OT8.4.** Status panel in SmartCV shows the green dot for that action
  within 5 s of completion.
- [ ] **OT8.5.** No action is sent twice — confirm by checking
  `OutreachAction.objects.filter(target_handle=..., status='sent').count() == 1`.

## 9. Edge cases (live)

- [ ] **OT9.1. Already connected.** Queue an action targeting someone you're
  already connected to → action transitions to `skipped` with
  `last_error='already_connected'`. No invite sent.
- [ ] **OT9.2. Profile not found.** Queue an action with
  `target_handle='this-handle-does-not-exist-xyz'` → action transitions to
  `failed` with `last_error='not_found'`.
- [ ] **OT9.3. Selector drift simulation.** In Chrome DevTools on the LinkedIn
  profile page, run `document.querySelectorAll('button[aria-label]').forEach(b => b.setAttribute('aria-label', 'XXX'))` then trigger **Poll now** → action
  fails with `last_error='selector_drift'`. (Reload the page to undo.)
- [ ] **OT9.4. Weekly-cap simulation.** Open DevTools → Application → Storage,
  inject a fake LinkedIn cap modal:
  `document.body.innerText += "You're close to the weekly invitation limit"`
  then trigger **Poll now** → action fails with `weekly_cap` AND
  `chrome.storage.local.smartcv_hard_paused_until` is set ~24h in the future.
  Subsequent **Poll now** clicks do not call the LinkedIn tab until the time
  passes.

## 10. Pause / resume / cap

- [ ] **OT10.1.** With a campaign running and ≥2 queued actions, click **Pause**
  in the status panel → campaign status flips to `paused`. Subsequent
  `/api/outreach/next` calls return 204.
- [ ] **OT10.2.** Click **Resume** → status flips to `running`. Next poll resumes
  dispatch.
- [ ] **OT10.3. Cap enforcement.** With `daily_invite_cap=2` and 2 already-sent
  actions in the last 24 h, polling returns 204 until 24 h has passed
  (verify with a SQL `UPDATE outreach_actions SET completed_at = ...` to
  fast-forward instead of waiting).
- [ ] **OT10.4.** "Sent today" counter on the status panel reflects the live
  count.

## 11. Token revocation

- [ ] **OT11.1.** Note the current token. Hit `/profiles/extension/pair/` and
  click **Regenerate** → server returns a new token; the old one is now invalid.
- [ ] **OT11.2.** Without updating the extension's token, click **Poll now** →
  popup eventually shows the request fails (401 in network tab). Server logs
  show 401.
- [ ] **OT11.3.** Update the extension's options page with the new token →
  polling resumes.

## 12. Concurrency

- [ ] **OT12.1.** With a single queued action, hit `/api/outreach/next` twice in
  quick succession (two `curl` calls in parallel). The first returns the action
  payload; the second returns 204 (action was already claimed and is now `in_flight`).
- [ ] **OT12.2.** Create two running campaigns for the same user. Polling
  should pull actions from whichever campaign has the oldest `queued_at`,
  respecting the combined daily cap.

## 13. Cleanup

- [ ] **OT13.1.** Delete test campaigns from Django admin → cascading delete
  removes all associated `OutreachAction` rows.
- [ ] **OT13.2.** Reset the throwaway LinkedIn account's pending invitations from
  `linkedin.com/mynetwork/invitation-manager/sent/` if you want to re-test from
  zero.

---

## Acceptance criteria summary

A passing test run requires:

1. All sections 1–7 pass with ≤2 minutes of operator time.
2. Section 8 lands at least one real LinkedIn invite from a throwaway account.
3. Sections 9–12 cover the failure modes and edge cases enumerated in
   `docs/superpowers/specs/2026-04-21-outreach-automation-design.md` § Risks.
4. No unexpected 500s in the server log throughout.
5. No silent failures in the extension service worker (check the extension's
   service-worker DevTools for stack traces after each section).
