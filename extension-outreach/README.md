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

## Limitations

* Not packaged for the Chrome Web Store — Web Store review will reject any
  extension that automates LinkedIn UI flows.
* LinkedIn DOM changes will break the content script. The script asserts
  selectors and reports `selector_drift` so the SmartCV status panel surfaces
  the breakage instead of silently failing.
* Only handles `kind=connect` (connect-with-note). Direct messages and
  follow-up after-accept are out of v1 scope.
