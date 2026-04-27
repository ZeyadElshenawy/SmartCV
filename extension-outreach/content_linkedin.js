// Injected into the user's LinkedIn profile tab. Drives the connect-with-note
// flow with humanized pacing and reports outcome back via the function return.
//
// Outcome shape: { status, error?, detail?, matched_selector? }
//
// `status` values must match what record_action_result accepts on the server.
// Errors we deliberately surface separately so the dispatcher and UI can react:
//   - 'already_connected'   → skipped
//   - 'weekly_cap'          → failed (background.js pauses 24h on this)
//   - 'not_found'           → failed
//   - 'selector_drift:<step>' → failed (LinkedIn DOM changed at <step>)
//   - 'profile_not_ready'   → failed (page never hydrated past the load event)
//   - 'timeout'             → failed
//
// `matched_selector` is set to the selector that actually worked at each step,
// so when LinkedIn shifts and we still find the button via a fallback, the
// server can flag "we're surviving on selector #2 — fix-it window is closing"
// rather than a binary works/broken signal.

(function () {
    if (window.smartcvOutreach) return;  // re-injection guard

    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const jitter = (lo, hi) => sleep(lo + Math.random() * (hi - lo));

    // Try a sequence of selectors; resolve with the first match. Each entry is
    // { sel, label } so the caller can report which one survived.
    function waitForAnySelector(candidates, timeoutMs = 8000) {
        return new Promise((resolve) => {
            const tryNow = () => {
                for (const c of candidates) {
                    const node = document.querySelector(c.sel);
                    if (node) return { node, candidate: c };
                }
                return null;
            };
            const initial = tryNow();
            if (initial) return resolve(initial);
            const observer = new MutationObserver(() => {
                const hit = tryNow();
                if (hit) {
                    observer.disconnect();
                    resolve(hit);
                }
            });
            observer.observe(document.body, { childList: true, subtree: true });
            setTimeout(() => {
                observer.disconnect();
                resolve(null);
            }, timeoutMs);
        });
    }

    // Wait for ANY of a list of (predicate-described) buttons to appear.
    // Used when aria-labels are localized or A/B-tested: predicates inspect
    // the DOM directly instead of relying on a CSS selector match.
    function waitForButton(predicateLabel, predicate, timeoutMs = 8000) {
        return new Promise((resolve) => {
            const tryNow = () => {
                const buttons = Array.from(document.querySelectorAll(
                    'button[aria-label], div[role="button"][aria-label], button, a[role="button"]'
                ));
                return buttons.find(predicate) || null;
            };
            const initial = tryNow();
            if (initial) return resolve({ node: initial, label: predicateLabel });
            const observer = new MutationObserver(() => {
                const hit = tryNow();
                if (hit) {
                    observer.disconnect();
                    resolve({ node: hit, label: predicateLabel });
                }
            });
            observer.observe(document.body, { childList: true, subtree: true });
            setTimeout(() => {
                observer.disconnect();
                resolve(null);
            }, timeoutMs);
        });
    }

    // Detect "this profile cannot be connected with" / "page not found"
    function isProfileMissing() {
        const body = document.body.innerText || '';
        return /This profile is not available|Page not found|This page doesn'?t exist/i.test(body);
    }

    // Detect the weekly invite-cap modal LinkedIn shows after ~100 invites/week.
    // Multiple wordings observed in the wild — keep this regex permissive.
    function isWeeklyCapModal() {
        const body = document.body.innerText || '';
        return /You.{0,3}re close to the weekly invitation limit|reached the weekly invitation limit|weekly invite limit|too many invitations/i.test(body);
    }

    // Wait for the profile DOM to actually hydrate. tab.status='complete' from
    // the service worker only means the navigation finished — LinkedIn's
    // React app then async-loads the profile header, action bar, etc. Without
    // this guard, fast machines hit the click-flow before the Connect button
    // exists in the DOM and falsely report selector_drift.
    async function waitForProfileReady(timeoutMs = 8000) {
        const start = Date.now();
        while (Date.now() - start < timeoutMs) {
            // Profile header (h1 with the candidate name) is the most reliable
            // "this is a profile page that has rendered" signal.
            const h1 = document.querySelector('main h1, section.pv-text-details__left-panel h1, h1.text-heading-xlarge');
            // Or the action bar with at least one labeled button.
            const actionBtn = document.querySelector(
                'main button[aria-label], section.pv-top-card button[aria-label]'
            );
            if (h1 || actionBtn) return true;
            // Bail early if we already know it's a missing/error page.
            if (isProfileMissing()) return false;
            await sleep(150);
        }
        return false;
    }

    async function findConnectButton() {
        // 1) Direct Connect button anywhere with an aria-label that starts with
        //    "Connect" or contains the word (handles "Invite X to connect").
        const direct = Array.from(document.querySelectorAll('button[aria-label], div[role="button"][aria-label]'))
            .find((b) => {
                const al = (b.getAttribute('aria-label') || '').trim();
                return /^Connect\b|^Invite .* to connect/i.test(al);
            });
        if (direct) return { node: direct, label: 'aria_label_connect' };

        // 2) Some profiles bury Connect under the "More actions" overflow menu.
        const more = Array.from(document.querySelectorAll('button[aria-label], div[role="button"][aria-label]'))
            .find((b) => /^More actions/i.test((b.getAttribute('aria-label') || '').trim()));
        if (more) {
            more.click();
            await jitter(600, 1200);
            const overflowConnect = Array.from(document.querySelectorAll('div[role="button"], button, span, li'))
                .find((d) => /^Connect$|Invite .* to connect/i.test((d.innerText || '').trim()));
            if (overflowConnect) return { node: overflowConnect, label: 'overflow_more_actions' };
        }

        // 3) Last-ditch: any button whose visible text is exactly "Connect".
        //    Lower confidence (could be on a different card, e.g., suggested
        //    connections), so we only return it when we couldn't find a real
        //    profile-action-bar button.
        const byText = Array.from(document.querySelectorAll('main button, main div[role="button"]'))
            .find((b) => /^Connect$/i.test((b.innerText || '').trim()));
        if (byText) return { node: byText, label: 'main_text_connect' };

        return null;
    }

    async function isAlreadyConnected() {
        // LinkedIn replaces "Connect" with "Message" or shows "Pending" once
        // connected. "Following" alone is NOT enough (anyone can follow).
        const buttons = Array.from(document.querySelectorAll('button[aria-label], div[role="button"][aria-label]'));
        return buttons.some((b) => {
            const al = (b.getAttribute('aria-label') || '').trim();
            return /^(Message|Pending|Withdraw invitation)/i.test(al);
        });
    }

    async function run(action) {
        const trace = [];  // record which selectors matched at each step
        try {
            await jitter(1200, 2400);

            // Wait for the React-driven profile DOM to hydrate before doing
            // any selector work. Without this, slow connections false-fail
            // with selector_drift on Connect.
            const ready = await waitForProfileReady(8000);
            if (!ready) {
                if (isProfileMissing()) return { status: 'failed', error: 'not_found' };
                return { status: 'failed', error: 'profile_not_ready' };
            }
            if (isWeeklyCapModal()) return { status: 'failed', error: 'weekly_cap' };
            if (await isAlreadyConnected()) return { status: 'skipped', error: 'already_connected' };

            const connect = await findConnectButton();
            if (!connect) return { status: 'failed', error: 'selector_drift:connect', trace };
            trace.push({ step: 'connect', via: connect.label });

            connect.node.click();
            await jitter(900, 1800);

            if (isWeeklyCapModal()) return { status: 'failed', error: 'weekly_cap' };

            // "Add a note" button — multiple wording variants seen across
            // locales and recent UI tests. Predicate-based to be robust.
            const addNote = await waitForButton('add_note', (b) => {
                const al = (b.getAttribute && b.getAttribute('aria-label') || '').trim();
                const txt = (b.innerText || '').trim();
                return /add a note|add note/i.test(al) || /^Add a note$|^Add note$/i.test(txt);
            }, 6000);
            if (!addNote) return { status: 'failed', error: 'selector_drift:add_note', trace };
            trace.push({ step: 'add_note', via: addNote.label });
            addNote.node.click();
            await jitter(500, 1100);

            // Note textarea — try multiple known selectors in order.
            const noteFieldHit = await waitForAnySelector([
                { sel: 'textarea[name="message"]', label: 'name_message' },
                { sel: 'textarea#custom-message', label: 'id_custom_message' },
                { sel: 'textarea[id^="custom-message"]', label: 'id_custom_message_prefix' },
                { sel: 'div[role="dialog"] textarea', label: 'dialog_textarea' },
            ], 6000);
            if (!noteFieldHit) return { status: 'failed', error: 'selector_drift:note_field', trace };
            const noteField = noteFieldHit.node;
            trace.push({ step: 'note_field', via: noteFieldHit.candidate.label });

            // Focus first so React listeners pick up the typing. Then type
            // in chunks so LinkedIn's input handlers fire naturally.
            noteField.focus();
            const message = (action.payload || '').slice(0, 300);
            for (const chunk of message.match(/.{1,8}/g) || []) {
                noteField.value = (noteField.value || '') + chunk;
                noteField.dispatchEvent(new Event('input', { bubbles: true }));
                await jitter(40, 120);
            }
            await jitter(700, 1400);

            // Send button — only inside a dialog/modal, so we don't
            // accidentally click an unrelated Send elsewhere on the page.
            const sendBtn = await waitForButton('send', (b) => {
                const al = (b.getAttribute && b.getAttribute('aria-label') || '').trim();
                const txt = (b.innerText || '').trim();
                const inDialog = b.closest && b.closest('[role="dialog"], div.artdeco-modal');
                if (!inDialog) return false;
                return /^Send/i.test(al) || /^Send$|^Send invitation$|^Send now$/i.test(txt);
            }, 6000);
            if (!sendBtn) return { status: 'failed', error: 'selector_drift:send', trace };
            trace.push({ step: 'send', via: sendBtn.label });
            sendBtn.node.click();
            await jitter(1500, 2500);

            if (isWeeklyCapModal()) return { status: 'failed', error: 'weekly_cap' };

            return { status: 'sent', trace };
        } catch (err) {
            return { status: 'failed', error: 'timeout', detail: String(err && err.message || err), trace };
        }
    }

    window.smartcvOutreach = { run };
})();
