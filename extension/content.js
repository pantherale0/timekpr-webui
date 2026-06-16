// ============================================================
// YouTube watch-time monitoring (YouTube pages only)
// ============================================================

let currentVideoId = null;
let currentTitle = "";
let currentChannelName = "";
let currentChannelId = "";
let accumulatedTime = 0; // in seconds
let lastTick = Date.now();
let monitorInterval = null;

function getVideoId() {
    const params = new URLSearchParams(window.location.search);
    return params.get('v');
}

function parseVideoDetails() {
    // Title
    let title = "";
    const titleEl = document.querySelector('ytd-watch-metadata h1') || 
                    document.querySelector('h1.ytd-watch-metadata') ||
                    document.querySelector('#container > h1 > yt-formatted-string');
    if (titleEl) {
        title = titleEl.textContent.trim();
    } else {
        title = document.title.replace(" - YouTube", "").trim();
    }

    // Channel Name and Channel ID
    let channelName = "";
    let channelId = "";
    const channelEl = document.querySelector('ytd-video-owner-renderer #channel-name a') ||
                      document.querySelector('#upload-info #channel-name a') ||
                      document.querySelector('#owner-text a');
    if (channelEl) {
        channelName = channelEl.textContent.trim();
        const href = channelEl.getAttribute('href') || "";
        channelId = href.split('/').pop();
    }

    return { title, channelName, channelId };
}

function flush() {
    if (accumulatedTime > 0 && currentVideoId) {
        const details = parseVideoDetails();
        const payload = {
            video_id: currentVideoId,
            title: details.title || currentTitle || "Unknown Video",
            channel_name: details.channelName || currentChannelName || "Unknown Channel",
            channel_id: details.channelId || currentChannelId || "",
            duration_seconds: Math.round(accumulatedTime),
            watched_at: new Date().toISOString()
        };
        
        try {
            chrome.runtime.sendMessage({ type: "YOUTUBE_LOG", log: payload });
        } catch (e) {
            // Extension context invalidated (e.g. extension reloaded/disabled)
        }
        accumulatedTime = 0;
    }
}

function checkState() {
    const videoId = getVideoId();
    if (!videoId) {
        flush();
        currentVideoId = null;
        return;
    }

    const video = document.querySelector('video');
    const isPlaying = video && !video.paused && !video.ended && video.readyState >= 3;
    const isVisible = document.visibilityState === 'visible';

    const now = Date.now();
    const delta = (now - lastTick) / 1000;
    lastTick = now;

    if (videoId !== currentVideoId) {
        flush(); // Flush old video log
        currentVideoId = videoId;
        const details = parseVideoDetails();
        currentTitle = details.title;
        currentChannelName = details.channelName;
        currentChannelId = details.channelId;
        accumulatedTime = 0;
    }

    if (isPlaying && isVisible) {
        accumulatedTime += delta;
        // Periodic flush every 60 seconds to prevent losing data and excessive session spikes
        if (accumulatedTime >= 60) {
            flush();
        }
    }
}

function bindPageListeners() {
    if (bindPageListeners.bound) {
        return;
    }
    bindPageListeners.bound = true;

    window.addEventListener('beforeunload', flush);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') {
            flush();
        }
        lastTick = Date.now();
    });
    // YouTube SPA navigations (playlist, related videos, home -> watch) reuse the same document.
    document.addEventListener('yt-navigate-finish', () => {
        lastTick = Date.now();
    });
}

function startMonitoring() {
    bindPageListeners();
    if (monitorInterval) {
        return;
    }
    lastTick = Date.now();
    monitorInterval = setInterval(checkState, 1000);
}

// Only run YouTube monitoring on YouTube pages
if (window.location.hostname.includes('youtube.com')) {
    startMonitoring();
}

// ============================================================
// Registration detection & login audit (all pages)
// ============================================================

// URL path heuristics that strongly suggest a signup/registration page
const SIGNUP_PATH_PATTERNS = [
    /\/signup/i,
    /\/sign-up/i,
    /\/register/i,
    /\/registration/i,
    /\/join/i,
    /\/create[-_]?account/i,
    /\/new[-_]?account/i,
    /\/get[-_]?started/i,
    /\/enroll/i,
];

// Button/link text that signals a signup form
const SIGNUP_BUTTON_PATTERNS = [
    /sign\s*up/i,
    /create\s*(an?\s*)?account/i,
    /register/i,
    /get\s*started/i,
    /join\s*(now|free|us)?/i,
];

/**
 * Returns true if the current page is likely a signup / account-creation page.
 * Uses both URL heuristics and DOM analysis.
 */
function isSignupPage() {
    const path = window.location.pathname + window.location.search;

    // 1. URL heuristic
    for (const pattern of SIGNUP_PATH_PATTERNS) {
        if (pattern.test(path)) {
            return true;
        }
    }

    // 2. DOM analysis — must have a password field
    const passwordInputs = document.querySelectorAll('input[type="password"]');
    if (passwordInputs.length === 0) {
        return false;
    }

    // 3. Multiple password fields strongly suggest a signup (password + confirm)
    if (passwordInputs.length >= 2) {
        return true;
    }

    // 4. Single password field — look for signup button/link text nearby
    const submitButtons = document.querySelectorAll(
        'button[type="submit"], input[type="submit"], button'
    );
    for (const btn of submitButtons) {
        const text = (btn.textContent || btn.value || "").trim();
        for (const pattern of SIGNUP_BUTTON_PATTERNS) {
            if (pattern.test(text)) {
                return true;
            }
        }
    }

    return false;
}

/**
 * Returns true if the page looks like a login form (single password field,
 * no signup indicators).
 */
function isLoginPage() {
    const passwordInputs = document.querySelectorAll('input[type="password"]');
    // Login pages typically have exactly one password field
    return passwordInputs.length === 1 && !isSignupPage();
}

/**
 * Extracts the best username/email from a form containing the given password input.
 */
function extractUsername(form) {
    if (!form) return "";
    const emailInput = form.querySelector(
        'input[type="email"], input[name*="email" i], input[id*="email" i]'
    );
    if (emailInput && emailInput.value) {
        return emailInput.value.trim();
    }
    const usernameInput = form.querySelector(
        'input[type="text"][name*="user" i], input[type="text"][name*="login" i], ' +
        'input[type="text"][id*="user" i], input[type="text"][id*="login" i]'
    );
    if (usernameInput && usernameInput.value) {
        return usernameInput.value.trim();
    }
    // Fallback: first text input in the form
    const firstText = form.querySelector('input[type="text"]');
    return firstText ? firstText.value.trim() : "";
}

// ---- Registration detection ----

let registrationCheckDone = false;

function checkRegistration() {
    if (registrationCheckDone) return;
    registrationCheckDone = true;

    if (!isSignupPage()) return;

    const domain = window.location.hostname;
    try {
        chrome.runtime.sendMessage(
            {
                type: "CHECK_REGISTRATION",
                domain: domain,
                url: window.location.href,
            },
            (response) => {
                if (chrome.runtime.lastError) return; // Extension context gone
                // background.js handles the redirect if blocked
            }
        );
    } catch (e) {
        // Extension context invalidated
    }
}

// Run detection after DOM is fully ready (we're injected at document_end)
checkRegistration();

// Also hook into SPA navigations where the URL changes without a full reload
window.addEventListener('popstate', () => {
    registrationCheckDone = false;
    checkRegistration();
});

// ---- Login detection ----

/**
 * Attaches a submit listener to every form that contains a password field
 * and looks like a login (not signup) form.  Captures the username (not
 * the password) and sends it to background.js for the online-accounts audit.
 */
function attachLoginListeners() {
    const forms = document.querySelectorAll('form');
    forms.forEach((form) => {
        const passwordInputs = form.querySelectorAll('input[type="password"]');
        if (passwordInputs.length !== 1) return; // Skip signup forms (>=2 pw fields)

        form.addEventListener(
            'submit',
            (event) => {
                // Don't interfere with the actual submit
                const username = extractUsername(form);
                if (!username) return;

                const domain = window.location.hostname;
                try {
                    chrome.runtime.sendMessage({
                        type: "LOGIN_DETECTED",
                        domain: domain,
                        username: username,
                    });
                } catch (e) {
                    // Extension context invalidated
                }
            },
            { once: true } // Only fire once per form lifecycle
        );
    });
}

// Attach login listeners on page load
if (isLoginPage()) {
    attachLoginListeners();
}
