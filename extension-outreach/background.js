// Service worker: poll SmartCV for queued outreach actions, then drive the
// LinkedIn UI inside the user's own tab via a content script.
//
// Cadence: chrome.alarms fires every ~90s with ±20s jitter. We do not chain
// setTimeout because MV3 service workers can be terminated and revived; alarms
// survive the worker restart.

const POLL_ALARM = 'smartcv_outreach_poll';
const HARD_PAUSE_KEY = 'smartcv_hard_paused_until';   // ms epoch, set on weekly_cap

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

function scheduleNextPoll() {
    // 90s ± 20s jitter, expressed in minutes for chrome.alarms
    const minutes = (90 + (Math.random() * 40 - 20)) / 60;
    chrome.alarms.create(POLL_ALARM, { delayInMinutes: minutes });
}

chrome.runtime.onInstalled.addListener(() => scheduleNextPoll());
chrome.runtime.onStartup.addListener(() => scheduleNextPoll());

chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm.name !== POLL_ALARM) return;
    try {
        await pollOnce();
    } catch (err) {
        console.error('[smartcv-outreach] poll failed:', err);
    } finally {
        scheduleNextPoll();
    }
});

async function pollOnce() {
    const { host, token } = await getConfig();
    if (!host || !token) return;        // not paired yet
    if (await isHardPaused()) return;   // weekly cap tripped

    const res = await fetch(`${host}/profiles/api/outreach/next`, {
        headers: { 'Authorization': `Token ${token}` },
    });
    if (res.status === 204) return;     // nothing to do
    if (!res.ok) throw new Error(`next returned ${res.status}`);

    const action = await res.json();
    await dispatchAction(action);
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
