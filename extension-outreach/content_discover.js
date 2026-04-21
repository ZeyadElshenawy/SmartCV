// Auto-runs on every linkedin.com/jobs/view/<id>/ page in the user's
// authenticated tab. Scrapes the two sections that LinkedIn shows only when
// logged in — "Meet the hiring team" and "People you can reach out to" —
// then POSTs the results to SmartCV via the paired token.
//
// We don't scrape on every page-load forever; once we've successfully
// pushed targets for a given job ID in this tab session, we mark a flag
// and skip subsequent scrapes for the same job ID. (If the user reloads
// the page we'll scrape again — that's fine, the server upsert dedupes.)
//
// Failures (token missing, host unreachable, LinkedIn DOM missing) are
// logged to console and silent to the user — discovery is a background
// helper, not a foreground action.

(function () {
    if (window.__smartcvDiscoveryRan) return;
    window.__smartcvDiscoveryRan = true;

    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

    function extractJobId() {
        const m = location.pathname.match(/\/jobs\/view\/(\d+)/);
        return m ? m[1] : null;
    }

    function extractHandle(href) {
        const m = (href || '').match(/\/in\/([^/?#]+)/);
        return m ? m[1].toLowerCase() : null;
    }

    function scrapeHiringTeam() {
        const out = [];
        const seen = new Set();
        // LinkedIn uses several class names over time; query all known ones.
        const selectors = [
            '.hirer-card__hirer-information a[href*="/in/"]',
            '[data-test-modal-id="hirer-modal"] a[href*="/in/"]',
            'a[data-tracking-control-name*="hirer"][href*="/in/"]',
        ];
        for (const sel of selectors) {
            for (const a of document.querySelectorAll(sel)) {
                const handle = extractHandle(a.getAttribute('href'));
                if (!handle || seen.has(handle)) continue;
                seen.add(handle);
                const card = a.closest('.hirer-card, [data-test-modal-id="hirer-modal"]') || a.parentElement;
                const name = a.textContent.trim() || handle;
                const roleNode = card ? card.querySelector('.hirer-card__hirer-job-title, .t-14') : null;
                const role = roleNode ? roleNode.textContent.trim() : '';
                out.push({ handle, name, role, source: 'hiring_team' });
            }
        }
        return out;
    }

    function scrapePeopleYouKnow() {
        const out = [];
        const seen = new Set();
        // Find sections whose heading mentions "reach out" or "people you" — LinkedIn
        // changes the wording slightly across A/B tests. Then collect /in/ links inside.
        const headings = Array.from(document.querySelectorAll('h2, h3, .text-heading-medium, .text-heading-small'));
        const sections = headings
            .filter((h) => /reach out|people you/i.test(h.textContent))
            .map((h) => h.closest('section, div[class*="card"]') || h.parentElement);
        for (const section of sections) {
            if (!section) continue;
            for (const a of section.querySelectorAll('a[href*="/in/"]')) {
                const handle = extractHandle(a.getAttribute('href'));
                if (!handle || seen.has(handle)) continue;
                seen.add(handle);
                const name = a.textContent.trim() || handle;
                // Try to find a role/subtitle near the name. LinkedIn varies wildly here.
                let role = '';
                const card = a.closest('li, div[class*="card"], div[class*="entity"]') || a.parentElement;
                if (card) {
                    const subtitle = Array.from(card.querySelectorAll('span, p, div'))
                        .map((n) => n.textContent.trim())
                        .find((t) => t && t !== name && t.length > 5 && t.length < 140);
                    if (subtitle) role = subtitle;
                }
                out.push({ handle, name, role, source: 'people_you_know' });
            }
        }
        return out;
    }

    async function pushToSmartCV(linkedinJobId, targets) {
        // Chrome's Private Network Access policy blocks linkedin.com (public
        // origin) from fetch()ing 127.0.0.1 directly. Send the data to the
        // extension's background service worker via chrome.runtime instead —
        // the SW runs as chrome-extension://... origin and can reach loopback.
        try {
            const res = await chrome.runtime.sendMessage({
                type: 'pushDiscovery',
                linkedinJobId,
                targets,
            });
            console.log('[smartcv-discovery] pushed', targets.length, 'targets ->', res);
        } catch (err) {
            console.log('[smartcv-discovery] push failed:', err);
        }
    }

    async function run() {
        const jobId = extractJobId();
        if (!jobId) return;

        // LinkedIn hydrates these sections async. Wait up to ~6s for them.
        for (let i = 0; i < 12; i++) {
            const team = scrapeHiringTeam();
            const peers = scrapePeopleYouKnow();
            if (team.length || peers.length) {
                await pushToSmartCV(jobId, [...team, ...peers]);
                return;
            }
            await sleep(500);
        }
        // Even if both came back empty, push an empty list so the SmartCV side
        // can mark "scrape attempted, nothing visible" and not endlessly poll.
        await pushToSmartCV(jobId, []);
    }

    run();
})();
