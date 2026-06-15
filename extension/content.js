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

function init() {
    if (monitorInterval) clearInterval(monitorInterval);
    lastTick = Date.now();
    monitorInterval = setInterval(checkState, 1000);

    // Event listeners to flush on unload or visibility change
    window.addEventListener('beforeunload', flush);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') {
            flush();
        }
        lastTick = Date.now();
    });
}

// Start monitoring
init();
