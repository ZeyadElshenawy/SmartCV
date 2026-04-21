async function refresh() {
    const { smartcv_host, smartcv_token, smartcv_hard_paused_until } =
        await chrome.storage.local.get(['smartcv_host', 'smartcv_token', 'smartcv_hard_paused_until']);
    const dot = document.getElementById('paired-dot');
    const text = document.getElementById('paired-text');
    text.textContent = '';
    if (smartcv_host && smartcv_token) {
        dot.className = 'dot dot-green';
        const code = document.createElement('code');
        code.textContent = smartcv_host;
        text.appendChild(document.createTextNode('Paired with '));
        text.appendChild(code);
    } else {
        dot.className = 'dot dot-red';
        text.textContent = 'Not paired — open Options.';
    }
    const pausedEl = document.getElementById('paused-text');
    if (typeof smartcv_hard_paused_until === 'number' && Date.now() < smartcv_hard_paused_until) {
        const mins = Math.round((smartcv_hard_paused_until - Date.now()) / 60000);
        pausedEl.textContent = `Paused for weekly cap (${mins} min remaining).`;
    } else {
        pausedEl.textContent = '';
    }
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
