// Service worker to handle background video and web log shipping with local offline buffering,
// plus registration detection enforcement and login audit forwarding.

importScripts('i18n.js', 'youtube_utils.js', 'tiktok_utils.js');

// Flush queues on startup
chrome.runtime.onInstalled.addListener(() => {
    migrateLegacyVideoQueue();
    flushVideoBufferQueue();
    flushWebBufferQueue();
});

// Periodic flush every 5 minutes
chrome.alarms.create("flush_queue_alarm", { periodInMinutes: 5 });
chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === "flush_queue_alarm") {
        flushVideoBufferQueue();
        flushWebBufferQueue();
    }
});

// ============================================================
// Message router — content script → background
// ============================================================

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "VIDEO_LOG" && message.log) {
        queueVideoLog(message.platform || 'youtube', message.log);
        return false;
    }

    if (message.type === "YOUTUBE_LOG" && message.log) {
        queueVideoLog('youtube', message.log);
        return false;
    }

    if (message.type === "CHECK_REGISTRATION" && message.domain) {
        handleCheckRegistration(message, sender, sendResponse);
        return true;
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

function handleCheckRegistration(message, sender, sendResponse) {
    sendNativeRequest(
        { type: "CHECK_REGISTRATION", domain: message.domain },
        (response) => {
            if (!response || response.allowed !== false) {
                sendResponse({ allowed: true });
                return;
            }

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

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (changeInfo.status === 'complete' && tab.url) {
        const urlString = tab.url;

        if (!urlString.startsWith('http://') && !urlString.startsWith('https://')) {
            return;
        }

        if (urlString.startsWith(chrome.runtime.getURL(""))) {
            return;
        }

        try {
            const parsedUrl = new URL(urlString);
            if (parsedUrl.hostname.includes('youtube.com') && (parseYoutubeVideoId(urlString) || parsedUrl.pathname.startsWith('/shorts'))) {
                return;
            }
            if (parsedUrl.hostname.includes('tiktok.com') && parseTiktokVideoId(urlString)) {
                return;
            }

            if (lastLoggedUrls[tabId] === urlString) {
                return;
            }
            lastLoggedUrls[tabId] = urlString;

            const domain = parsedUrl.hostname;
            const title = tab.title || domain;

            queueWebLog({
                url: urlString,
                title: title,
                domain: domain,
                visited_at: new Date().toISOString()
            });
        } catch (e) {
            console.error("Guardian History Monitor: Error parsing tab update:", e);
        }
    }
});

chrome.tabs.onRemoved.addListener((tabId) => {
    delete lastLoggedUrls[tabId];
});

// ============================================================
// Queue helpers
// ============================================================

function migrateLegacyVideoQueue() {
    chrome.storage.local.get({ log_queue: [], video_log_queue: [] }, (result) => {
        if (!result.log_queue || result.log_queue.length === 0) {
            return;
        }
        const migrated = result.log_queue.map((entry) => ({
            platform: 'youtube',
            ...entry,
        }));
        const merged = [...result.video_log_queue, ...migrated];
        chrome.storage.local.set({ video_log_queue: merged, log_queue: [] });
    });
}

function queueVideoLog(platform, logEntry) {
    chrome.storage.local.get({ video_log_queue: [] }, (result) => {
        const queue = result.video_log_queue;
        queue.push({ platform, ...logEntry });

        if (queue.length > 1000) {
            queue.shift();
        }

        chrome.storage.local.set({ video_log_queue: queue }, () => {
            flushVideoBufferQueue();
        });
    });
}

function queueWebLog(logEntry) {
    chrome.storage.local.get({ web_queue: [] }, (result) => {
        const queue = result.web_queue;
        queue.push(logEntry);

        if (queue.length > 2000) {
            queue.shift();
        }

        chrome.storage.local.set({ web_queue: queue }, () => {
            flushWebBufferQueue();
        });
    });
}

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

function flushVideoBufferQueue() {
    migrateLegacyVideoQueue();

    chrome.storage.local.get({ video_log_queue: [] }, (result) => {
        const queue = result.video_log_queue;
        if (queue.length === 0) {
            return;
        }

        const grouped = {};
        for (const entry of queue) {
            const platform = entry.platform || 'youtube';
            if (!grouped[platform]) {
                grouped[platform] = [];
            }
            grouped[platform].push(entry);
        }

        const platforms = Object.keys(grouped);
        let remainingFailures = false;

        const flushPlatform = (index) => {
            if (index >= platforms.length) {
                if (!remainingFailures) {
                    chrome.storage.local.set({ video_log_queue: [], last_native_error: "None" });
                }
                return;
            }

            const platform = platforms[index];
            const logsToSend = grouped[platform].map((entry) => {
                const copy = { ...entry };
                delete copy.platform;
                return copy;
            });

            const payload = {
                type: 'VIDEO_LOG',
                platform: platform,
                logs: logsToSend,
            };

            chrome.runtime.sendNativeMessage('com.guardian.agent', payload, (response) => {
                if (chrome.runtime.lastError) {
                    const errMsg = chrome.runtime.lastError.message;
                    console.warn(
                        "Guardian Video Monitor: Failed to connect to Native Messaging Host. Keeping logs in buffer.",
                        errMsg
                    );
                    chrome.storage.local.set({ last_native_error: "Connection failed: " + errMsg });
                    remainingFailures = true;
                    flushPlatform(index + 1);
                    return;
                }

                if (response && response.success) {
                    chrome.storage.local.get({ video_log_queue: [] }, (currentResult) => {
                        const currentQueue = currentResult.video_log_queue;
                        const remainingQueue = currentQueue.filter((item) => {
                            const itemPlatform = item.platform || 'youtube';
                            if (itemPlatform !== platform) {
                                return true;
                            }
                            return !logsToSend.some((sentItem) =>
                                sentItem.video_id === item.video_id &&
                                sentItem.watched_at === item.watched_at
                            );
                        });
                        chrome.storage.local.set({ video_log_queue: remainingQueue });
                        console.log(
                            `Guardian Video Monitor: Successfully flushed ${logsToSend.length} ${platform} log(s).`
                        );
                        flushPlatform(index + 1);
                    });
                } else {
                    const errMsg = response ? response.message : "No response";
                    console.error("Guardian Video Monitor: Agent failed to log video history:", errMsg);
                    chrome.storage.local.set({ last_native_error: "Agent error: " + errMsg });
                    remainingFailures = true;
                    flushPlatform(index + 1);
                }
            });
        };

        flushPlatform(0);
    });
}

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
