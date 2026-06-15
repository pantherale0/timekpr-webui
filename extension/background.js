// Service worker to handle background YouTube log shipping with local offline buffering

// Flush queue on startup
chrome.runtime.onInstalled.addListener(() => {
    flushBufferQueue();
});

// Periodic flush every 5 minutes
chrome.alarms.create("flush_queue_alarm", { periodInMinutes: 5 });
chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === "flush_queue_alarm") {
        flushBufferQueue();
    }
});

// Listen for messages from content.js
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "YOUTUBE_LOG" && message.log) {
        queueLog(message.log);
    }
});

// Queue a log entry in local storage and trigger flush
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

// Flush buffered queue to TimeKpr/Guardian server
function flushBufferQueue() {
    // 1. Get policies from managed storage (configured by OS administrative policies)
    chrome.storage.managed.get(['server_url', 'secure_token', 'linux_username'], (policy) => {
        const serverUrl = (policy.server_url || "").trim().replace(/\/$/, "");
        const secureToken = (policy.secure_token || "").trim();
        const linuxUsername = (policy.linux_username || "").trim();

        if (!serverUrl || !secureToken || !linuxUsername) {
            console.warn("Guardian YouTube Monitor: Managed policy settings missing. Skipping flush.", policy);
            return;
        }

        // 2. Get queued logs from local storage
        chrome.storage.local.get({ log_queue: [] }, (result) => {
            const queue = result.log_queue;
            if (queue.length === 0) return;

            // Make a copy of logs to send
            const logsToSend = [...queue];

            const payload = {
                linux_username: linuxUsername,
                logs: logsToSend
            };

            const targetUrl = `${serverUrl}/api/youtube/log`;

            fetch(targetUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${secureToken}`
                },
                body: JSON.stringify(payload)
            })
            .then(response => {
                if (response.status === 200) {
                    // Success! Remove sent logs from queue
                    chrome.storage.local.get({ log_queue: [] }, (currentResult) => {
                        const currentQueue = currentResult.log_queue;
                        // Filter out the items we successfully sent
                        const remainingQueue = currentQueue.filter(item => {
                            // Match by video_id and watched_at timestamp
                            return !logsToSend.some(sentItem => 
                                sentItem.video_id === item.video_id && 
                                sentItem.watched_at === item.watched_at
                            );
                        });
                        chrome.storage.local.set({ log_queue: remainingQueue });
                        console.log(`Guardian YouTube Monitor: Successfully flushed ${logsToSend.length} log(s).`);
                    });
                } else {
                    console.error(`Guardian YouTube Monitor: Server returned HTTP ${response.status} during flush.`);
                }
            })
            .catch(error => {
                console.error("Guardian YouTube Monitor: Network error during flush.", error);
            });
        });
    });
}
