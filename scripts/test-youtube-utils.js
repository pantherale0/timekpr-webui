#!/usr/bin/env node
/** Smoke tests for extension/youtube_utils.js */

const path = require('path');
const { pathToFileURL } = require('url');

const SAMPLE_ID = 'dQw4w9WgXcQ';

async function main() {
    const utilsPath = path.join(__dirname, '..', 'extension', 'youtube_utils.js');
    await import(pathToFileURL(utilsPath).href);

    const parse = globalThis.parseYoutubeVideoId;
    if (typeof parse !== 'function') {
        throw new Error('parseYoutubeVideoId was not exported on globalThis');
    }

    const cases = [
        {
            url: `https://www.youtube.com/watch?v=${SAMPLE_ID}`,
            expected: SAMPLE_ID,
            label: 'watch page with v param',
        },
        {
            url: `https://youtube.com/watch?v=${SAMPLE_ID}&t=42s`,
            expected: SAMPLE_ID,
            label: 'watch page with extra query params',
        },
        {
            url: `https://m.youtube.com/watch?v=${SAMPLE_ID}`,
            expected: SAMPLE_ID,
            label: 'mobile watch page',
        },
        {
            url: `https://www.youtube.com/shorts/${SAMPLE_ID}`,
            expected: SAMPLE_ID,
            label: 'shorts page',
        },
        {
            url: `https://www.youtube.com/shorts/${SAMPLE_ID}?feature=share`,
            expected: SAMPLE_ID,
            label: 'shorts page with query string',
        },
        {
            url: `https://www.youtube.com/embed/${SAMPLE_ID}`,
            expected: SAMPLE_ID,
            label: 'embed page',
        },
        {
            url: `https://www.youtube.com/live/${SAMPLE_ID}`,
            expected: SAMPLE_ID,
            label: 'live page',
        },
        {
            url: 'https://www.youtube.com/shorts',
            expected: null,
            label: 'shorts index without id',
        },
        {
            url: 'https://www.youtube.com/shorts/tooshort',
            expected: null,
            label: 'shorts path with invalid id length',
        },
        {
            url: 'https://youtu.be/dQw4w9WgXcQ',
            expected: null,
            label: 'youtu.be out of scope',
        },
        {
            url: 'https://example.com/watch?v=dQw4w9WgXcQ',
            expected: null,
            label: 'non-youtube host',
        },
        {
            url: '',
            expected: null,
            label: 'empty string',
        },
        {
            url: null,
            expected: null,
            label: 'null input',
        },
    ];

    let failed = 0;
    for (const testCase of cases) {
        const actual = parse(testCase.url);
        if (actual !== testCase.expected) {
            failed += 1;
            console.error(
                `FAIL [${testCase.label}]: expected ${testCase.expected}, got ${actual}`
            );
        }
    }

    if (failed > 0) {
        process.exitCode = 1;
        console.error(`${failed} test(s) failed`);
        return;
    }

    console.log(`All ${cases.length} parseYoutubeVideoId tests passed`);
}

main().catch((err) => {
    console.error(err);
    process.exitCode = 1;
});
