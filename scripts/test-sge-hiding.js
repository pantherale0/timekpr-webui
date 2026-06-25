#!/usr/bin/env node
/**
 * Automated Browser Test: Google AI Search Mode Matching (SGE + AI Mode Button Hiding)
 * Launches Chromium with the extension loaded, serves a mock Google Search page,
 * and verifies all AI elements are correctly hidden while regular results remain visible.
 *
 * Environment variables (all optional, for CI override):
 *   CHROMIUM_PATH   — path to the chromium/google-chrome binary
 *   DEBUG_PORT      — Chrome DevTools remote debugging port (default: 9223)
 *   HTTP_PORT       — local HTTP server port (default: 8085)
 */

const { spawn, execSync } = require('child_process');
const http = require('http');
const os = require('os');
const path = require('path');
const fs = require('fs');

// ── Configuration ────────────────────────────────────────────────────────────

function findChromium() {
    if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH;
    const candidates = [
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/snap/bin/chromium',
    ];
    for (const c of candidates) {
        if (fs.existsSync(c)) return c;
    }
    // Fall back to 'which'
    try { return execSync('which chromium || which google-chrome', { encoding: 'utf8' }).trim(); }
    catch { throw new Error('Could not find Chromium. Set CHROMIUM_PATH env var.'); }
}

const PORT       = parseInt(process.env.HTTP_PORT  || '8085', 10);
const DEBUG_PORT = parseInt(process.env.DEBUG_PORT || '9223', 10);
const CHROMIUM_PATH   = findChromium();
const EXTENSION_PATH  = path.resolve(__dirname, '..', 'extension');
const USER_DATA_DIR   = path.join(os.tmpdir(), `guardian-ext-test-${process.pid}`);
const MOCK_URL = `http://google.127.0.0.1.nip.io:${PORT}/search.html`;

console.log(`Chromium: ${CHROMIUM_PATH}`);
console.log(`Extension: ${EXTENSION_PATH}`);
console.log(`User data dir: ${USER_DATA_DIR}`);

async function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }


function startHttpServer() {
    return new Promise((resolve) => {
        const server = spawn('python3', ['-m', 'http.server', PORT.toString(), '--directory', EXTENSION_PATH]);
        server.stderr.on('data', (d) => {
            const line = d.toString().trim();
            if (line) console.log(`[HTTP] ${line}`);
        });
        server.on('error', (err) => console.error('[HTTP] Error:', err));
        setTimeout(() => resolve(server), 800);
    });
}

function launchChromium() {
    return new Promise((resolve) => {
        // --no-sandbox is required in CI environments (no user namespace support).
        // Extensions require a headed display — use Xvfb in CI (xvfb-run wraps this call).
        const args = [
            `--load-extension=${EXTENSION_PATH}`,
            `--disable-extensions-except=${EXTENSION_PATH}`,
            `--remote-debugging-port=${DEBUG_PORT}`,
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-gpu',
            '--disable-software-rasterizer',
            '--no-sandbox',                   // required for CI (unprivileged namespaces disabled)
            '--disable-setuid-sandbox',
            `--user-data-dir=${USER_DATA_DIR}`,
            MOCK_URL
        ];
        const proc = spawn(CHROMIUM_PATH, args);
        proc.stderr.on('data', (d) => {
            const line = d.toString().trim();
            if (line.includes('DevTools listening on')) console.log(`[Chromium] ${line}`);
        });
        proc.on('error', (err) => console.error('[Chromium] Error:', err));
        setTimeout(() => resolve(proc), 3500);
    });
}

async function getPageTarget() {
    return new Promise((resolve, reject) => {
        const req = http.get(`http://127.0.0.1:${DEBUG_PORT}/json`, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try { resolve(JSON.parse(data)); }
                catch (e) { reject(e); }
            });
        });
        req.on('error', reject);
        setTimeout(() => reject(new Error('Timeout fetching DevTools targets')), 5000);
    });
}

async function main() {
    let httpServer = null;
    let chromiumProc = null;
    let ws = null;

    try {
        console.log('Step 1: Starting HTTP server...');
        httpServer = await startHttpServer();
        console.log(`HTTP server on port ${PORT}`);

        console.log('Step 2: Launching Chromium with extension...');
        chromiumProc = await launchChromium();
        console.log('Chromium launched.');

        console.log('Step 3: Fetching DevTools targets...');
        const targets = await getPageTarget();
        console.log('Targets:', targets.map(t => t.url));

        const target = targets.find(t => t.url.includes('search.html'));
        if (!target) throw new Error('Could not find the search.html tab in Chromium');

        ws = new WebSocket(target.webSocketDebuggerUrl);
        await new Promise((resolve, reject) => {
            ws.onopen = resolve;
            ws.onerror = (e) => reject(new Error('WebSocket failed: ' + (e.message || 'unknown')));
            setTimeout(() => reject(new Error('WebSocket connection timeout')), 5000);
        });

        console.log('WebSocket connected. Waiting 3 seconds for extension to initialise...');
        await sleep(3000);

        let msgId = 1;
        const sendCmd = (method, params = {}) => new Promise((resolve, reject) => {
            const id = msgId++;
            const handler = (event) => {
                const r = JSON.parse(event.data);
                if (r.id === id) {
                    ws.removeEventListener('message', handler);
                    r.error ? reject(r.error) : resolve(r.result);
                }
            };
            ws.addEventListener('message', handler);
            ws.send(JSON.stringify({ id, method, params }));
        });

        console.log('Step 4: Evaluating DOM state...');

        const evalExpr = `(() => {
            const vis = (el) => el ? window.getComputedStyle(el).display !== 'none' : null;
            const hidden = (el) => el ? window.getComputedStyle(el).display === 'none' : null;

            return {
                // Style injection
                styleInjected: !!document.getElementById('guardian-hide-sge'),

                // CSS: SGE data-attribute containers
                dataSgeHidden:  hidden(document.getElementById('sge-1')),
                sgeClassHidden: hidden(document.getElementById('sge-class-1')),

                // CSS: AI Mode tab link — href contains udm=50 (stable URL parameter)
                aiModeTabLinkHidden: hidden(document.getElementById('ai-mode-tab-link')),

                // CSS: AI Mode history sidebar button — aria-label (stable accessibility attr)
                aiModeHistoryBtnHidden: hidden(document.getElementById('ai-mode-history-btn')),
                aiModeHistoryLinkHidden: hidden(document.getElementById('ai-mode-history-link')),
                aiModeNewSearchHidden: hidden(document.getElementById('ai-mode-new-search')),

                // JS text-walk: homepage AI Mode button (no stable CSS class)
                aiModeHomepageBtnHidden: hidden(document.getElementById('ai-mode-homepage-btn')),

                // JS text-walk: search tab bar listitem with text "AI Mode"
                aiModeTabTextmatchHidden: hidden(document.getElementById('ai-mode-tab-textmatch')),

                // MutationObserver: AI Overview heading container
                aiOverviewHidden: (() => {
                    const aiCard = document.querySelector('.ai-card');
                    if (!aiCard) return null;
                    let parent = aiCard;
                    for (let i = 0; i < 5; i++) {
                        if (parent && window.getComputedStyle(parent).display === 'none') return true;
                        parent = parent ? parent.parentElement : null;
                    }
                    return false;
                })(),

                // Things that MUST remain visible
                result1Visible:     vis(document.getElementById('result-1')),
                result2Visible:     vis(document.getElementById('result-2')),
                normalButtonVisible: vis(document.getElementById('normal-button')),
                normalListitemVisible: vis(document.getElementById('normal-listitem')),
                tabAllVisible:      vis(document.getElementById('tab-all')),
                tabImagesVisible:   vis(document.getElementById('tab-images')),
            };
        })()`;

        const result = await sendCmd('Runtime.evaluate', { expression: evalExpr, returnByValue: true });
        const r = result.result.value;

        console.log('\n─────────────────────────────────────────────');
        console.log('           GUARDIAN EXTENSION TEST REPORT');
        console.log('─────────────────────────────────────────────');
        const check = (label, value) => {
            const icon = value === true ? '✅' : value === false ? '❌' : '⚠️ (null)';
            console.log(`${icon}  ${label}: ${value}`);
        };

        check('Style tag #guardian-hide-sge injected',            r.styleInjected);
        check('[CSS] div[data-sge] hidden',                        r.dataSgeHidden);
        check('[CSS] div.sge hidden',                              r.sgeClassHidden);
        check('[CSS] AI Mode tab link (href udm=50) hidden',       r.aiModeTabLinkHidden);
        check('[CSS] AI Mode history button (aria-label) hidden',  r.aiModeHistoryBtnHidden);
        check('[CSS] AI Mode history link (title attr) hidden',    r.aiModeHistoryLinkHidden);
        check('[CSS] "Start new AI Mode search" link hidden',      r.aiModeNewSearchHidden);
        check('[JS]  AI Mode homepage button (text-walk) hidden',  r.aiModeHomepageBtnHidden);
        check('[JS]  AI Mode listitem (text-walk) hidden',         r.aiModeTabTextmatchHidden);
        check('[JS]  AI Overview (MutationObserver) hidden',       r.aiOverviewHidden);
        check('[VIS] Regular result 1 visible',                    r.result1Visible);
        check('[VIS] Regular result 2 visible',                    r.result2Visible);
        check('[VIS] Normal button (not AI) visible',              r.normalButtonVisible);
        check('[VIS] Normal listitem (not AI) visible',            r.normalListitemVisible);
        check('[VIS] "All" tab visible',                           r.tabAllVisible);
        check('[VIS] "Images" tab visible',                        r.tabImagesVisible);
        console.log('─────────────────────────────────────────────\n');

        const allPassed =
            r.styleInjected &&
            r.dataSgeHidden === true &&
            r.sgeClassHidden === true &&
            r.aiModeTabLinkHidden === true &&
            r.aiModeHistoryBtnHidden === true &&
            r.aiModeHistoryLinkHidden === true &&
            r.aiModeNewSearchHidden === true &&
            r.aiModeHomepageBtnHidden === true &&
            r.aiModeTabTextmatchHidden === true &&
            r.aiOverviewHidden === true &&
            r.result1Visible === true &&
            r.result2Visible === true &&
            r.normalButtonVisible === true &&
            r.normalListitemVisible === true &&
            r.tabAllVisible === true &&
            r.tabImagesVisible === true;

        if (allPassed) {
            console.log('✅ ALL TESTS PASSED — Google AI Mode hiding (SGE + AI Mode button, stable selectors) verified!');
            process.exitCode = 0;
        } else {
            console.error('❌ SOME TESTS FAILED — review the report above.');
            process.exitCode = 1;
        }

    } catch (err) {
        console.error('Fatal error:', err);
        process.exitCode = 1;
    } finally {
        if (ws) ws.close();
        if (chromiumProc) {
            chromiumProc.kill('SIGTERM');
            setTimeout(() => { try { chromiumProc.kill('SIGKILL'); } catch (e) {} }, 500);
        }
        if (httpServer) httpServer.kill('SIGKILL');
        console.log('Test runner done.');
    }
}

main();
