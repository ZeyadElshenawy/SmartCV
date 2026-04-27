# Outreach automation — v2 design spec

> **Status:** draft (2026-04-27). Written immediately after the Tier 1–3
> robustness pass on v1. Not yet implemented; this document defines what
> v2 would do, why, and where the seams already exist for it.

## Where v1 stops

v1 ships a one-shot loop:

1. User builds a campaign for one job → server queues
   `OutreachAction(kind='connect')` rows, one per target.
2. The Chrome extension drains the queue, sending LinkedIn connect-with-note
   from the user's tab.
3. The extension reports `sent` / `failed` / `skipped`. Every action
   terminates after one network round-trip.
4. There's no post-send action: no "did they accept?" check, no DM after
   accept, no follow-up sequence, no second touch on no-response.

The README explicitly scopes this out (`extension-outreach/README.md` —
"Only handles `kind=connect`. Direct messages and follow-up after-accept
are out of v1 scope"). v2 closes that gap.

## What v2 does

**A connect-with-note is the start of a thread, not the end.** Once an
invite is accepted, the candidate has 1 second of attention and a couple of
weeks of soft signal. v2 turns that into a sequence:

1. **`connect`** (existing) — connect-with-note to a discovered target.
2. **`message_after_accept`** — once the invite is accepted, send a thank-you
   DM that doesn't pitch yet. Hand-tailored from the source CV + JD.
3. **`message_followup_1`** — N days later, if no reply, a soft follow-up
   that names a concrete reason (e.g., "I noticed you wrote about X
   recently — that's exactly the problem I'm trying to solve in role Y").
4. **`message_followup_2`** — final touch at 2× N days. Polite close with
   one specific ask ("If you're open to a 15-min chat, here's a Calendly
   link"). After this, the action thread is `closed`.

Each step in the sequence is a separate `OutreachAction` row, linked back to
the original `connect` action via a new `parent_action` FK. This lets the
status panel show the thread together, and lets retries / pauses operate at
the thread level without losing per-step granularity.

## Why this shape

**Sequences, not scheduled jobs.** The naive approach is "schedule a
follow-up via Celery / django-q". That requires a scheduler that runs even
when the user's browser is closed, which v1 deliberately doesn't have
(synchronous everywhere, extension is the only "executor"). v2 keeps that
discipline: each follow-up is *queued* but only *runs* when the extension
polls and the dispatcher decides it's eligible. The dispatcher gates on
elapsed-since-parent-action, not wall-clock cron.

**Accept detection happens via discovery.** LinkedIn doesn't have a public
"connection accepted" webhook. v2 detects acceptance the same way v1
detects it ("isAlreadyConnected"): the next time the extension visits any
target's profile, the content script reports back whether the relationship
state has changed. We piggyback on the existing discovery polling rather
than building a separate "check accepts" pipeline.

**One thread per (campaign, target).** No multi-recipient threads, no
group messages — LinkedIn doesn't make those clean to automate, and the
value is in the personalized 1:1.

## Data model deltas

```
OutreachAction (existing, modified)
  + kind: add 'message_after_accept', 'message_followup_1', 'message_followup_2'
  + parent_action: ForeignKey('self', on_delete=CASCADE, related_name='followups', null=True)
                   Identifies the connect action this DM belongs to.
                   Null for the original connect (root of the thread).
  + scheduled_after: DateTimeField(null=True)
                     Earliest time the dispatcher will hand this to the
                     extension. Used to enforce the "wait N days after
                     accept / no-reply" delay without a cron.
  + accepted_at: DateTimeField(null=True)
                 When the connection was detected as accepted. Set by the
                 extension's accept-detection probe. Drives scheduled_after
                 for the next step in the thread.

OutreachActionEvent (existing)
  + actor: add 'extension_probe' for accept-detection events
                 (different from 'extension' which means "executed an action")
  + No schema change — just new actor value
```

The kind whitelist on `OutreachAction.KIND_CHOICES` extends. The extension's
content script needs a new branch per kind that knows how to drive the DM
modal (different LinkedIn flow than connect-with-note).

## Dispatcher logic

`claim_next_action(user)` extends with one filter pass:

1. Existing: `status='queued'` actions with no parent.
2. **New:** thread follow-ups (`parent_action IS NOT NULL`) where
   `scheduled_after <= now` AND the parent's `accepted_at IS NOT NULL`
   for `message_after_accept`, OR the previous step in the thread reached
   `sent` AND no reply in N days for `message_followup_*`.

The "N days" gate is a server-side `scheduled_after` timestamp set by the
event hook on the parent's transition — no scheduler needed, just a
`<= now` comparison on the next claim.

```
Connect sent           → accept-detection probe queued (next visit to target)
                       → if accepted: message_after_accept queued, scheduled_after = now
                       → if not accepted in 14d: thread closed (timeout)

Accept detected        → message_after_accept fires (next claim)
                       → on sent: message_followup_1 queued,
                          scheduled_after = now + 7d
                       → on reply detected: thread closed (success)

message_followup_1 sent → message_followup_2 queued,
                           scheduled_after = now + 14d (cumulative)
                        → on reply: thread closed
                        → on no reply at 14d: message_followup_2 fires
                        → after that: thread closed regardless
```

## Accept-detection probe

A new lightweight content script (`content_probe.js`) injected on the
target's profile during *any* tab visit. It reports back:

```js
{ kind: 'probe', target_handle, relationship_state: 'connected' | 'pending' | 'not_connected', detected_at }
```

Server hook: when `relationship_state` flips from `pending` (the implicit
state after our connect) to `connected`, set the parent action's
`accepted_at` and queue the `message_after_accept` row.

The probe runs *opportunistically* — not on a schedule. Whenever the user
already has a LinkedIn tab visiting *any* profile, the probe checks if it
matches a target with a pending invite. No dedicated "scan all targets" job
needed.

## UI deltas

**Campaign status panel** (existing):
- Per-action rows expand to show the thread (connect → DM → follow-ups)
  with a vertical rail and per-step status pill.
- Cached `summary_stats` adds keys: `accepted`, `replied`, `closed`.
- Each step links to the LinkedIn DM thread (deep-link via the connection
  URL — even if the message doesn't open, the recipient page does).

**Drafts editor** (new):
- When creating a campaign, the UI generates 4 drafts per target (connect
  + 3 DMs) so the user can review them all upfront. The follow-ups are
  drafted using the same `outreach_generator` logic, but with the
  preceding step's payload as additional context so each message builds
  on the prior one rather than restating it.
- Per-target "Edit thread" pencil icon opens all 4 drafts side-by-side
  for review/regeneration.

**Pairing UI** (existing): unchanged. The token is the same.

## Anti-abuse considerations

- **Per-thread cap.** Already enforced at the LinkedIn level (sending too
  many DMs trips spam detection). v2 should hard-cap follow-ups to 2
  (configurable down to 0 — some users may want connect-only). Default 2.
- **Per-day DM cap.** Separate from the connect daily-cap. Default 30/day
  (LinkedIn's soft DM cap is around 100 but lower is safer).
- **Reply detection breaks the thread.** If we detect any reply (extension
  probe sees a new message in the thread or a new connection visible from
  the target's side), we mark the thread `closed` with reason `replied`
  and don't send any more touches. Critical: a "thanks" reply ends the
  follow-up sequence. We're not a chatbot.
- **Manual override.** The user can mark any thread "skip" at any step via
  the campaign panel. No way to mass-skip currently — explicitly out of v2.

## Migration plan

This is non-trivially a migration. We can't just add new `kind` values and
ship — existing campaigns shouldn't suddenly grow follow-up actions
retroactively. Plan:

1. **Migration 0018**: add fields (`parent_action`, `scheduled_after`,
   `accepted_at`) — nullable, no defaults that affect existing rows.
2. **Migration 0019** (data): leave existing rows alone. v1 connect actions
   without follow-ups remain `parent_action=NULL` and don't trigger any
   threading logic.
3. **Settings flag**: `OUTREACH_V2_ENABLED = False` until rollout. v2 logic
   gated on this; v1 path runs unchanged when off.
4. **Per-campaign opt-in** (initial rollout): user explicitly toggles
   "include follow-up sequence" on campaign creation. Defaults off until
   we have enough live data to make it default-on.
5. **Extension version bump** (0.4.0): new content script for DMs +
   accept-detection probe. The old extension keeps working with v1
   campaigns; only campaigns marked `v2_enabled` get DM actions queued.

## What v2 explicitly does NOT do

- **No reply parsing.** "They replied" is a state, not a content
  classification. We don't try to read what they said and decide the next
  step. If you want a chatbot, this isn't it.
- **No template marketplace.** The prompt is private to the user's profile;
  no shared template library.
- **No A/B testing of variants.** Tempting, but the sample size of
  outreach campaigns per user is too small for any conclusion to be
  meaningful. Out of v2.
- **No outbound email.** v1 already drafts cold-email-subject and
  cold-email-body, but the dispatcher doesn't send them and there's no SMTP
  integration. v2 keeps that scope intact — emails are still draft-only.
  Email automation is its own ToS minefield (CAN-SPAM, opt-out, etc.) and
  out of scope.
- **No account-level rate-limit override.** The 30-DMs/day default is a
  ceiling, not a target. Users can lower it; we don't expose a way to
  raise it (LinkedIn will punish them, not us).

## Verification before shipping

The v1 audit (in `benchmarks/results/2026-04-26/REPORT.md` discussion)
identified two integration-test gaps that get harder to ignore in v2:

1. **End-to-end campaign-creation → claim → result → finish.** v2 multiplies
   this by 4× (one connect + three DM steps per target), so the existing
   manual test plan won't scale. We need an automated integration test
   that mocks the extension and walks a full thread.
2. **LLM fallback path.** v2's draft generation runs 4× per target; one
   transient Groq failure on follow-up #2 shouldn't tank the whole thread.
   The recovery path in `outreach_generator.py:25-46` needs an explicit
   test before v2 can rely on it.

Both should ship as part of v2's PR, not after.

## Estimated size

- **Schema + data model:** 1 migration, ~50 LOC in models.py.
- **Dispatcher:** ~80 LOC across `outreach_dispatcher.py`. The
  `scheduled_after` filter in `claim_next_action` is the only meaningful
  algorithmic change.
- **Generator:** ~120 LOC for the per-step prompts, + tests.
- **Extension:** new `content_probe.js` (~80 LOC), DM-flow branch in
  `content_linkedin.js` (~120 LOC). Manifest version bump + permission
  refresh (no new permissions).
- **UI:** Campaign panel thread expansion + drafts editor (~300 LOC of
  template + Alpine).
- **Tests:** ~30 new test cases minimum.

Total: ~700 LOC + tests. Roughly 3–5 dedicated days, mostly in the UI and
LLM prompt tuning. The core dispatcher change is small.

## Open questions

- **DM character limit.** Connect-with-note is 300 chars; LinkedIn DMs are
  effectively unlimited. Should v2 cap follow-ups at e.g. 800 chars to
  match the readability of connect notes, or trust the LLM's judgment?
  Probably cap.
- **Accept-detection cadence.** The probe runs whenever the user visits
  any LinkedIn profile. Is that frequent enough? For most users yes — they
  visit LinkedIn daily. For users who never browse profiles manually,
  acceptance might never be detected. Possible mitigation: a once-a-day
  "background sweep" of pending targets, but that requires the extension
  to navigate proactively, which is more bot-detectable. Open question.
- **Multi-job follow-up coalescing.** If a candidate is connected to multiple
  jobs in the user's pipeline (e.g., they're an SWE the user is targeting
  for two different roles), should the follow-up reference both, or treat
  them as separate threads? Probably separate, but worth deciding before
  building.

## Decision log

This document should be edited in-place (not appended to) as decisions are
made. When v2 ships, the entire content gets a "DELIVERED" header and
moves to `docs/architecture/outreach.md` as the v2 reference.
