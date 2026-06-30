/**
 * Toast notifications and session expiry banner for the Guardian parent console.
 */
(function () {
    'use strict';

    const TOAST_VISIBLE_MS = 10000;
    const SESSION_CHECK_MS = 30000;
    let toastHideTimer = null;

    function i18n(key, params) {
        if (typeof window.guardianI18n === 'function') {
            return window.guardianI18n(key, params);
        }
        const data = document.getElementById('i18n-data');
        const catalog = data ? JSON.parse(data.textContent || '{}') : {};
        let text = catalog[key] || key;
        if (params) {
            Object.keys(params).forEach((name) => {
                text = text.replace(new RegExp('\\{' + name + '\\}', 'g'), String(params[name]));
            });
        }
        return text;
    }

    function showNotification(message, type) {
        const toast = document.getElementById('notification-toast');
        if (!toast) return;
        const messageEl = toast.querySelector('.toast-message');
        const iconEl = toast.querySelector('.toast-icon path');
        if (!messageEl || !iconEl) return;

        messageEl.textContent = message;
        toast.className = 'notification-toast ' + (type || 'success');

        if (type === 'success') {
            iconEl.setAttribute('d', 'M16 8A8 8 0 1 1 0 8a8 8 0 0 1 16 0zm-3.97-3.03a.75.75 0 0 0-1.08.022L7.477 9.417 5.384 7.323a.75.75 0 0 0-1.06 1.061L6.97 11.03a.75.75 0 0 0 1.079-.02l3.992-4.99a.75.75 0 0 0-.01-1.05z');
        } else if (type === 'error') {
            iconEl.setAttribute('d', 'M16 8A8 8 0 1 1 0 8a8 8 0 0 1 16 0zM5.354 4.646a.5.5 0 1 0-.708.708L7.293 8l-2.647 2.646a.5.5 0 0 0 .708.708L8 8.707l2.646 2.647a.5.5 0 0 0 .708-.708L8.707 8l2.647-2.646a.5.5 0 0 0-.708-.708L8 7.293 5.354 4.646z');
        }

        toast.classList.add('show');
        if (toastHideTimer) {
            window.clearTimeout(toastHideTimer);
        }
        toastHideTimer = window.setTimeout(() => toast.classList.remove('show'), TOAST_VISIBLE_MS);
    }

    function readSessionExpiryConfig() {
        const body = document.body;
        const raw = body && body.getAttribute('data-session-expires-at');
        if (!raw) return null;
        const expiresAt = Number.parseInt(raw, 10);
        if (!Number.isFinite(expiresAt)) return null;
        const warnSeconds = Number.parseInt(body.getAttribute('data-session-warn-seconds') || '300', 10);
        return {
            expiresAt,
            warnSeconds: Number.isFinite(warnSeconds) ? warnSeconds : 300,
        };
    }

    function updateSessionBanner(config) {
        const banner = document.getElementById('session-expiry-banner');
        const messageEl = document.getElementById('session-expiry-message');
        if (!banner || !messageEl || !config) return;

        const secondsLeft = config.expiresAt - Math.floor(Date.now() / 1000);
        if (secondsLeft > config.warnSeconds) {
            banner.classList.add('d-none');
            return;
        }

        const minutes = Math.max(1, Math.ceil(secondsLeft / 60));
        messageEl.textContent = i18n('session_expiring', { minutes });
        banner.classList.remove('d-none');
    }

    function extendSession(config) {
        const button = document.getElementById('session-extend-btn');
        if (button) button.disabled = true;

        return fetch('/api/session/extend', { method: 'POST' })
            .then((response) => response.json().then((data) => ({ response, data })))
            .then(({ response, data }) => {
                if (!response.ok || !data.success) {
                    throw new Error(data.message || i18n('session_extend_failed'));
                }
                if (data.expires_at) {
                    config.expiresAt = data.expires_at;
                    document.body.setAttribute('data-session-expires-at', String(data.expires_at));
                }
                const banner = document.getElementById('session-expiry-banner');
                if (banner) banner.classList.add('d-none');
                showNotification(data.message || i18n('session_extended'), 'success');
            })
            .catch((error) => {
                showNotification(error.message || i18n('session_extend_failed'), 'error');
            })
            .finally(() => {
                if (button) button.disabled = false;
            });
    }

    function initSessionExpiryBanner() {
        const config = readSessionExpiryConfig();
        if (!config) return;

        const button = document.getElementById('session-extend-btn');
        if (button) {
            button.addEventListener('click', () => extendSession(config));
        }

        const tick = () => updateSessionBanner(config);
        tick();
        window.setInterval(tick, SESSION_CHECK_MS);
    }

    window.showNotification = showNotification;
    window.GUARDIAN_TOAST_VISIBLE_MS = TOAST_VISIBLE_MS;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSessionExpiryBanner);
    } else {
        initSessionExpiryBanner();
    }
})();
