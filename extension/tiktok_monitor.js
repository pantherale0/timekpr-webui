// TikTok watch-time monitoring (TikTok pages only)

const TIKTOK_PLATFORM = 'tiktok';

let currentVideoId = null;
let currentTitle = '';
let currentChannelName = '';
let currentChannelId = '';
let accumulatedTime = 0;
let lastTick = Date.now();
let monitorInterval = null;
let lastKnownHref = window.location.href;

function getVideoIdFromHydration() {
    const script = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
    if (!script || !script.textContent) {
        return null;
    }

    try {
        const data = JSON.parse(script.textContent);
        const defaultScope = data?.__DEFAULT_SCOPE__ || {};
        const videoDetail = defaultScope['webapp.video-detail'];
        if (videoDetail?.itemInfo?.itemStruct?.id) {
            return String(videoDetail.itemInfo.itemStruct.id);
        }
        const videoItem = defaultScope['webapp.video-detail']?.itemInfo?.itemStruct;
        if (videoItem?.video?.id) {
            return String(videoItem.video.id);
        }
    } catch (e) {
        return null;
    }

    return null;
}

function getVideoId() {
    return parseTiktokVideoId(window.location.href) || getVideoIdFromHydration();
}

function parseVideoDetails() {
    let title = '';
    const titleEl = document.querySelector('[data-e2e="browse-video-desc"]') ||
        document.querySelector('[data-e2e="video-desc"]') ||
        document.querySelector('h1[data-e2e="video-title"]');
    if (titleEl) {
        title = titleEl.textContent.trim();
    } else {
        title = document.title.replace(/\s*\|\s*TikTok\s*$/i, '').trim();
    }

    let channelName = '';
    let channelId = '';
    const channelEl = document.querySelector('[data-e2e="browse-username"]') ||
        document.querySelector('[data-e2e="video-author-uniqueid"]') ||
        document.querySelector('[data-e2e="video-author-avatar"] + a');
    if (channelEl) {
        channelName = channelEl.textContent.trim();
        const href = channelEl.getAttribute('href') || '';
        const handleMatch = href.match(/@([^/?#]+)/);
        channelId = handleMatch ? `@${handleMatch[1]}` : href.split('/').pop();
    }

    return { title, channelName, channelId };
}

function flush() {
    if (accumulatedTime > 0 && currentVideoId) {
        const details = parseVideoDetails();
        const payload = {
            video_id: currentVideoId,
            title: details.title || currentTitle || 'Unknown Video',
            channel_name: details.channelName || currentChannelName || 'Unknown Creator',
            channel_id: details.channelId || currentChannelId || '',
            duration_seconds: Math.round(accumulatedTime),
            watched_at: new Date().toISOString(),
        };

        try {
            chrome.runtime.sendMessage({
                type: 'VIDEO_LOG',
                platform: TIKTOK_PLATFORM,
                log: payload,
            });
        } catch (e) {
            // Extension context invalidated
        }
        accumulatedTime = 0;
    }
}

function checkState() {
    if (window.location.href !== lastKnownHref) {
        lastKnownHref = window.location.href;
        lastTick = Date.now();
    }

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
        flush();
        currentVideoId = videoId;
        const details = parseVideoDetails();
        currentTitle = details.title;
        currentChannelName = details.channelName;
        currentChannelId = details.channelId;
        accumulatedTime = 0;
    }

    if (isPlaying && isVisible) {
        accumulatedTime += delta;
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
    window.addEventListener('popstate', () => {
        lastKnownHref = window.location.href;
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

if (window.location.hostname.includes('tiktok.com')) {
    startMonitoring();
}
