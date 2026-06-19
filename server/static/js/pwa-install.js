/**
 * PWA service worker registration and add-to-homescreen prompts.
 */
(function () {
    'use strict';

    const DISMISS_KEY = 'guardian-a2hs-dismissed';
    const DISMISS_DAYS = 7;

    let deferredPrompt = null;

    function isStandalone() {
        return window.matchMedia('(display-mode: standalone)').matches
            || window.navigator.standalone === true;
    }

    function isIOS() {
        return /iPad|iPhone|iPod/.test(navigator.userAgent)
            || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    }

    function isMobileViewport() {
        return window.matchMedia('(max-width: 768px)').matches;
    }

    function wasDismissedRecently() {
        const raw = localStorage.getItem(DISMISS_KEY);
        if (!raw) {
            return false;
        }
        const dismissedAt = parseInt(raw, 10);
        if (Number.isNaN(dismissedAt)) {
            return false;
        }
        const cooldownMs = DISMISS_DAYS * 24 * 60 * 60 * 1000;
        return Date.now() - dismissedAt < cooldownMs;
    }

    function dismissBanner() {
        localStorage.setItem(DISMISS_KEY, String(Date.now()));
        hideBanner();
    }

    function hideBanner() {
        const banner = document.getElementById('guardian-a2hs-banner');
        if (banner) {
            banner.hidden = true;
        }
    }

    function showBanner(mode) {
        if (isStandalone() || wasDismissedRecently()) {
            return;
        }

        const banner = document.getElementById('guardian-a2hs-banner');
        const message = document.getElementById('guardian-a2hs-message');
        const installBtn = document.getElementById('guardian-a2hs-install');
        if (!banner) {
            return;
        }

        if (mode === 'ios') {
            if (message) {
                message.textContent = 'Tap Share, then "Add to Home Screen" for quick access.';
            }
            if (installBtn) {
                installBtn.hidden = true;
            }
        } else if (mode === 'android') {
            if (message) {
                message.textContent = 'Quick access to routines and family dialogue.';
            }
            if (installBtn) {
                installBtn.hidden = false;
            }
        }

        banner.hidden = false;
    }

    function registerServiceWorker() {
        if (!('serviceWorker' in navigator)) {
            return;
        }
        navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((err) => {
            console.warn('Service worker registration failed', err);
        });
    }

    window.addEventListener('beforeinstallprompt', (event) => {
        event.preventDefault();
        deferredPrompt = event;
        showBanner('android');
    });

    document.addEventListener('DOMContentLoaded', () => {
        registerServiceWorker();

        const dismissBtn = document.getElementById('guardian-a2hs-dismiss');
        const installBtn = document.getElementById('guardian-a2hs-install');

        if (dismissBtn) {
            dismissBtn.addEventListener('click', dismissBanner);
        }

        if (installBtn) {
            installBtn.addEventListener('click', async () => {
                if (!deferredPrompt) {
                    return;
                }
                deferredPrompt.prompt();
                await deferredPrompt.userChoice;
                deferredPrompt = null;
                hideBanner();
            });
        }

        if (isStandalone()) {
            hideBanner();
            return;
        }

        if (isIOS() && isMobileViewport()) {
            showBanner('ios');
        }
    });

    window.addEventListener('appinstalled', () => {
        deferredPrompt = null;
        hideBanner();
    });
})();
