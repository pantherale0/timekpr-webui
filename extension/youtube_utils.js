// Shared YouTube video ID extraction for content and background scripts.
// Exposed on globalThis for importScripts / content script / Node tests.

const YOUTUBE_VIDEO_ID_RE = /^[A-Za-z0-9_-]{11}$/;

function isYoutubeHostname(hostname) {
    return hostname === 'youtube.com' ||
        hostname === 'www.youtube.com' ||
        hostname === 'm.youtube.com' ||
        hostname.endsWith('.youtube.com');
}

function parseYoutubeVideoId(urlString) {
    if (!urlString || typeof urlString !== 'string') {
        return null;
    }

    let parsed;
    try {
        parsed = new URL(urlString);
    } catch (e) {
        return null;
    }

    if (!isYoutubeHostname(parsed.hostname)) {
        return null;
    }

    const queryId = parsed.searchParams.get('v');
    if (queryId && YOUTUBE_VIDEO_ID_RE.test(queryId)) {
        return queryId;
    }

    const pathMatch = parsed.pathname.match(
        /^\/(?:shorts|embed|live)\/([A-Za-z0-9_-]{11})(?:\/|$)/
    );
    if (pathMatch && YOUTUBE_VIDEO_ID_RE.test(pathMatch[1])) {
        return pathMatch[1];
    }

    return null;
}

globalThis.parseYoutubeVideoId = parseYoutubeVideoId;
