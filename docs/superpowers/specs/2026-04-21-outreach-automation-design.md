# Outreach Automation (Browser-Extension Hybrid) — Design Spec

**Date:** 2026-04-21
**Feature:** Automated LinkedIn outreach for SmartCV (extends `outreach_generator.py`)
**Status:** Design draft, pending user review

## Problem

`profiles/services/outreach_generator.py` already drafts a LinkedIn connection request and a cold email per `(profile, job)`. The user copy-pastes into LinkedIn manually, and there is no recipient discovery — the user has to find the hiring manager and target employees themselves. The drafting quality goes to waste because the friction between "draft" and "sent" is everything that isn't drafting.

## Goal

Close the loop end-to-end: SmartCV identifies relevant people for a given job, drafts personalized messages, and *sends connection requests + follow-up messages on the user's behalf* — without ever taking the user's LinkedIn password or storing their session cookie on the server, and without the server ever talking to LinkedIn directly.

The mechanism is a **paired Chrome extension** that executes the LinkedIn-side actions inside the user's own browser tab while SmartCV's Django server orchestrates the campaign (queue, throttle, draft, status).

## Non-goals

- **Public Chrome Web Store distribution.** The extension is unpacked / sideloaded for the demo. Web Store review will reject any extension that automates LinkedIn UI flows; that's out of scope.
- **Headless browser automation on the SmartCV server.** Explicitly rejected (see Approach A in the brainstorming session).
- **Storing the user's LinkedIn `li_at` cookie on the server.** All LinkedIn auth stays in the user's browser.
- **Email sending.** Cold-email drafts are still copy-paste; only LinkedIn is automated in v1.
- **Reply parsing / inbox monitoring.** v1 tracks send + accept; reply detection is v2.
- **Cross-platform sending** (Twitter/X, Bluesky). LinkedIn only.

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│ User in Chrome with SmartCV-Outreach extension installed,     │
│ already logged into linkedin.com in another tab               │
└─────────┬─────────────────────────────────────────┬──────────┘
          │                                         │
          │ 1. Open SmartCV                         │ 5. Background worker polls
          ▼                                         │    SmartCV /api/outreach/next
┌─────────────────────────────────────┐            │
│ SmartCV web app                     │            │
│  /jobs/<id>/outreach/campaign/      │            │
│   ─ "Find people" → server-side     │            │
│     calls jobs/services/            │◀───────────┘
│     linkedin_scraper to fetch the   │
│     job page (anonymous), extracts  │            ┌──────────────────────────┐
│     "Meet the hiring team" handles  │            │ Chrome extension         │
│     when present                    │            │  ─ background.js (MV3    │
│   ─ "Find peers" → Google SERP      │            │    service worker)       │
│     fallback (site:linkedin.com/in) │            │  ─ content_scripts on    │
│   ─ User picks targets, approves    │            │    linkedin.com/*        │
│     drafts, hits "Send campaign"    │            │  ─ popup.html (status)   │
└──────────────┬──────────────────────┘            └────────┬─────────────────┘
               │                                            │
               │ 2. POST /api/outreach/campaigns/           │ 6. Pull next OutreachAction
               │    {job_id, targets[], throttle}           │    from server, drive
               │                                            │    LinkedIn UI inside
               ▼                                            │    user's authenticated tab
┌───────────────────────────────────────────────────────────┴──┐
│ Django                                                       │
│   ─ OutreachCampaign(user, job, status, throttle)            │
│   ─ OutreachAction(campaign, target_url, kind, payload,      │
│       status, attempts, last_error, completed_at)            │
│   ─ /api/outreach/next  → returns oldest queued action       │
│   ─ /api/outreach/result → extension posts success/fail      │
│   ─ Throttle check: ≤ N invites/day per user (configurable)  │
└──────────────────────────────────────────────────────────────┘
               ▲                                            │
               │                                            │
               │ 7. POST result                             │
               └────────────────────────────────────────────┘
```

## Components

### 1. Database — `profiles/models.py` (new models)

```python
class OutreachCampaign(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    job = models.ForeignKey('jobs.Job', on_delete=models.CASCADE)
    status = models.CharField(max_length=16, default='draft')
        # draft | running | paused | done | failed
    daily_invite_cap = models.PositiveSmallIntegerField(default=15)
    created_at = models.DateTimeField(auto_now_add=True)

class OutreachAction(models.Model):
    campaign = models.ForeignKey(OutreachCampaign, related_name='actions')
    target_handle = models.CharField(max_length=128)        # linkedin vanity slug
    target_name = models.CharField(max_length=128, blank=True)
    target_role = models.CharField(max_length=128, blank=True)
    kind = models.CharField(max_length=16)                  # connect | message
    payload = models.TextField()                            # the message body
    status = models.CharField(max_length=16, default='queued')
        # queued | in_flight | sent | accepted | failed | skipped
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    queued_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
```

**Indexes:** `(campaign, status)`, `(target_handle, campaign)` to dedupe.
**Daily-cap enforcement:** computed from `OutreachAction.completed_at` per user per day; queried at dispatch time.

### 2. Server-side discovery — `jobs/services/people_finder.py` (new)

Two strategies, both unauthenticated:

- **`find_hiring_team(job_url) -> list[Target]`**
  Reuses `jobs.services.linkedin_scraper.scrape_linkedin_job` to fetch the job page anonymously. Parses any `meet-the-hiring-team` block if present in public HTML (it is for some jobs, hidden for most). Returns 0–3 targets. Honest about its low hit rate.

- **`find_peers_via_google(company, role_keywords, n=10) -> list[Target]`**
  Issues a query like `site:linkedin.com/in "{company}" "{role keyword}"` against a SERP source. **Implementation choice:** start with a single `requests.get` to Google with rotating `User-Agent` and a 1.5s timeout; degrade to "show me the search URL" if it 429s. No paid SERP API in v1.

`Target` is a dataclass: `{handle, name, role, source: 'hiring_team' | 'google'}`.

### 3. Drafting — extend `profiles/services/outreach_generator.py`

Add `generate_outreach_for_target(profile, job, target) -> {connect_message, follow_up_message}`. Reuses the existing `OutreachCampaignResult` schema with one extra LLM call per target so messages reference the target's role. Anti-hallucination rule from the existing module is preserved.

**Cost note:** N targets = N LLM calls. With `daily_invite_cap=15` that's ≤ 15 calls per campaign, latency hidden behind the user's review step.

### 4. Server endpoints — `profiles/views.py` + `profiles/urls.py`

| Method + Path | Purpose |
|---|---|
| `GET /jobs/<uuid:job_id>/outreach/campaign/` | Campaign builder UI: discovers targets, shows drafts, lets user approve/edit |
| `POST /api/outreach/campaigns/` | Create `OutreachCampaign` + `OutreachAction` rows from approved targets |
| `GET /api/outreach/next` | **Extension polls this.** Returns oldest queued action *if* under daily cap; else `204 No Content`. Auth: extension token (see §6) |
| `POST /api/outreach/result/<action_id>/` | Extension reports outcome: `{status, error?}`. Updates `OutreachAction` |
| `POST /api/outreach/campaigns/<id>/pause/` | Pause/resume from web UI |

All `/api/outreach/*` endpoints validate the action belongs to the authenticated user.

### 5. Browser extension — `extension/` (new top-level dir, not Django-served)

Manifest V3, ~6 files:

```
extension/
  manifest.json            # MV3, perms: storage, alarms, scripting,
                           #   host_permissions: ["https://www.linkedin.com/*",
                           #                      "https://<smartcv-host>/*"]
  background.js            # service worker: poll loop via chrome.alarms
                           #   every 90s; jittered by ±20s
  content_linkedin.js      # injected into linkedin.com/in/* and
                           #   linkedin.com/mynetwork/*; performs
                           #   click-Connect → Add note → Send
  popup.html / popup.js    # status: "Connected to SmartCV ✓ |
                           #   12/15 invites used today | Pause"
  options.html             # paste SmartCV API token; set throttle
                           #   ceiling
```

**The execution loop (background.js):**

1. `chrome.alarms` fires every ~90 s.
2. `fetch(SMARTCV_HOST + '/api/outreach/next', {headers: {Authorization: 'Token …'}})`.
3. If `204`, sleep until next alarm.
4. If `200`, open or focus a LinkedIn tab on `linkedin.com/in/<target_handle>/`.
5. Inject content script with the action payload via `chrome.scripting.executeScript`.
6. Content script awaits the page DOM, clicks `Connect → Add a note → <type>`, then `Send`. Uses `setTimeout` jitter (1.2–3.5 s between actions) and `MutationObserver` for state, **not** `Promise`-based instant clicks — humanized cadence is the whole point of doing this client-side.
7. Content script reports back to background, which `POST /api/outreach/result/<id>/`.

**Failure modes the content script must distinguish:**
- "Already connected" → `status=skipped`
- "Connection limit reached" weekly modal → `status=failed, error='weekly_cap'` and *background.js stops polling for 24h*
- Profile not found → `status=failed, error='not_found'`
- Generic timeout → increment `attempts`, requeue if `<3`

### 6. Auth handoff — extension ↔ SmartCV

User flow on first install:
1. Open `chrome-extension://…/options.html`.
2. Click "Pair with SmartCV" → opens `https://<smartcv-host>/extension/pair/`.
3. SmartCV view (auth-required) shows a one-time bearer token (UUID, stored on `User.outreach_token`, regenerable from settings).
4. User pastes token into extension options. Extension stores in `chrome.storage.local`.
5. All `/api/outreach/*` requests carry `Authorization: Token <uuid>`.

Token is **opaque, scoped only to outreach endpoints** (DRF token auth class checks the prefix). Never the user's session cookie. Revocable from the SmartCV web UI.

### 7. UI surfaces — `templates/profiles/outreach_campaign.html`

Single-page Alpine.js view with three sections:

- **Targets** (top): Hiring team box + "Find peers" button → table with `[checkbox] name | role | source`
- **Drafts** (middle): For each selected target, the LLM-generated connect message in an editable `<textarea>`. "Regenerate" button per target.
- **Send** (bottom): Daily cap selector (default 15, max 25), "Start campaign" button. After start, this section becomes a live status panel polling `/api/outreach/campaigns/<id>/status/` every 5 s.

## Data flow

1. User on `/jobs/<id>/` clicks "Run outreach campaign" → lands on `/jobs/<id>/outreach/campaign/`.
2. View calls `find_hiring_team()` and renders results immediately. User clicks "Find peers" → AJAX to `find_peers_via_google()`.
3. User picks 8 targets, hits "Generate drafts" → server runs `generate_outreach_for_target` ×8 in parallel (`concurrent.futures.ThreadPoolExecutor`, max_workers=4 to respect Groq rate limits).
4. User edits drafts inline, hits "Start campaign" → POST creates `OutreachCampaign(status='running')` + 8 `OutreachAction(status='queued')`.
5. Within ≤90 s, the user's extension polls, gets the first action, navigates the LinkedIn tab, sends the invite, reports back. Repeats on the next alarm.
6. Status page in SmartCV reflects each action moving `queued → in_flight → sent` (or `failed`).

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| LinkedIn DOM changes → content script breaks silently | Content script asserts presence of expected selectors before clicking; on assertion fail, marks action `failed` with `error='selector_drift'` and surfaces it in the SmartCV status panel so the user notices |
| User exceeds LinkedIn's weekly invite limit (~100/week, soft) | `daily_invite_cap` is a hard ceiling on our side; weekly-cap detection in content script halts dispatch for 24 h |
| Extension token leaks (e.g. user reposts options.html screenshot) | Token is single-purpose (outreach endpoints only) and revocable from SmartCV UI; rotating it invalidates the extension instantly |
| User has multiple SmartCV accounts → two extensions on one Chrome profile | Out of scope. Pairing overwrites stored token. |
| Duplicate sends if user re-runs a campaign on the same target | DB unique constraint on `(campaign, target_handle, kind)` |
| LinkedIn ToS exposure for the *user* | Disclosed in the campaign UI ("This automates actions on your LinkedIn account. LinkedIn may rate-limit or restrict accounts that exceed normal usage. SmartCV defaults to ≤15 invites/day to stay within human-plausible bounds."). User opts in per campaign. |
| Google SERP scraping gets 429'd | Single-shot, soft-fail to "search yourself" link; no retries, no rotation. Acceptable for graduation-demo scale. |

## Open questions for user review

1. **Daily cap default:** 15 sounds defensible. Confirm or override?
2. **Polling cadence:** 90 s feels right (low extension overhead, slow enough to look human across actions). Lower = faster campaigns but more noise.
3. **First message timing:** Send connect-with-note immediately, or queue the follow-up message for *after* acceptance is detected? v1 spec is "connect-with-note only; no separate follow-up until reply detection ships in v2." Confirm.
4. **Where does the campaign live in nav?** Proposed: a "Outreach" tab on the per-job page (`/jobs/<id>/outreach/campaign/`), and a row in `/applications/` showing campaign status per applied job.

## Out-of-scope (parking lot for v2)

- Acceptance detection (would require extension to scrape `/mynetwork/invitation-manager/` periodically)
- Reply detection + auto-follow-up
- Email channel automation (would need OAuth Gmail / IMAP)
- A/B testing of message variants
- Sequence builder ("if accepted within 3 days, send follow-up X; else send Y")
- Public Chrome Web Store packaging

## Implementation order (when approved)

1. DB models + migration
2. Server endpoints + token auth + campaign UI (no extension yet — test by hand-POSTing actions and marking them `sent` manually)
3. `people_finder.py` (hiring team + Google SERP)
4. Extend `outreach_generator.py` per-target drafting
5. Extension MVP: pairing + polling + connect-with-note
6. Status panel + pause/resume
7. End-to-end test on one real LinkedIn account
