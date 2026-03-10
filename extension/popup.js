document.getElementById('extract-btn').addEventListener('click', async () => {
    const btn = document.getElementById('extract-btn');
    const statusDiv = document.getElementById('status');
    const errorDiv = document.getElementById('error');
    const apiKey = document.getElementById('api-key').value;

    if (!apiKey) {
        errorDiv.textContent = '✗ Please enter your SmartCV API Key.';
        errorDiv.style.display = 'block';
        return;
    }

    // Reset UI
    btn.innerHTML = '<div class="loader"></div> Extracting...';
    btn.disabled = true;
    statusDiv.style.display = 'none';
    errorDiv.style.display = 'none';

    try {
        // Get active tab
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

        if (!tab) throw new Error("Could not find active tab");

        // Execute script to grab page data
        const [{ result }] = await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            function: extractJobData,
        });

        if (!result) throw new Error("Could not extract job data from page.");

        result.url = tab.url;

        // Save key for future
        chrome.storage.local.set({ 'smartcv_key': apiKey });

        // Send to Django backend
        // Note: In production, URL should point to your hosted app
        const response = await fetch('http://localhost:8000/api/v1/extension/save-job/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Key': apiKey,
            },
            body: JSON.stringify(result)
        });

        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }

        const data = await response.json();

        // Success UI
        btn.textContent = 'Extract & Save Job';
        btn.disabled = false;
        statusDiv.style.display = 'block';

    } catch (err) {
        console.error("Extension Error:", err);
        btn.textContent = 'Extract & Save Job';
        btn.disabled = false;
        errorDiv.textContent = `✗ Failed: ${err.message}`;
        errorDiv.style.display = 'block';
    }
});

// Load saved key on open
document.addEventListener('DOMContentLoaded', () => {
    chrome.storage.local.get(['smartcv_key'], function (result) {
        if (result.smartcv_key) {
            document.getElementById('api-key').value = result.smartcv_key;
        }
    });
});

// The function injected into the page to scrape data
function extractJobData() {
    let title = "";
    let company = "";
    let description = "";

    // Basic heuristic scraping logic (works for LinkedIn/Indeed)
    if (window.location.hostname.includes('linkedin')) {
        title = document.querySelector('.top-card-layout__title')?.innerText ||
            document.querySelector('.job-details-jobs-unified-top-card__job-title')?.innerText || "";

        company = document.querySelector('.topcard__org-name-link')?.innerText ||
            document.querySelector('.job-details-jobs-unified-top-card__company-name')?.innerText || "";

        description = document.querySelector('.description__text')?.innerText ||
            document.querySelector('.jobs-description__content')?.innerText || "";
    } else if (window.location.hostname.includes('indeed')) {
        title = document.querySelector('.jobsearch-JobInfoHeader-title')?.innerText || "";
        company = document.querySelector('[data-company-name="true"]')?.innerText || "";
        description = document.querySelector('#jobDescriptionText')?.innerText || "";
    } else {
        // Generic fallback
        title = document.title;
        description = document.body.innerText.substring(0, 5000); // Grab first chunk of text
    }

    return {
        title: title.trim(),
        company: company.trim(),
        description: description.trim()
    };
}
