# Outreach Automation v2 — Extension-Side Discovery

**Date:** 2026-04-21
**Feature:** Move target discovery from the SmartCV server into the paired Chrome extension
**Builds on:** v1 spec `2026-04-21-outreach-automation-design.md` (commit `43ae31b`)
**Status:** Design draft, pending user review

## Problem

v1's server-side discovery is structurally broken on every job we've tested.

* `find_hiring_team` hits the public LinkedIn job page anonymously. LinkedIn renders "Meet the hiring team" only for authenticated users, so the public HTML omits it on almost every posting.
* `find_peers_via_google` hits Google's SERP with `requests.get`. Google detects the datacenter / residential-IP pattern and bounces us to `/sorry/index` (HTTP 429). No keyword tweak, jitter, or `User-Agent` rotation gets past this from the server.

Meanwhile the user's own logged-in LinkedIn tab routinely shows two relevant sections on the same job page that the server can never see:

* **People you can reach out to** — alumni / 2nd-degree connections at the company. ~3–8 results per job.
* **Meet the hiring team** — the recruiter or hiring manager named on the post. 0–3 results.

Plus the company-wide **People** directory (`/company/<slug>/people/`) which lists every employee with role/title/department filters.

The extension already runs inside this logged-in context. v1 used it only as an executor for queued actions; v2 turns it into the discovery source.

## Goal

When a SmartCV user lands on the campaign builder for a LinkedIn job and triggers discovery, their extension auto-scrapes the relevant LinkedIn pages in their authenticated tab, POSTs the targets back to SmartCV, and the campaign builder displays them as discovered targets ready for review and drafting.

The user's manual paths (paste handle, manual fields) stay exactly as they are — extension discovery is additive, not a replacement.

## Non-goals

* **Replacing the manual "Add" path.** Still useful when the user knows exactly who they want.
* **Auto-sending without review.** Discovery only — the user still approves drafts and clicks Start campaign.
* **Web Store packaging.** Still sideload-only.
* **Scraping search-by-people** (`/search/results/people/`). Higher signal but rate-limited harder by LinkedIn; defer to v3.
* **InMail / paid LinkedIn features.** Free LinkedIn surface area only.
* **Background drafting.** LLM calls happen lazily, when the user clicks an "Add" button per target — not on every scraped row, since most won't be selected.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│ User on SmartCV /profiles/outreach/<id>/campaign/                    │
│  • Clicks "Discover via extension" → POST /api/outreach/discovery/   │
│    {job_id, sources: ["job_page","company_people"]}                  │
│  • Server creates DiscoveryRequest(status='pending'), returns ID      │
│  • Page polls /api/outreach/discovery/<id>/ every 3s                 │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             │ Extension polls /api/outreach/discovery/next
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Extension service worker                                             │
│  • Same poll loop (chrome.alarms ~90s) as v1, second endpoint check  │
│  • On hit, opens (or focuses) a LinkedIn tab on the job URL          │
│  • Injects content_discover.js                                       │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ content_discover.js (in user's authenticated LinkedIn tab)           │
│  • Scrapes "Meet the hiring team" + "People you can reach out to"    │
│  • If sources includes "company_people": opens                       │
│    /company/<slug>/people/, scrapes first page (~10 employees)       │
│  • POSTs raw targets back to SmartCV with extension token            │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Server                                                               │
│  • Stores DiscoveredTarget rows under the DiscoveryRequest           │
│  • Marks request status='ready'                                      │
│  • Page's poll catches this, fetches /<id>/results, renders rows     │
│  • User clicks "Add" per row → LLM drafts that one (existing path)   │
└──────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. DB — `profiles/models.py` (additive)

```python
class DiscoveryRequest(models.Model):
    """A user-initiated extension scrape job. Short-lived (auto-cleanup after 24h)."""
    SOURCES = [('job_page', 'Job page'), ('company_people', 'Company people')]
    STATUS = [('pending', 'Pending'), ('in_flight', 'In flight'),
              ('ready', 'Ready'), ('failed', 'Failed')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    job = models.ForeignKey('jobs.Job', on_delete=models.CASCADE)
    sources = models.JSONField(default=list)          # subset of SOURCES keys
    status = models.CharField(max_length=16, choices=STATUS, default='pending')
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)


class DiscoveredTarget(models.Model):
    """A single scraped person. Drafts are NOT generated here — only when the
    user clicks Add in the UI (existing draft_manual_target endpoint)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    request = models.ForeignKey(DiscoveryRequest, on_delete=models.CASCADE,
                                related_name='targets')
    handle = models.CharField(max_length=128)
    name = models.CharField(max_length=128, blank=True)
    role = models.CharField(max_length=128, blank=True)
    source = models.CharField(max_length=24)          # 'hiring_team' | 'people_you_know' | 'company_people'

    class Meta:
        constraints = [models.UniqueConstraint(fields=['request', 'handle'],
                                               name='unique_target_per_request')]
```

A daily cleanup deletes `DiscoveryRequest` rows >24h old (cron, or just `created_at__lt=now-24h` filter at view time + lazy delete). The actual outreach flow uses `OutreachAction`, which is the long-lived record.

### 2. Server endpoints — `profiles/views_outreach_api.py` (extend)

| Method + Path | Auth | Purpose |
|---|---|---|
| `POST /api/outreach/discovery/` | session | Web UI creates request: `{job_id, sources}`. Returns `{id}`. |
| `GET /api/outreach/discovery/<id>/` | session | Web UI polls; returns `{status, targets: []}` |
| `GET /api/outreach/discovery/next` | extension token | Returns oldest `pending` request as `{id, job_url, sources}`; marks `in_flight`. |
| `POST /api/outreach/discovery/<id>/results/` | extension token | Extension dumps `[{handle, name, role, source}]`; server stores + marks `ready`. |
| `POST /api/outreach/discovery/<id>/error/` | extension token | Extension reports `{error}` on scrape failure; server marks `failed`. |

Daily-cap logic does **not** apply to discovery — it's read-only on LinkedIn's side, no invites are sent. Concurrency: same `claim`-style update as v1's `claim_next_action` so two extension tabs don't double-claim a request.

### 3. Extension — `extension-outreach/` (extend)

* `background.js` adds a second poll branch to the existing alarm: after `outreach_next` returns 204, hit `discovery/next`. If a request is returned, dispatch to a new `runDiscovery(request)`.
* `content_discover.js` (new) — gets the request payload via `chrome.scripting.executeScript`, scrapes the page, returns `{targets: [...]}`. Background POSTs to `/results/` or `/error/`.

Selectors (mirror the same `selector_drift` failure mode v1 has):

| Section | Selector hint |
|---|---|
| Meet the hiring team | `section[data-test-modal-id="hirer-modal"] a[href*="/in/"]`, `.hirer-card__hirer-information a[href*="/in/"]` |
| People you can reach out to | `section:has(h2:contains("People you can reach out to")) a[href*="/in/"]` (or rely on a stable section data-test-id when LinkedIn provides one) |
| Company people page | `[data-view-name="profile-card"] a[href*="/in/"]` (LinkedIn's React profile cards in /company/<slug>/people/) |

For each anchor: `_extract_handle()` (already in `people_finder.py`) for the slug; the surrounding text node for `name` and `role`. Cap at 10 per source for v2.

The extension never opens additional tabs — it scrolls the existing job/company tab. If the user navigates away mid-scrape, the request fails with `error='tab_navigated_away'`.

### 4. UI — `templates/profiles/outreach_campaign.html` (extend)

A second button next to "Find people + draft":

```
[ Find people + draft ]    [ Discover via extension ↗ ]
                            opens LinkedIn job page in new tab,
                            extension scrapes when it lands.
```

When clicked:
1. POST `/api/outreach/discovery/` with `{job_id, sources: ['job_page', 'people_you_know']}`.
2. Open `https://www.linkedin.com/jobs/view/<id>/` in a new tab.
3. Show a "Waiting for extension on LinkedIn tab…" toast that polls every 3s.
4. When `status='ready'`, render the new targets in the existing targets table (with `source: 'extension:job_page'` etc.), each with an "Add to drafts" button that calls the existing `draft_manual_target` endpoint to generate a draft and slot the row into the existing flow.

Discovered targets that aren't added remain in the `DiscoveredTarget` table for the request's lifetime, then are cleaned up after 24h.

### 5. Extension manifest — `extension-outreach/manifest.json` (extend)

Add the company-people host pattern explicitly:
```json
"host_permissions": [
  "https://www.linkedin.com/jobs/*",
  "https://www.linkedin.com/in/*",
  "https://www.linkedin.com/company/*"
]
```

(This widens the existing `https://www.linkedin.com/*` if v1 used it, but the v1 manifest already grants the broad scope, so no permission re-prompt for users who have it installed.)

## Data flow (E2E)

1. User opens `/profiles/outreach/<job-id>/campaign/`. Empty state shows the "Find people + draft" button **and** a new "Discover via extension" button.
2. Click "Discover via extension" → server creates `DiscoveryRequest(status='pending')`. New LinkedIn tab opens at the job URL.
3. Extension's next ≤90s alarm fires; polls `discovery/next`; gets the request (in `in_flight`); injects `content_discover.js` into the (just-opened) LinkedIn tab.
4. Content script scrapes hiring team + people-you-know; if `company_people` was in sources, scrolls/awaits the company directory and scrapes that too. POSTs `[targets]` to `/results/`.
5. Server marks the request `ready`; SmartCV page's 3s poll picks it up; renders rows in the targets table.
6. User reviews → clicks "Add to drafts" per target → `draft_manual_target` (existing) returns the LLM draft → row joins the existing review/start-campaign flow unchanged.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| LinkedIn DOM changes (high — they ship UI updates routinely) | Same `selector_drift` reporting as v1's content script; surface the breakage in the SmartCV waiting toast: "Extension couldn't find the expected sections — LinkedIn may have changed its layout. Use Add manually for now." |
| LinkedIn detects scrape pattern over time | Read-only DOM access (no Network calls beyond the navigation already triggered by the user opening the tab). LinkedIn's bot detection focuses on action patterns (invites, messages), not passive reads. |
| User has multiple LinkedIn tabs open | Extension targets the most-recently-active LinkedIn tab. Document this in the waiting toast: "Make sure the LinkedIn job tab is the active one." |
| Extension scrape races with LinkedIn's lazy-load | Use `MutationObserver` (already in v1's content_linkedin.js pattern) with a 6s timeout per section before reporting empty. Empty sections are NOT failures — just `[]`, so the user still gets whatever was visible. |
| Discovery request piles up if user never opens LinkedIn | The next-claim ignores requests >10min old (auto-fail); cleanup deletes >24h. Stale claims are returned to `pending` after 5min so a different extension session can pick them up. |
| Same LinkedIn profile listed in multiple sources | DB unique constraint on `(request, handle)`; client-side de-dupe at render. |

## Open questions

1. **Default sources:** v2 ships with `['job_page', 'people_you_know']` enabled by default. `company_people` requires opening a second URL — make it opt-in via a checkbox on the discover button, or include automatically?
2. **Discovery cadence:** the extension already polls every ~90s for actions. Is sharing that alarm fine, or should discovery have its own faster ~30s cadence (since the user is actively waiting on the SmartCV tab)? Faster cadence = visible "extension found 5 people" feedback within seconds.
3. **Should `find_peers_via_google` be removed entirely** now that we know it 429s reliably? Or keep as a no-op that surfaces the diagnostic empty-state with a "search Google manually" link as before?

## Out-of-scope (parked for v3+)

* `linkedin.com/search/results/people/` scraping (broader queries, harder rate limits)
* Acceptance detection (`/mynetwork/invitation-manager/`)
* Reply detection
* InMail / Sales Navigator surfaces
* Twitter/Bluesky outreach

## Implementation order

1. Models + migration (`DiscoveryRequest`, `DiscoveredTarget`)
2. Server endpoints + claim/result + cleanup
3. Extension `background.js` discovery branch + `content_discover.js`
4. Campaign template "Discover via extension" button + waiting state + render-from-results
5. Tests: claim ordering, result idempotency, stale-claim recovery, content-script unit tests for selector parsers (run against a saved LinkedIn HTML fixture)
6. Update `docs/qa/outreach-automation-test-plan.md` with extension-discovery scenarios
7. End-to-end test against one real LinkedIn job posting
