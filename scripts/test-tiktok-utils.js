#!/usr/bin/env node
/** Smoke tests for extension/tiktok_utils.js */

const path = require('path');
const { pathToFileURL } = require('url');

const SAMPLE_ID = '7123456789012345678';

async function main() {
    const utilsPath = path.join(__dirname, '..', 'extension', 'tiktok_utils.js');
    await import(pathToFileURL(utilsPath).href);

    const parse = globalThis.parseTiktokVideoId;
    if (typeof parse !== 'function') {
        throw new Error('parseTiktokVideoId was not exported on globalThis');
    }

    const cases = [
        {
            url: `https://www.tiktok.com/@creator/video/${SAMPLE_ID}`,
            expected: SAMPLE_ID,
            label: 'handle video page',
        },
        {
            url: `https://www.tiktok.com/@creator/video/${SAMPLE_ID}?lang=en`,
            expected: SAMPLE_ID,
            label: 'handle video page with query',
        },
        {
            url: `https://www.tiktok.com/video/${SAMPLE_ID}`,
            expected: SAMPLE_ID,
            label: 'video page without handle',
        },
        {
            url: `https://m.tiktok.com/v/${SAMPLE_ID}.html`,
            expected: SAMPLE_ID,
            label: 'mobile html page',
        },
        {
            url: 'https://www.tiktok.com/foryou',
            expected: null,
            label: 'foryou feed without id',
        },
        {
            url: 'https://www.tiktok.com/@creator/video/tooshort',
            expected: null,
            label: 'invalid id length',
        },
        {
            url: 'https://vm.tiktok.com/ABC123/',
            expected: null,
            label: 'short link out of scope',
        },
        {
            url: 'https://example.com/video/123',
            expected: null,
            label: 'non-tiktok host',
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

    console.log(`All ${cases.length} parseTiktokVideoId tests passed`);
}

main().catch((err) => {
    console.error(err);
    process.exitCode = 1;
});
