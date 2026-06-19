// Shared TikTok video ID extraction for content and background scripts.
// Exposed on globalThis for importScripts / content script / Node tests.

const TIKTOK_VIDEO_ID_RE = /^\d{10,25}$/;

function isTiktokHostname(hostname) {
    return hostname === 'tiktok.com' ||
        hostname === 'www.tiktok.com' ||
        hostname === 'm.tiktok.com' ||
        hostname.endsWith('.tiktok.com');
}

function parseTiktokVideoId(urlString) {
    if (!urlString || typeof urlString !== 'string') {
        return null;
    }

    let parsed;
    try {
        parsed = new URL(urlString);
    } catch (e) {
        return null;
    }

    if (!isTiktokHostname(parsed.hostname)) {
        return null;
    }

    const pathMatch = parsed.pathname.match(
        /^\/@[^/]+\/video\/(\d{10,25})(?:\/|$)/
    ) || parsed.pathname.match(
        /^\/video\/(\d{10,25})(?:\/|$)/
    ) || parsed.pathname.match(
        /^\/v\/(\d{10,25})\.html(?:\/|$)/
    );

    if (pathMatch && TIKTOK_VIDEO_ID_RE.test(pathMatch[1])) {
        return pathMatch[1];
    }

    return null;
}

globalThis.parseTiktokVideoId = parseTiktokVideoId;
