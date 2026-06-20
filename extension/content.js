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
    return parseYoutubeVideoId(window.location.href);
}

function isShortsPage() {
    return window.location.pathname.startsWith('/shorts/') ||
        document.querySelector('ytd-shorts, ytd-reel-player-overlay-renderer') !== null;
}

function getActiveVideoElement() {
    if (isShortsPage()) {
        const activeRenderer = document.querySelector('ytd-reel-video-renderer[is-active]');
        if (activeRenderer) {
            const video = activeRenderer.querySelector('video');
            if (video) return video;
        }
        // Fallback to finding any playing video on Shorts page
        const playing = Array.from(document.querySelectorAll('video')).find(v => !v.paused);
        if (playing) return playing;
    }
    return document.querySelector('video');
}

function parseVideoDetails() {
    const onShorts = isShortsPage();

    // Title
    let title = "";
    let titleEl = null;

    if (onShorts) {
        const activeRenderer = document.querySelector('ytd-reel-video-renderer[is-active]');
        if (activeRenderer) {
            titleEl = activeRenderer.querySelector('ytd-reel-player-header-renderer h2') ||
                      activeRenderer.querySelector('h2.ytd-reel-player-header-renderer') ||
                      activeRenderer.querySelector('ytd-reel-player-overlay-renderer #title') ||
                      activeRenderer.querySelector('.title') ||
                      activeRenderer.querySelector('#title');
        }
        if (!titleEl) {
            titleEl = document.querySelector('ytd-reel-player-header-renderer h2') ||
                      document.querySelector('h2.ytd-reel-player-header-renderer') ||
                      document.querySelector('ytd-reel-player-overlay-renderer #title') ||
                      document.querySelector('#title');
        }
    } else {
        titleEl = document.querySelector('ytd-watch-metadata h1') ||
                  document.querySelector('h1.ytd-watch-metadata') ||
                  document.querySelector('#container > h1 > yt-formatted-string');
    }

    if (titleEl) {
        title = titleEl.textContent.trim();
    } else {
        title = document.title.replace(" - YouTube", "").trim();
    }

    // Channel Name and Channel ID
    let channelName = "";
    let channelId = "";
    let channelEl = null;

    if (onShorts) {
        const activeRenderer = document.querySelector('ytd-reel-video-renderer[is-active]');
        if (activeRenderer) {
            channelEl = activeRenderer.querySelector('ytd-reel-player-overlay-renderer ytd-channel-name a') ||
                        activeRenderer.querySelector('.ytd-reel-player-overlay-renderer #channel-name a') ||
                        activeRenderer.querySelector('ytd-channel-name a') ||
                        activeRenderer.querySelector('#channel-name a') ||
                        activeRenderer.querySelector('a[href^="/@"]');
        }
        if (!channelEl) {
            channelEl = document.querySelector('ytd-reel-player-overlay-renderer ytd-channel-name a') ||
                        document.querySelector('.ytd-reel-player-overlay-renderer #channel-name a');
        }
    } else {
        channelEl = document.querySelector('ytd-video-owner-renderer #channel-name a') ||
                    document.querySelector('#upload-info #channel-name a') ||
                    document.querySelector('#owner-text a');
    }

    if (channelEl) {
        channelName = channelEl.textContent.trim();
        const href = channelEl.getAttribute('href') || "";
        channelId = href.split('/').pop();
    }

    return { title, channelName, channelId };
}

function flush() {
    if (accumulatedTime > 0 && currentVideoId) {
        // Only parse from DOM if we are still on the same video page.
        // If the URL has already changed, querying the DOM will yield the next video's details.
        let title = currentTitle;
        let channelName = currentChannelName;
        let channelId = currentChannelId;
        
        if (getVideoId() === currentVideoId) {
            const details = parseVideoDetails();
            title = details.title || title;
            channelName = details.channelName || channelName;
            channelId = details.channelId || channelId;
        }

        const payload = {
            video_id: currentVideoId,
            title: title || "Unknown Video",
            channel_name: channelName || "Unknown Channel",
            channel_id: channelId || "",
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

    const video = getActiveVideoElement();
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
        // If we don't have details yet, try to fetch them again
        if (!currentTitle || currentTitle === "Unknown Video" || !currentChannelName) {
            const details = parseVideoDetails();
            if (details.title) currentTitle = details.title;
            if (details.channelName) currentChannelName = details.channelName;
            if (details.channelId) currentChannelId = details.channelId;
        }
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

// Button/link/heading text that signals a signup form
const SIGNUP_TEXT_PATTERNS = [
    /sign\s*up/i,
    /create\s*(an?\s*)?account/i,
    /register/i,
    /get\s*started/i,
    /join\s*(now|free|us)?/i,
    /new\s*account/i,
];

// Form field name/id/autocomplete attributes that appear on registration forms
// but almost never on login forms.
const REGISTRATION_FIELD_SELECTORS = [
    // Name fields
    'input[name*="first" i][type="text"]',
    'input[name*="last" i][type="text"]',
    'input[id*="first" i][type="text"]',
    'input[id*="last" i][type="text"]',
    'input[autocomplete="given-name"]',
    'input[autocomplete="family-name"]',
    'input[autocomplete="name"]',
    // Phone / birthday — common on signup, never on login
    'input[type="tel"]',
    'input[autocomplete="bday"]',
    'input[autocomplete="bday-day"]',
    'input[name*="birthday" i]',
    'input[name*="phone" i]',
    // Username chosen during creation (not email for login)
    'input[autocomplete="username"][name*="new" i]',
    'input[name*="username" i][name*="new" i]',
];

/**
 * Returns true if the current page is likely a signup / account-creation page.
 * Uses four complementary signal layers in priority order:
 *   1. URL path heuristics
 *   2. autocomplete="new-password" on any password field (spec-required by browsers)
 *   3. Multiple password fields (password + confirm)
 *   4. Registration-specific form fields (name, phone, birthday)
 *   5. Page-level text (headings, links, submit buttons)
 */
function isSignupPage() {
    const path = window.location.pathname + window.location.search;

    // 1. URL heuristic — fast, reliable for well-structured sites
    for (const pattern of SIGNUP_PATH_PATTERNS) {
        if (pattern.test(path)) {
            return true;
        }
    }

    // Must have at least one password field for any of the following checks
    const passwordInputs = document.querySelectorAll('input[type="password"]');
    if (passwordInputs.length === 0) {
        return false;
    }

    // 2. autocomplete="new-password" — the most reliable signal; the HTML spec
    //    requires registration forms to use this and login forms "current-password".
    //    All major sites (Google, GitHub, Facebook, DigitalOcean) set this correctly.
    for (const input of passwordInputs) {
        if ((input.getAttribute('autocomplete') || '').toLowerCase() === 'new-password') {
            return true;
        }
    }

    // 3. Multiple password fields strongly suggest password + confirm-password
    if (passwordInputs.length >= 2) {
        return true;
    }

    // 4. Registration-specific fields that are essentially never on login forms
    for (const selector of REGISTRATION_FIELD_SELECTORS) {
        if (document.querySelector(selector)) {
            return true;
        }
    }

    // 5. Page-level text signals — check headings, links, and submit buttons
    const textCandidates = [
        ...document.querySelectorAll('h1, h2, h3'),
        ...document.querySelectorAll('button[type="submit"], input[type="submit"], button'),
        ...document.querySelectorAll('a[href*="signup" i], a[href*="register" i]'),
    ];
    for (const el of textCandidates) {
        const text = (el.textContent || el.value || '').trim();
        for (const pattern of SIGNUP_TEXT_PATTERNS) {
            if (pattern.test(text)) {
                return true;
            }
        }
    }

    return false;
}

/**
 * Returns true if the page looks like a login form.
 * A page is a login if it has exactly one password field AND that field is not
 * marked as new-password AND the page doesn't match signup heuristics.
 */
function isLoginPage() {
    const passwordInputs = document.querySelectorAll('input[type="password"]');
    if (passwordInputs.length !== 1) return false;

    // If the single password field is for a new password, it's a signup step
    const autocomplete = (passwordInputs[0].getAttribute('autocomplete') || '').toLowerCase();
    if (autocomplete === 'new-password') return false;

    return !isSignupPage();
}

/**
 * Extracts the best username/email from a form containing the given password input.
 */
function extractUsername(form) {
    if (!form) return '';
    const emailInput = form.querySelector(
        'input[type="email"], input[name*="email" i], input[id*="email" i], input[autocomplete="email"]'
    );
    if (emailInput && emailInput.value) {
        return emailInput.value.trim();
    }
    const usernameInput = form.querySelector(
        'input[type="text"][name*="user" i], input[type="text"][name*="login" i], ' +
        'input[type="text"][id*="user" i], input[type="text"][id*="login" i], ' +
        'input[autocomplete="username"]'
    );
    if (usernameInput && usernameInput.value) {
        return usernameInput.value.trim();
    }
    // Fallback: first text input in the form
    const firstText = form.querySelector('input[type="text"]');
    return firstText ? firstText.value.trim() : '';
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
                type: 'CHECK_REGISTRATION',
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
        if (passwordInputs.length === 0) return;

        // Skip if any password field is flagged as new-password (signup step)
        for (const input of passwordInputs) {
            const ac = (input.getAttribute('autocomplete') || '').toLowerCase();
            if (ac === 'new-password') return;
        }

        // Skip if more than one password field (signup with confirm)
        if (passwordInputs.length > 1) return;

        form.addEventListener(
            'submit',
            (event) => {
                // Don't interfere with the actual submit
                const username = extractUsername(form);
                if (!username) return;

                const domain = window.location.hostname;
                try {
                    chrome.runtime.sendMessage({
                        type: 'LOGIN_DETECTED',
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

// ============================================================
// Local Sentiment & Slang Analysis Monitor
// ============================================================

const DEFAULT_BAD_WORDS = {
    self_harm: [
        "kys", "kms", "suicide", "kill myself", "kill yourself", "end my life", 
        "cut myself", "want to die", "hanging myself", "overdose"
    ],
    modifiers: [
        "loser", "retard", "trash", "fat", "ugly", "worthless", "die", "kill you",
        "kill u", "hate you", "stupid", "idiot", "garbage", "pathetic"
    ]
};

const TARGET_PRONOUNS = ["you", "your", "u", "ur", "yours", "yourself", "u're", "you're"];

function decodeLeetspeak(text) {
    const leetMap = {
        '1': 'l', '3': 'e', '4': 'a', '@': 'a', '5': 's', 
        '$': 's', '0': 'o', '7': 't', '!': 'i', '8': 'b'
    };
    return text.toLowerCase().split('').map(char => leetMap[char] || char).join('');
}

function normalizeSentence(sentence) {
    let text = decodeLeetspeak(sentence);
    // Strip common spacing punctuation
    text = text.replace(/[-_.,*?#!$@()\[\]{}]/g, '');
    
    // Collapse single-letter spaced chains (e.g. "l o s e r" -> "loser")
    text = text.replace(/\b([a-z])(?:\s+([a-z]))+\b/gi, (match) => {
        return match.replace(/\s+/g, '');
    });
    
    return text.trim();
}

function evaluateTextContent(text, platform) {
    const sentences = text.split(/(?<=[.!?])\s+|\n+/);
    
    chrome.storage.local.get({ custom_bad_words: [] }, (storage) => {
        const customWords = storage.custom_bad_words || [];
        const modifiers = [...DEFAULT_BAD_WORDS.modifiers, ...customWords];
        const selfHarm = DEFAULT_BAD_WORDS.self_harm;
        
        for (let i = 0; i < sentences.length; i++) {
            const rawSentence = sentences[i].trim();
            if (!rawSentence) continue;
            
            const normalized = normalizeSentence(rawSentence);
            const tokens = normalized.split(/\s+/);
            
            let matchedWords = [];
            let score = 0.0;
            let isBreach = false;
            
            // Stage 2: Check self-harm (immediate trigger)
            for (const word of selfHarm) {
                if (normalized.includes(word)) {
                    matchedWords.push(word);
                    score = -1.0;
                    isBreach = true;
                    break;
                }
            }
            
            if (!isBreach) {
                // Stage 3: Check slang modifiers + pronoun proximity
                for (const word of modifiers) {
                    const idx = normalized.indexOf(word);
                    if (idx !== -1) {
                        let isTargeted = false;
                        const wordTokens = word.split(/\s+/);
                        const mainWordToken = wordTokens[0];
                        const wordTokenIdx = tokens.indexOf(mainWordToken);
                        
                        if (wordTokenIdx !== -1) {
                            const startSearch = Math.max(0, wordTokenIdx - 3);
                            const endSearch = Math.min(tokens.length - 1, wordTokenIdx + wordTokens.length + 2);
                            
                            for (let j = startSearch; j <= endSearch; j++) {
                                if (j >= wordTokenIdx && j < wordTokenIdx + wordTokens.length) {
                                    continue;
                                }
                                if (TARGET_PRONOUNS.includes(tokens[j])) {
                                    isTargeted = true;
                                    break;
                                }
                            }
                        } else {
                            const proximityRegex = new RegExp(`(?:\\b(?:you|your|u|ur|yours|yourself|you're)\\b\\s+(?:\\w+\\s+){0,2}\\b${word}\\b)|(?:\\b${word}\\b\\s+(?:\\w+\\s+){0,2}\\b(?:you|your|u|ur|yours|yourself|you're)\\b)`, 'i');
                            if (proximityRegex.test(normalized)) {
                                isTargeted = true;
                            }
                        }
                        
                        if (isTargeted) {
                            matchedWords.push(word);
                            score = -0.8;
                            isBreach = true;
                            break;
                        } else {
                            score = -0.3; // Gaming noise
                        }
                    }
                }
            }
            
            if (isBreach) {
                const contextBefore = i > 0 ? sentences[i - 1].trim() : "";
                const contextAfter = i < sentences.length - 1 ? sentences[i + 1].trim() : "";
                
                const payload = {
                    type: score <= -0.9 ? "SENTIMENT_BREACH" : "DIALOGUE_FLAG",
                    platform: platform,
                    details: {
                        text_snippet: rawSentence,
                        context_before: contextBefore,
                        context_after: contextAfter,
                        platform: platform,
                        matched_words: matchedWords,
                        score: score,
                        timestamp: new Date().toISOString()
                    }
                };
                
                try {
                    chrome.runtime.sendMessage(payload);
                } catch (e) {
                    // Context invalidated
                }
                break;
            }
        }
    });
}

let dialogueDebounceTimer = null;
document.addEventListener('input', (event) => {
    const target = event.target;
    if (!target) return;
    
    const isInput = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.getAttribute('contenteditable') === 'true';
    if (!isInput) return;
    
    const hostname = window.location.hostname;
    let platform = null;
    if (hostname.includes('discord.com')) {
        platform = 'discord';
    } else if (hostname.includes('reddit.com')) {
        platform = 'reddit';
    } else if (hostname.includes('instagram.com')) {
        platform = 'instagram';
    }
    
    if (!platform) return;
    
    let text = '';
    if (target.getAttribute('contenteditable') === 'true') {
        text = target.innerText || target.textContent || '';
    } else {
        text = target.value || '';
    }
    
    if (!text.trim()) return;
    
    clearTimeout(dialogueDebounceTimer);
    dialogueDebounceTimer = setTimeout(() => {
        evaluateTextContent(text, platform);
    }, 1500);
});
