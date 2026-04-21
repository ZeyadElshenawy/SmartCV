async function load() {
    const cfg = await chrome.storage.local.get(['smartcv_host', 'smartcv_token']);
    document.getElementById('host').value = cfg.smartcv_host || 'http://127.0.0.1:8000';
    document.getElementById('token').value = cfg.smartcv_token || '';
}

document.getElementById('save').addEventListener('click', async () => {
    const host = document.getElementById('host').value.trim().replace(/\/+$/, '');
    const token = document.getElementById('token').value.trim();
    await chrome.storage.local.set({ smartcv_host: host, smartcv_token: token });
    const ok = document.getElementById('ok');
    ok.style.display = 'inline';
    setTimeout(() => (ok.style.display = 'none'), 2000);
});

document.addEventListener('DOMContentLoaded', load);
