# SmartCV Outreach — Chrome extension (MVP)

Sideloaded MV3 extension that drains your SmartCV outreach queue from inside
your own LinkedIn tab. Server orchestrates and drafts; this extension only
executes the click flow with humanized timing.

## Install

1. Open `chrome://extensions` and enable **Developer mode**.
2. Click **Load unpacked** and select this `extension-outreach/` directory.
3. Click the puzzle-piece icon → **SmartCV Outreach** → **Options**.
4. Set **SmartCV host** (e.g. `http://127.0.0.1:8000`) and paste the token
   from `/profiles/extension/pair/`.
5. Make sure you're logged into LinkedIn in the same browser.

## How it works

* `background.js` — service worker; polls `/profiles/api/outreach/next` every
  ~90 s ± 20 s via `chrome.alarms`. On hit, opens/refocuses a LinkedIn tab on
  the target's profile and runs `content_linkedin.js` against it.
* `content_linkedin.js` — clicks **Connect → Add a note → <type>** with
  jittered delays (40–120 ms per chunk, 0.5–2.4 s between actions) and uses
  `MutationObserver` to wait for modal state. Returns `{status, error?}`.
* On `weekly_cap`, the worker pauses polling for 24 h via
  `chrome.storage.local`.

## Status surface (v0.3.0+)

The popup now classifies poll outcomes and shows them so the user knows
when something broke:

* `Token rejected` (red) — server returned 401/403. The "Open Options"
  link in the banner takes the user straight to the re-pair flow. Polling
  backs off to 30 min so we don't burn rate budget while waiting for a fix.
* `Server is rate-limited` (amber) — server (or upstream Groq) returned
  429. Honors `Retry-After` header up to 60 min, default 30 min. No polling
  during the cooldown.
* `Can't reach SmartCV` (red) — fetch threw (network down, host wrong,
  DNS fail). Backs off to 5 min.
* `Server error` (amber) — 5xx response. Backs off to 5 min.
* `LinkedIn weekly cap hit` (amber) — content script detected the cap
  modal. 24 h pause, no polling.

A "Recent activity" panel shows the last 10 actions (sent / accepted /
failed / skipped) with target and timestamp so the user can confirm the
extension is doing something even when the queue is otherwise quiet.

## Limitations

* Not packaged for the Chrome Web Store — Web Store review will reject any
  extension that automates LinkedIn UI flows.
* LinkedIn DOM changes will break the content script. The script asserts
  selectors and reports `selector_drift` so the SmartCV status panel surfaces
  the breakage instead of silently failing.
* Only handles `kind=connect` (connect-with-note). Direct messages and
  follow-up after-accept are out of v1 scope.
