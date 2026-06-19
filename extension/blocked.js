// blocked.js — Logic for the Guardian registration-blocked page.
// Reads `url` and `domain` from the query string, allows requesting approval
// and polling for approval status via the native messaging host.

// ============================================================
// State
// ============================================================

const params = new URLSearchParams(window.location.search);
const originalUrl = params.get('url') || '';
const domain = params.get('domain') || window.location.hostname;

/** @type {'idle' | 'pending' | 'approved' | 'denied'} */
let currentStatus = 'idle';
let pollInterval = null;
let requestSent = false;

// ============================================================
// i18n
// ============================================================

function msg(key) {
    return guardianExtI18n(key);
}

function applyPageI18n() {
    document.title = msg('blockedPageTitle');

    const textMap = {
        'header-title': 'blockedHeaderTitle',
        'header-subtitle': 'blockedHeaderSubtitle',
        'blocked-heading': 'blockedHeading',
        'blocked-sub': 'blockedSub',
        'btn-check-label': 'btnCheckStatus',
        'footer-auto-check': 'footerAutoCheck',
    };

    for (const [elementId, messageKey] of Object.entries(textMap)) {
        const el = document.getElementById(elementId);
        if (el) {
            el.textContent = msg(messageKey);
        }
    }

    const requestLabel = document.getElementById('btn-request-label');
    if (requestLabel) {
        requestLabel.textContent = msg('btnRequestApproval');
    }

    const statusText = document.getElementById('status-text');
    if (statusText && currentStatus === 'idle') {
        statusText.textContent = msg('statusIdle');
    }
}

// ============================================================
// DOM helpers
// ============================================================

function setStatusBanner(state, message) {
    const banner = document.getElementById('status-banner');
    const text = document.getElementById('status-text');
    if (!banner || !text) return;

    banner.className = `status-banner ${state}`;
    text.textContent = message;
    currentStatus = state;
}

function setButtonLoading(buttonId, loading, labelId, labelText) {
    const btn = document.getElementById(buttonId);
    if (!btn) return;
    btn.disabled = loading;
    if (labelId) {
        const label = document.getElementById(labelId);
        if (label) {
            label.innerHTML = loading
                ? `<span class="spinner"></span> ${msg('sending')}`
                : labelText;
        }
    }
}

// ============================================================
// Initialise page
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
    applyPageI18n();

    // Display the blocked domain
    const domainText = document.getElementById('domain-text');
    if (domainText) {
        domainText.textContent = domain || msg('unknownSite');
    }

    // Disable Request Approval if we're already pending or approved
    if (requestSent) {
        document.getElementById('btn-request').disabled = true;
    }

    // Start polling
    startPolling();

    // Also check when the tab regains focus
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            checkStatus();
        }
    });
});

// ============================================================
// Request approval
// ============================================================

function requestApproval() {
    if (requestSent) return;

    const requestLabel = msg('btnRequestApproval');
    setButtonLoading('btn-request', true, 'btn-request-label', requestLabel);

    chrome.runtime.sendMessage(
        { type: 'REQUEST_REGISTRATION', domain: domain },
        (response) => {
            requestSent = true;
            setButtonLoading('btn-request', false, 'btn-request-label', requestLabel);

            // Disable button — request has been sent
            const btn = document.getElementById('btn-request');
            if (btn) btn.disabled = true;

            if (chrome.runtime.lastError || !response) {
                setStatusBanner('pending', msg('statusPendingSent'));
            } else if (response.success === false) {
                setStatusBanner('idle', msg('statusSendFailed'));
                if (btn) btn.disabled = false;
                requestSent = false;
            } else {
                setStatusBanner('pending', msg('statusPendingSent'));
            }
        }
    );
}

// ============================================================
// Check current approval status
// ============================================================

function checkStatus() {
    if (currentStatus === 'approved') return; // No need to keep checking

    chrome.runtime.sendMessage(
        { type: 'CHECK_REGISTRATION', domain: domain, url: originalUrl },
        (response) => {
            if (chrome.runtime.lastError || !response) {
                // Agent unavailable — keep current state, don't disrupt UI
                return;
            }

            if (response.allowed === true) {
                handleApproved();
            } else if (response.pending === true) {
                setStatusBanner('pending', msg('statusPendingWaiting'));
            }
            // If still blocked and not pending, keep current banner
        }
    );
}

// ============================================================
// Approved → redirect
// ============================================================

function handleApproved() {
    stopPolling();
    setStatusBanner('approved', msg('statusApproved'));

    // Brief delay so the user sees the approved banner
    setTimeout(() => {
        if (originalUrl) {
            window.location.href = originalUrl;
        } else {
            window.history.back();
        }
    }, 1500);
}

// ============================================================
// Polling
// ============================================================

function startPolling() {
    if (pollInterval) return;
    // First check immediately, then every 10 seconds
    checkStatus();
    pollInterval = setInterval(checkStatus, 10_000);
}

function stopPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
}
