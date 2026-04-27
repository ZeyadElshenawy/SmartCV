// Status states (mirror background.js).
const STATUS_OK = 'ok';
const STATUS_NOT_PAIRED = 'not_paired';
const STATUS_AUTH_FAILED = 'auth_failed';
const STATUS_RATE_LIMITED = 'rate_limited';
const STATUS_SERVER_ERROR = 'server_error';
const STATUS_OFFLINE = 'offline';
const STATUS_PAUSED_CAP = 'paused_cap';

function relTime(ms) {
    const diff = Math.max(0, Date.now() - ms);
    if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return `${Math.floor(diff / 86_400_000)}d ago`;
}

function pairedRow(host, token) {
    const dot = document.getElementById('paired-dot');
    const text = document.getElementById('paired-text');
    text.textContent = '';
    if (host && token) {
        dot.className = 'dot dot-green';
        const code = document.createElement('code');
        code.textContent = host;
        text.appendChild(document.createTextNode('Paired with '));
        text.appendChild(code);
    } else {
        dot.className = 'dot dot-red';
        text.textContent = 'Not paired — open Options.';
    }
}

// Render a banner explaining the current health state. The banner is the
// thing the user sees if they wonder "why isn't it working?" — keep the
// copy actionable, never just an error code.
function renderBanner({ smartcv_status, smartcv_hard_paused_until, smartcv_rate_limit_until }) {
    const slot = document.getElementById('banner-slot');
    slot.innerHTML = '';
    // Hard pause (LinkedIn weekly cap) takes precedence over everything else.
    if (typeof smartcv_hard_paused_until === 'number' && Date.now() < smartcv_hard_paused_until) {
        const mins = Math.round((smartcv_hard_paused_until - Date.now()) / 60_000);
        const banner = document.createElement('div');
        banner.className = 'banner banner-warn';
        banner.textContent = `LinkedIn weekly invite cap hit. Paused ${mins} min — automation will resume once LinkedIn lets us through.`;
        slot.appendChild(banner);
        document.getElementById('paired-dot').className = 'dot dot-amber';
        return;
    }
    // Soft rate-limit backoff (server told us to cool down, e.g., Groq 429).
    if (typeof smartcv_rate_limit_until === 'number' && Date.now() < smartcv_rate_limit_until) {
        const mins = Math.round((smartcv_rate_limit_until - Date.now()) / 60_000);
        const banner = document.createElement('div');
        banner.className = 'banner banner-warn';
        banner.textContent = `Server is rate-limited. Backing off for ${mins} min.`;
        slot.appendChild(banner);
        document.getElementById('paired-dot').className = 'dot dot-amber';
        return;
    }
    if (!smartcv_status) return;
    const { state, detail, at } = smartcv_status;
    if (state === STATUS_AUTH_FAILED) {
        const banner = document.createElement('div');
        banner.className = 'banner banner-error';
        banner.innerHTML = `Token rejected by SmartCV. <a href="#" id="reopen-options">Open Options</a> and re-paste the token from <code>/profiles/extension/pair/</code>.`;
        slot.appendChild(banner);
        const link = banner.querySelector('#reopen-options');
        if (link) link.addEventListener('click', (e) => { e.preventDefault(); chrome.runtime.openOptionsPage(); });
        document.getElementById('paired-dot').className = 'dot dot-red';
        return;
    }
    if (state === STATUS_OFFLINE) {
        const banner = document.createElement('div');
        banner.className = 'banner banner-error';
        banner.textContent = `Can't reach SmartCV at the configured host. Check that the server is running and the host in Options is correct.${detail ? ` (${detail})` : ''}`;
        slot.appendChild(banner);
        document.getElementById('paired-dot').className = 'dot dot-red';
        return;
    }
    if (state === STATUS_SERVER_ERROR) {
        const banner = document.createElement('div');
        banner.className = 'banner banner-error';
        banner.textContent = `SmartCV server returned an error${detail ? ` (${detail})` : ''}. Will retry shortly.`;
        slot.appendChild(banner);
        document.getElementById('paired-dot').className = 'dot dot-amber';
        return;
    }
    // STATUS_OK / STATUS_NOT_PAIRED / STATUS_RATE_LIMITED handled by paired
    // dot and the dedicated banners above; nothing else to render.
    if (state === STATUS_OK && at) {
        // Tiny "last polled" hint under the paired row — no banner needed.
        const hint = document.createElement('div');
        hint.className = 'muted';
        hint.style.fontSize = '11px';
        hint.style.marginTop = '-4px';
        hint.textContent = `Last poll: ${relTime(at)}${detail ? ` — ${detail}` : ''}`;
        slot.appendChild(hint);
    }
}

function renderHistory(history) {
    const slot = document.getElementById('history-slot');
    slot.innerHTML = '';
    if (!Array.isArray(history) || history.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = 'No actions yet.';
        slot.appendChild(empty);
        return;
    }
    for (const entry of history) {
        const row = document.createElement('div');
        row.className = 'history-row';
        const status = document.createElement('span');
        status.className = `history-status history-status-${entry.status}`;
        status.textContent = entry.status;
        const target = document.createElement('span');
        target.className = 'history-target';
        target.textContent = entry.target_name || entry.target_handle || '?';
        if (entry.error) {
            target.title = entry.error;
            target.textContent += ` · ${entry.error}`;
        }
        const time = document.createElement('span');
        time.className = 'history-time';
        time.textContent = relTime(entry.at);
        row.appendChild(status);
        row.appendChild(target);
        row.appendChild(time);
        slot.appendChild(row);
    }
}

async function refresh() {
    const data = await chrome.storage.local.get([
        'smartcv_host', 'smartcv_token',
        'smartcv_hard_paused_until', 'smartcv_rate_limit_until',
        'smartcv_status', 'smartcv_history',
    ]);
    pairedRow(data.smartcv_host, data.smartcv_token);
    renderBanner(data);
    renderHistory(data.smartcv_history);
}

document.getElementById('open-options').addEventListener('click', (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
});

document.getElementById('poll-now').addEventListener('click', async () => {
    await chrome.alarms.create('smartcv_outreach_poll', { delayInMinutes: 0.05 });
    setTimeout(refresh, 1500);
});

document.addEventListener('DOMContentLoaded', refresh);
