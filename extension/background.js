// Service worker to handle background YouTube and Web log shipping with local offline buffering,
// plus registration detection enforcement and login audit forwarding.

importScripts('i18n.js', 'youtube_utils.js');

// Flush queues on startup
chrome.runtime.onInstalled.addListener(() => {
    flushBufferQueue();
    flushWebBufferQueue();
});

// Periodic flush every 5 minutes
chrome.alarms.create("flush_queue_alarm", { periodInMinutes: 5 });
chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === "flush_queue_alarm") {
        flushBufferQueue();
        flushWebBufferQueue();
    }
});

// ============================================================
// Message router — content script → background
// ============================================================

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "YOUTUBE_LOG" && message.log) {
        queueLog(message.log);
        return false; // No async response needed
    }

    if (message.type === "CHECK_REGISTRATION" && message.domain) {
        handleCheckRegistration(message, sender, sendResponse);
        return true; // Keep channel open for async response
    }

    if (message.type === "REQUEST_REGISTRATION" && message.domain) {
        sendNativeRequest({ type: "REQUEST_REGISTRATION", domain: message.domain }, () => {});
        return false;
    }

    if (message.type === "LOGIN_DETECTED" && message.domain) {
        sendNativeRequest(
            { type: "LOGIN_DETECTED", domain: message.domain, username: message.username || "" },
            () => {}
        );
        return false;
    }

    // Forward child access requests (Guardian Space overlay) to the native agent
    if (message.type === "ACCESS_REQUEST") {
        sendNativeRequest(
            {
                type: "ACCESS_REQUEST",
                reason: message.reason || "unknown",
                message: message.message || ""
            },
            () => {}
        );
        return false;
    }
});


// ============================================================
// Registration enforcement
// ============================================================

/**
 * Asks the native agent whether registration on this domain is allowed.
 * If not allowed, redirects the tab to the block page.
 */
function handleCheckRegistration(message, sender, sendResponse) {
    sendNativeRequest(
        { type: "CHECK_REGISTRATION", domain: message.domain },
        (response) => {
            if (!response || response.allowed !== false) {
                // Allowed or agent unavailable — let the page through
                sendResponse({ allowed: true });
                return;
            }

            // Registration is blocked — redirect tab to the Guardian Space block page
            if (sender && sender.tab && sender.tab.id) {
                const lang = (navigator.language || "en").split("-")[0];
                const blockUrl =
                    chrome.runtime.getURL("blockedv2.html") +
                    "?reason=signup" +
                    "&age=eight12" +
                    "&lang=" +
                    encodeURIComponent(lang) +
                    "&device=" +
                    encodeURIComponent(message.domain || "") +
                    "&url=" +
                    encodeURIComponent((sender && sender.tab && sender.tab.url) || "") +
                    "&note=" +
                    encodeURIComponent(guardianExtI18n('signupBlockNote'));

                chrome.tabs.update(sender.tab.id, { url: blockUrl });
            }
            sendResponse({ allowed: false });
        }
    );
}

// ============================================================
// Keep track of the last logged URL per tab to prevent duplicate logging
// ============================================================

const lastLoggedUrls = {};

// Listen for tab navigation changes to record web history
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (changeInfo.status === 'complete' && tab.url) {
        const urlString = tab.url;

        // Skip internal/invalid URLs
        if (!urlString.startsWith('http://') && !urlString.startsWith('https://')) {
            return;
        }

        // Skip the extension's own blocked page
        if (urlString.startsWith(chrome.runtime.getURL(""))) {
            return;
        }

        // Skip YouTube watch pages since they are handled separately by content.js
        try {
            const parsedUrl = new URL(urlString);
            if (parsedUrl.hostname.includes('youtube.com') && parseYoutubeVideoId(urlString)) {
                return;
            }

            // Prevent duplicate logs for the same URL in the same tab
            if (lastLoggedUrls[tabId] === urlString) {
                return;
            }
            lastLoggedUrls[tabId] = urlString;

            const domain = parsedUrl.hostname;
            const title = tab.title || domain;

            const webLogEntry = {
                url: urlString,
                title: title,
                domain: domain,
                visited_at: new Date().toISOString()
            };

            queueWebLog(webLogEntry);
        } catch (e) {
            console.error("Guardian History Monitor: Error parsing tab update:", e);
        }
    }
});

// Clean up stored tab URLs when tabs are closed
chrome.tabs.onRemoved.addListener((tabId) => {
    delete lastLoggedUrls[tabId];
});

// ============================================================
// Queue helpers
// ============================================================

// Queue a YouTube log entry in local storage and trigger flush
function queueLog(logEntry) {
    chrome.storage.local.get({ log_queue: [] }, (result) => {
        const queue = result.log_queue;
        queue.push(logEntry);
        
        // Keep queue capped at 1000 items to avoid storage overflow
        if (queue.length > 1000) {
            queue.shift();
        }
        
        chrome.storage.local.set({ log_queue: queue }, () => {
            flushBufferQueue();
        });
    });
}

// Queue a web log entry in local storage and trigger flush
function queueWebLog(logEntry) {
    chrome.storage.local.get({ web_queue: [] }, (result) => {
        const queue = result.web_queue;
        queue.push(logEntry);
        
        // Keep queue capped at 2000 items to avoid storage overflow
        if (queue.length > 2000) {
            queue.shift();
        }
        
        chrome.storage.local.set({ web_queue: queue }, () => {
            flushWebBufferQueue();
        });
    });
}

// ============================================================
// Native messaging helpers
// ============================================================

/**
 * Sends a single request to the native messaging host and invokes the callback
 * with the parsed response (or null on error).
 */
function sendNativeRequest(payload, callback) {
    chrome.runtime.sendNativeMessage('com.guardian.agent', payload, (response) => {
        if (chrome.runtime.lastError) {
            console.warn(
                "Guardian: Failed to contact Native Messaging Host:",
                chrome.runtime.lastError.message
            );
            callback(null);
            return;
        }
        callback(response);
    });
}

// ============================================================
// Flush functions
// ============================================================

// Flush buffered YouTube queue to TimeKpr/Guardian server via Native Messaging Host
function flushBufferQueue() {
    chrome.storage.local.get({ log_queue: [] }, (result) => {
        const queue = result.log_queue;
        if (queue.length === 0) return;

        const logsToSend = [...queue];
        const payload = {
            type: 'YOUTUBE_LOG',
            logs: logsToSend
        };

        chrome.runtime.sendNativeMessage('com.guardian.agent', payload, (response) => {
            if (chrome.runtime.lastError) {
                const errMsg = chrome.runtime.lastError.message;
                console.warn("Guardian YouTube Monitor: Failed to connect to Native Messaging Host. Keeping logs in buffer.", errMsg);
                chrome.storage.local.set({ last_native_error: "Connection failed: " + errMsg });
                return;
            }

            if (response && response.success) {
                chrome.storage.local.get({ log_queue: [] }, (currentResult) => {
                    const currentQueue = currentResult.log_queue;
                    const remainingQueue = currentQueue.filter(item => {
                        return !logsToSend.some(sentItem => 
                            sentItem.video_id === item.video_id && 
                            sentItem.watched_at === item.watched_at
                        );
                    });
                    chrome.storage.local.set({ log_queue: remainingQueue, last_native_error: "None" });
                    console.log(`Guardian YouTube Monitor: Successfully flushed ${logsToSend.length} log(s) via Native Messaging.`);
                });
            } else {
                const errMsg = response ? response.message : "No response";
                console.error("Guardian YouTube Monitor: Agent failed to log YouTube history:", errMsg);
                chrome.storage.local.set({ last_native_error: "Agent error: " + errMsg });
            }
        });
    });
}

// Flush buffered Web queue to TimeKpr/Guardian server via Native Messaging Host
function flushWebBufferQueue() {
    chrome.storage.local.get({ web_queue: [] }, (result) => {
        const queue = result.web_queue;
        if (queue.length === 0) return;

        const logsToSend = [...queue];
        const payload = {
            type: 'BROWSER_LOG',
            logs: logsToSend
        };

        chrome.runtime.sendNativeMessage('com.guardian.agent', payload, (response) => {
            if (chrome.runtime.lastError) {
                const errMsg = chrome.runtime.lastError.message;
                console.warn("Guardian History Monitor: Failed to connect to Native Messaging Host. Keeping logs in buffer.", errMsg);
                chrome.storage.local.set({ last_web_native_error: "Connection failed: " + errMsg });
                return;
            }

            if (response && response.success) {
                chrome.storage.local.get({ web_queue: [] }, (currentResult) => {
                    const currentQueue = currentResult.web_queue;
                    const remainingQueue = currentQueue.filter(item => {
                        return !logsToSend.some(sentItem => 
                            sentItem.url === item.url && 
                            sentItem.visited_at === item.visited_at
                        );
                    });
                    chrome.storage.local.set({ web_queue: remainingQueue, last_web_native_error: "None" });
                    console.log(`Guardian History Monitor: Successfully flushed ${logsToSend.length} log(s) via Native Messaging.`);
                });
            } else {
                const errMsg = response ? response.message : "No response";
                console.error("Guardian History Monitor: Agent failed to log web history:", errMsg);
                chrome.storage.local.set({ last_web_native_error: "Agent error: " + errMsg });
            }
        });
    });
}
