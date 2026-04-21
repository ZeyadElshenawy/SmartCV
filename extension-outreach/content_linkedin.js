// Injected into the user's LinkedIn profile tab. Drives the connect-with-note
// flow with humanized pacing and reports outcome back via the function return.
//
// Outcome shape: { status: 'sent' | 'failed' | 'skipped', error?: string }
//
// `status` values must match what record_action_result accepts on the server.
// Errors we deliberately surface separately so the dispatcher and UI can react:
//   - 'already_connected' → skipped
//   - 'weekly_cap'        → failed (background.js pauses 24h on this)
//   - 'not_found'         → failed
//   - 'selector_drift'    → failed (LinkedIn DOM changed)
//   - 'timeout'           → failed

(function () {
    if (window.smartcvOutreach) return;  // re-injection guard

    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const jitter = (lo, hi) => sleep(lo + Math.random() * (hi - lo));

    function waitForSelector(selector, timeoutMs = 8000) {
        return new Promise((resolve, reject) => {
            const existing = document.querySelector(selector);
            if (existing) return resolve(existing);
            const observer = new MutationObserver(() => {
                const node = document.querySelector(selector);
                if (node) {
                    observer.disconnect();
                    resolve(node);
                }
            });
            observer.observe(document.body, { childList: true, subtree: true });
            setTimeout(() => {
                observer.disconnect();
                reject(new Error('timeout:' + selector));
            }, timeoutMs);
        });
    }

    // Detect "this profile cannot be connected with" / "page not found"
    function isProfileMissing() {
        const body = document.body.innerText || '';
        return /This profile is not available|Page not found/i.test(body);
    }

    // Detect the weekly invite-cap modal LinkedIn shows after ~100 invites/week.
    function isWeeklyCapModal() {
        const body = document.body.innerText || '';
        return /You.{0,3}re close to the weekly invitation limit|reached the weekly invitation limit/i.test(body);
    }

    async function findConnectButton() {
        // Direct "Connect" button in the profile action bar, or under the "More" menu.
        const direct = Array.from(document.querySelectorAll('button[aria-label]'))
            .find((b) => /^Connect/i.test(b.getAttribute('aria-label') || ''));
        if (direct) return direct;

        // Some profiles bury Connect under the "More actions" overflow.
        const more = Array.from(document.querySelectorAll('button[aria-label]'))
            .find((b) => /^More actions$/i.test(b.getAttribute('aria-label') || ''));
        if (!more) return null;
        more.click();
        await jitter(600, 1200);
        const overflowConnect = Array.from(document.querySelectorAll('div[role="button"]'))
            .find((d) => /Connect/i.test(d.innerText || ''));
        return overflowConnect || null;
    }

    async function isAlreadyConnected() {
        // LinkedIn replaces "Connect" with "Message" or shows "Pending" once connected
        const messageBtn = Array.from(document.querySelectorAll('button[aria-label]'))
            .find((b) => /^Message/i.test(b.getAttribute('aria-label') || ''));
        const pendingBtn = Array.from(document.querySelectorAll('button[aria-label]'))
            .find((b) => /^Pending/i.test(b.getAttribute('aria-label') || ''));
        return Boolean(messageBtn || pendingBtn);
    }

    async function run(action) {
        try {
            await jitter(1200, 2400);

            if (isProfileMissing()) return { status: 'failed', error: 'not_found' };
            if (isWeeklyCapModal()) return { status: 'failed', error: 'weekly_cap' };
            if (await isAlreadyConnected()) return { status: 'skipped', error: 'already_connected' };

            const connect = await findConnectButton();
            if (!connect) return { status: 'failed', error: 'selector_drift' };

            connect.click();
            await jitter(900, 1800);

            if (isWeeklyCapModal()) return { status: 'failed', error: 'weekly_cap' };

            // "Add a note" button in the connection-request modal
            const addNote = await waitForSelector('button[aria-label="Add a note"]', 6000)
                .catch(() => null);
            if (!addNote) return { status: 'failed', error: 'selector_drift' };
            addNote.click();
            await jitter(500, 1100);

            const noteField = await waitForSelector('textarea[name="message"], textarea#custom-message', 6000)
                .catch(() => null);
            if (!noteField) return { status: 'failed', error: 'selector_drift' };

            // Type the message in chunks so LinkedIn's input listeners fire naturally
            const message = (action.payload || '').slice(0, 300);
            for (const chunk of message.match(/.{1,8}/g) || []) {
                noteField.value = (noteField.value || '') + chunk;
                noteField.dispatchEvent(new Event('input', { bubbles: true }));
                await jitter(40, 120);
            }
            await jitter(700, 1400);

            const sendBtn = Array.from(document.querySelectorAll('button[aria-label]'))
                .find((b) => /^Send/i.test(b.getAttribute('aria-label') || ''));
            if (!sendBtn) return { status: 'failed', error: 'selector_drift' };
            sendBtn.click();
            await jitter(1500, 2500);

            if (isWeeklyCapModal()) return { status: 'failed', error: 'weekly_cap' };

            return { status: 'sent' };
        } catch (err) {
            return { status: 'failed', error: 'timeout', detail: String(err && err.message || err) };
        }
    }

    window.smartcvOutreach = { run };
})();
