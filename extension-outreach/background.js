// Service worker: poll SmartCV for queued outreach actions, then drive the
// LinkedIn UI inside the user's own tab via a content script.
//
// Cadence: chrome.alarms fires every ~90s with ±20s jitter. We do not chain
// setTimeout because MV3 service workers can be terminated and revived; alarms
// survive the worker restart.

const POLL_ALARM = 'smartcv_outreach_poll';
const HARD_PAUSE_KEY = 'smartcv_hard_paused_until';   // ms epoch, set on weekly_cap
const STATUS_KEY = 'smartcv_status';                  // last poll outcome (object)
const HISTORY_KEY = 'smartcv_history';                // last N action results
const HISTORY_MAX = 10;
const RATE_LIMIT_BACKOFF_KEY = 'smartcv_rate_limit_until'; // ms epoch, soft pause

// Health states we surface in the popup. Anything other than `ok` means
// something the user might want to know about.
const STATUS_OK = 'ok';
const STATUS_NOT_PAIRED = 'not_paired';
const STATUS_AUTH_FAILED = 'auth_failed';      // 401 → token revoked or wrong host
const STATUS_RATE_LIMITED = 'rate_limited';    // 429 → server is throttling us
const STATUS_SERVER_ERROR = 'server_error';    // 5xx → server hiccup
const STATUS_OFFLINE = 'offline';              // fetch threw → network down or wrong host
const STATUS_PAUSED_CAP = 'paused_cap';        // LinkedIn weekly cap tripped

async function getConfig() {
    const cfg = await chrome.storage.local.get(['smartcv_host', 'smartcv_token']);
    return {
        host: (cfg.smartcv_host || '').replace(/\/+$/, ''),
        token: cfg.smartcv_token || '',
    };
}

async function isHardPaused() {
    const stored = await chrome.storage.local.get([HARD_PAUSE_KEY]);
    const until = stored[HARD_PAUSE_KEY];
    return typeof until === 'number' && Date.now() < until;
}

async function isRateLimitBackoff() {
    const stored = await chrome.storage.local.get([RATE_LIMIT_BACKOFF_KEY]);
    const until = stored[RATE_LIMIT_BACKOFF_KEY];
    return typeof until === 'number' && Date.now() < until;
}

// Persist the current health state so the popup can render without re-doing
// any network work. Always pass a plain object — the popup reads it raw.
async function setStatus(state, detail = '') {
    await chrome.storage.local.set({
        [STATUS_KEY]: { state, detail, at: Date.now() },
    });
}

// Append a per-action outcome to the rolling history (most recent first,
// truncated to HISTORY_MAX). The popup shows the last few so the user can
// confirm the extension is actually doing something.
async function appendHistory(entry) {
    const stored = await chrome.storage.local.get([HISTORY_KEY]);
    const prev = Array.isArray(stored[HISTORY_KEY]) ? stored[HISTORY_KEY] : [];
    const next = [entry, ...prev].slice(0, HISTORY_MAX);
    await chrome.storage.local.set({ [HISTORY_KEY]: next });
}

function scheduleNextPoll(delayMinutes) {
    // Default: 90s ± 20s jitter, expressed in minutes for chrome.alarms.
    // Callers can override (e.g., back off to 5min after a 429).
    const minutes = typeof delayMinutes === 'number'
        ? delayMinutes
        : (90 + (Math.random() * 40 - 20)) / 60;
    chrome.alarms.create(POLL_ALARM, { delayInMinutes: minutes });
}

chrome.runtime.onInstalled.addListener(() => scheduleNextPoll());
chrome.runtime.onStartup.addListener(() => scheduleNextPoll());

// Bridge for content_discover.js — content scripts inherit the page's origin
// (linkedin.com) which Chrome's Private Network Access blocks from reaching
// 127.0.0.1. The service worker runs as the extension origin and can.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === 'pushDiscovery') {
        (async () => {
            const { host, token } = await getConfig();
            if (!host || !token) {
                sendResponse({ ok: false, error: 'not_paired' });
                return;
            }
            try {
                const res = await fetch(`${host}/profiles/api/outreach/discovery/push/`, {
                    method: 'POST',
                    headers: {
                        'Authorization': `Token ${token}`,
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        linkedin_job_id: msg.linkedinJobId,
                        targets: msg.targets || [],
                    }),
                });
                const data = await res.json().catch(() => ({}));
                sendResponse({ ok: res.ok, status: res.status, data });
            } catch (err) {
                sendResponse({ ok: false, error: String(err && err.message || err) });
            }
        })();
        return true;  // keep the message channel open for async sendResponse
    }
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm.name !== POLL_ALARM) return;
    let nextDelayMinutes;  // undefined → use default jitter
    try {
        await pollOnce();
    } catch (err) {
        // pollOnce already classified the failure via setStatus; if it
        // returned a backoff hint we honor it for the NEXT alarm.
        nextDelayMinutes = err && typeof err.backoffMinutes === 'number'
            ? err.backoffMinutes
            : undefined;
        console.error('[smartcv-outreach] poll failed:', err);
    } finally {
        scheduleNextPoll(nextDelayMinutes);
    }
});

// A typed error so the alarm handler knows whether to back off the next poll.
function fail(state, detail, backoffMinutes) {
    const err = new Error(detail || state);
    err.smartcvState = state;
    err.backoffMinutes = backoffMinutes;
    return err;
}

async function pollOnce() {
    const { host, token } = await getConfig();
    if (!host || !token) {
        await setStatus(STATUS_NOT_PAIRED);
        return;
    }
    if (await isHardPaused()) {
        await setStatus(STATUS_PAUSED_CAP, 'LinkedIn weekly cap');
        return;
    }
    if (await isRateLimitBackoff()) {
        // Server told us to slow down; honor the cooldown without polling.
        await setStatus(STATUS_RATE_LIMITED, 'cooling down');
        return;
    }

    let res;
    try {
        res = await fetch(`${host}/profiles/api/outreach/next`, {
            headers: { 'Authorization': `Token ${token}` },
        });
    } catch (err) {
        // Network down, host unreachable, DNS fail, etc. Don't hammer the
        // host on every alarm — back off to 5 minutes until it recovers.
        await setStatus(STATUS_OFFLINE, String(err && err.message || err));
        throw fail(STATUS_OFFLINE, 'fetch failed', 5);
    }

    if (res.status === 401 || res.status === 403) {
        // Token revoked, host pointed at the wrong user, or pairing never
        // completed properly. The user has to re-pair; back off hard so
        // we don't burn rate budget while waiting for them to fix it.
        await setStatus(STATUS_AUTH_FAILED, 'token rejected');
        throw fail(STATUS_AUTH_FAILED, `auth ${res.status}`, 30);
    }
    if (res.status === 429) {
        // Server (or an upstream like Groq) is throttling. Honor the
        // Retry-After header if provided; otherwise default 30 min.
        const retryAfter = parseInt(res.headers.get('Retry-After') || '0', 10);
        const minutes = retryAfter > 0 ? Math.min(60, retryAfter / 60) : 30;
        await chrome.storage.local.set({
            [RATE_LIMIT_BACKOFF_KEY]: Date.now() + minutes * 60 * 1000,
        });
        await setStatus(STATUS_RATE_LIMITED, `cooling for ${Math.round(minutes)}m`);
        throw fail(STATUS_RATE_LIMITED, '429', minutes);
    }
    if (res.status >= 500) {
        await setStatus(STATUS_SERVER_ERROR, `${res.status}`);
        throw fail(STATUS_SERVER_ERROR, `${res.status}`, 5);
    }
    if (res.status === 204) {
        await setStatus(STATUS_OK, 'queue empty');
        return;
    }
    if (!res.ok) {
        await setStatus(STATUS_SERVER_ERROR, `unexpected ${res.status}`);
        throw fail(STATUS_SERVER_ERROR, `unexpected ${res.status}`, 5);
    }

    const action = await res.json();
    await setStatus(STATUS_OK, `dispatching ${action.target_handle || ''}`);
    await dispatchAction(action);
    await setStatus(STATUS_OK, 'idle');
}

async function dispatchAction(action) {
    // Find or open a LinkedIn tab on the target's profile
    const profileUrl = action.profile_url;
    let [tab] = await chrome.tabs.query({ url: 'https://www.linkedin.com/*' });
    if (!tab) {
        tab = await chrome.tabs.create({ url: profileUrl, active: false });
    } else {
        await chrome.tabs.update(tab.id, { url: profileUrl, active: false });
    }

    // Wait for the page to load before injecting
    await waitForTabComplete(tab.id, 15000);

    const [{ result } = {}] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ['content_linkedin.js'],
    }).then(() => chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: (payload) => window.smartcvOutreach.run(payload),
        args: [action],
    }));

    const outcome = result || { status: 'failed', error: 'no_content_script_result' };
    if (outcome.status === 'failed' && outcome.error === 'weekly_cap') {
        await chrome.storage.local.set({ [HARD_PAUSE_KEY]: Date.now() + 24 * 60 * 60 * 1000 });
    }
    // Record what just happened for the popup history panel.
    await appendHistory({
        target_handle: action.target_handle || '',
        target_name: action.target_name || '',
        status: outcome.status || 'failed',
        error: outcome.error || '',
        at: Date.now(),
    });
    await reportResult(action.id, outcome);
}

async function reportResult(actionId, outcome) {
    const { host, token } = await getConfig();
    await fetch(`${host}/profiles/api/outreach/result/${actionId}/`, {
        method: 'POST',
        headers: {
            'Authorization': `Token ${token}`,
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(outcome),
    });
}

function waitForTabComplete(tabId, timeoutMs) {
    return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
            chrome.tabs.onUpdated.removeListener(listener);
            reject(new Error('tab_load_timeout'));
        }, timeoutMs);
        function listener(updatedId, info) {
            if (updatedId === tabId && info.status === 'complete') {
                clearTimeout(timeout);
                chrome.tabs.onUpdated.removeListener(listener);
                resolve();
            }
        }
        chrome.tabs.onUpdated.addListener(listener);
    });
}
