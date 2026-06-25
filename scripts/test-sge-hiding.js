#!/usr/bin/env node
/**
 * Automated Browser Test: Google AI Search Mode Matching (SGE + AI Mode Button Hiding)
 * Launches Chrome/Chromium with the extension loaded, serves a mock Google Search page,
 * and verifies all AI elements are correctly hidden while regular results remain visible.
 *
 * Environment variables (all optional, for CI override):
 *   CHROMIUM_PATH     — path to the chrome/chromium binary
 *   DEBUG_PORT        — Chrome DevTools remote debugging port (default: 9223)
 *   HTTP_PORT         — local HTTP server port (default: 8085)
 *   SCREENSHOTS_DIR   — directory to write PNG screenshots (default: /tmp/ext-test-screenshots)
 */

const { spawn, execSync } = require('child_process');
const http = require('http');
const os = require('os');
const path = require('path');
const fs = require('fs');

// ── Configuration ─────────────────────────────────────────────────────────────

function findChromium() {
    if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH;
    // Prefer Debian packages over Snap wrappers.
    // Note: on Ubuntu 22.04+, /usr/bin/chromium-browser is a Snap stub that
    // drops --load-extension and --remote-debugging-port silently. Avoid it.
    const candidates = [
        '/usr/bin/google-chrome-stable',
        '/usr/bin/google-chrome',
        '/usr/bin/chromium',
        // chromium-browser last — may be a Snap stub on Ubuntu 22.04+
        '/usr/bin/chromium-browser',
        '/snap/bin/chromium',
    ];
    for (const c of candidates) {
        if (fs.existsSync(c)) return c;
    }
    try { return execSync('which google-chrome-stable || which google-chrome || which chromium', { encoding: 'utf8' }).trim(); }
    catch { throw new Error('Could not find Chrome/Chromium. Set CHROMIUM_PATH env var.'); }
}

const PORT            = parseInt(process.env.HTTP_PORT       || '8085', 10);
const DEBUG_PORT      = parseInt(process.env.DEBUG_PORT      || '9223', 10);
const CHROMIUM_PATH   = findChromium();
const EXTENSION_PATH  = path.resolve(__dirname, '..', 'extension');
const USER_DATA_DIR   = path.join(os.tmpdir(), `guardian-ext-test-${process.pid}`);
const SCREENSHOTS_DIR = process.env.SCREENSHOTS_DIR || path.join(os.tmpdir(), 'ext-test-screenshots');
const MOCK_URL        = `http://google.127.0.0.1.nip.io:${PORT}/search.html`;

fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

console.log(`Chrome binary : ${CHROMIUM_PATH}`);
console.log(`Extension     : ${EXTENSION_PATH}`);
console.log(`User data dir : ${USER_DATA_DIR}`);
console.log(`Screenshots   : ${SCREENSHOTS_DIR}`);

async function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

// ── HTTP server for the mock page ─────────────────────────────────────────────

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

// ── Chromium launcher ─────────────────────────────────────────────────────────

function launchChromium() {
    // --no-sandbox is required in CI (unprivileged namespaces are disabled).
    // Extensions require a headed display — use Xvfb in CI (set DISPLAY=:99).
    const args = [
        `--load-extension=${EXTENSION_PATH}`,
        `--disable-extensions-except=${EXTENSION_PATH}`,
        `--remote-debugging-port=${DEBUG_PORT}`,
        '--remote-debugging-address=127.0.0.1',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-gpu',
        '--disable-software-rasterizer',
        '--no-sandbox',           // required for CI
        '--disable-setuid-sandbox',
        `--user-data-dir=${USER_DATA_DIR}`,
        MOCK_URL
    ];
    const proc = spawn(CHROMIUM_PATH, args, {
        env: { ...process.env },  // inherit DISPLAY so Xvfb is used
    });
    proc.stderr.on('data', (d) => {
        const line = d.toString().trim();
        if (line.includes('DevTools listening on') || line.includes('ERROR') || line.includes('FATAL')) {
            console.log(`[Chrome] ${line}`);
        }
    });
    proc.on('error', (err) => console.error('[Chrome] Spawn error:', err));
    proc.on('exit', (code, sig) => {
        if (code !== null && code !== 0) console.log(`[Chrome] Exited with code ${code} signal ${sig}`);
    });
    return proc;
}

// ── CDP helpers ───────────────────────────────────────────────────────────────

// Poll the DevTools JSON endpoint until Chromium is ready (max 30 s).
async function waitForChromiumReady(maxMs = 30_000) {
    const deadline = Date.now() + maxMs;
    let lastErr;
    while (Date.now() < deadline) {
        try {
            const targets = await fetchJson(`http://127.0.0.1:${DEBUG_PORT}/json`);
            return targets;
        } catch (e) {
            lastErr = e;
            await sleep(500);
        }
    }
    throw new Error(`Chromium DevTools not available after ${maxMs}ms: ${lastErr?.message}`);
}

function fetchJson(url) {
    return new Promise((resolve, reject) => {
        const req = http.get(url, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try { resolve(JSON.parse(data)); }
                catch (e) { reject(e); }
            });
        });
        req.on('error', reject);
        setTimeout(() => reject(new Error('Timeout')), 4000);
    });
}

function connectWs(url) {
    return new Promise((resolve, reject) => {
        const ws = new WebSocket(url);
        ws.onopen  = () => resolve(ws);
        ws.onerror = (e) => reject(new Error('WS error: ' + (e.message || 'unknown')));
        setTimeout(() => reject(new Error('WS connection timeout')), 5000);
    });
}

// Chrome ships a built-in Hangouts component extension (background.html) that
// activates on google.* pages. Only match our MV3 service_worker background.js.
const COMPONENT_EXTENSION_IDS = new Set([
    'nkeimhogjdpnpccoofpliimaahmaaome', // Google Hangouts / Meet services
]);

function findGuardianServiceWorker(targets) {
    return targets.find((t) => {
        if (t.type !== 'service_worker' || !t.url.includes('/background.js')) {
            return false;
        }
        const match = t.url.match(/^chrome-extension:\/\/([^/]+)\//);
        const extId = match ? match[1] : '';
        return extId && !COMPONENT_EXTENSION_IDS.has(extId);
    });
}

async function warmGuardianServiceWorker(label = 'service worker') {
    const deadline = Date.now() + 10_000;
    while (Date.now() < deadline) {
        const targets = await fetchJson(`http://127.0.0.1:${DEBUG_PORT}/json`);
        const bgTarget = findGuardianServiceWorker(targets);
        if (bgTarget) {
            console.log(`Pre-warming Guardian ${label} via CDP (${bgTarget.url})...`);
            const bgWs = await connectWs(bgTarget.webSocketDebuggerUrl);
            const bgRpc = makeRpc(bgWs);
            await bgRpc('Runtime.evaluate', { expression: '1+1', returnByValue: true });
            bgWs.close();
            console.log('Guardian service worker warmed.');
            return true;
        }
        await sleep(300);
    }
    console.log(`No Guardian service worker found for ${label} — continuing without warmup.`);
    return false;
}

function makeRpc(ws) {
    let msgId = 1;
    return (method, params = {}) => new Promise((resolve, reject) => {
        const id = msgId++;
        const handler = (event) => {
            const r = JSON.parse(event.data);
            if (r.id === id) {
                ws.removeEventListener('message', handler);
                r.error ? reject(new Error(r.error.message)) : resolve(r.result);
            }
        };
        ws.addEventListener('message', handler);
        ws.send(JSON.stringify({ id, method, params }));
    });
}

// ── Screenshot helper ─────────────────────────────────────────────────────────

async function takeScreenshot(sendCmd, name) {
    const { data } = await sendCmd('Page.captureScreenshot', { format: 'png', quality: 90 });
    const filePath = path.join(SCREENSHOTS_DIR, `${name}.png`);
    fs.writeFileSync(filePath, Buffer.from(data, 'base64'));
    console.log(`📸  Screenshot saved: ${filePath}`);
    return filePath;
}

// ── Main test ─────────────────────────────────────────────────────────────────

async function main() {
    let httpServer   = null;
    let chromiumProc = null;
    let ws           = null;

    try {
        console.log('\nStep 1: Starting HTTP server...');
        httpServer = await startHttpServer();
        console.log(`HTTP server on port ${PORT}`);

        console.log('Step 2: Launching Chrome with extension...');
        chromiumProc = launchChromium();

        console.log('Step 3: Waiting for Chrome DevTools to become ready...');
        const targets = await waitForChromiumReady(30_000);
        console.log('DevTools targets:', targets.map(t => t.url));

        // ── Step 3a: Warm the MV3 service worker ─────────────────────────────
        // In MV3, the background service worker can be killed between events.
        // If it is not alive when content.js sends CHECK_AI_POLICY, the message
        // is silently dropped and the callback receives undefined → no CSS injection.
        // On google.* pages Chrome also exposes its built-in Hangouts extension
        // (background.html) in DevTools — never warm that by mistake.
        console.log('Step 3a: Pre-warming Guardian service worker via CDP...');
        await warmGuardianServiceWorker('initial load');

        // ── Step 3b: Connect to the test page and reload it ───────────────────
        // The page was already loaded before we warmed the worker. Reload it now
        // so the content script fires against the (now-live) service worker.
        const target = targets.find(t => t.url.includes('search.html'));
        if (!target) throw new Error('Could not find the search.html tab — available: ' + targets.map(t => t.url).join(', '));

        ws = await connectWs(target.webSocketDebuggerUrl);
        console.log('WebSocket connected to test page.');

        const sendCmd = makeRpc(ws);

        // Enable domains
        await sendCmd('Page.enable');
        await sendCmd('Runtime.enable');

        // Forward page console messages to the test output (useful for debugging)
        ws.addEventListener('message', (event) => {
            const msg = JSON.parse(event.data);
            if (msg.method === 'Runtime.consoleAPICalled') {
                const args = (msg.params.args || []).map(a => a.value ?? a.description ?? '').join(' ');
                console.log(`  [page:${msg.params.type}] ${args}`);
            }
        });

        console.log('Reloading test page so content script fires against live service worker...');
        await Promise.all([
            sendCmd('Page.reload', {}),
            new Promise((resolve) => {
                const handler = (event) => {
                    const msg = JSON.parse(event.data);
                    if (msg.method === 'Page.loadEventFired') {
                        ws.removeEventListener('message', handler);
                        resolve();
                    }
                };
                ws.addEventListener('message', handler);
                setTimeout(resolve, 5000); // bail-out after 5 s if event never fires
            })
        ]);
        console.log('Page reloaded.');

        // Give the content script time to run and receive the policy response
        console.log('Waiting 3 s for extension content script to initialise...');
        await sleep(3000);

        // Screenshot 1: page state after extension has had time to run
        await takeScreenshot(sendCmd, '01-after-extension-init');

        console.log('\nStep 4: Evaluating DOM state...');

        const evalExpr = `(() => {
            const vis    = (el) => el ? window.getComputedStyle(el).display !== 'none' : null;
            const hidden = (el) => el ? window.getComputedStyle(el).display === 'none'  : null;

            return {
                // Style injection
                styleInjected: !!document.getElementById('guardian-hide-sge'),

                // CSS: SGE data-attribute containers
                dataSgeHidden:  hidden(document.getElementById('sge-1')),
                sgeClassHidden: hidden(document.getElementById('sge-class-1')),

                // CSS: AI Mode tab link — href contains udm=50 (stable URL parameter)
                aiModeTabLinkHidden: hidden(document.getElementById('ai-mode-tab-link')),

                // CSS: AI Mode history sidebar button — aria-label (stable accessibility attr)
                aiModeHistoryBtnHidden:  hidden(document.getElementById('ai-mode-history-btn')),
                aiModeHistoryLinkHidden: hidden(document.getElementById('ai-mode-history-link')),
                aiModeNewSearchHidden:   hidden(document.getElementById('ai-mode-new-search')),

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
                result1Visible:        vis(document.getElementById('result-1')),
                result2Visible:        vis(document.getElementById('result-2')),
                normalButtonVisible:   vis(document.getElementById('normal-button')),
                normalListitemVisible: vis(document.getElementById('normal-listitem')),
                tabAllVisible:         vis(document.getElementById('tab-all')),
                tabImagesVisible:      vis(document.getElementById('tab-images')),
            };
        })()`;

        const result = await sendCmd('Runtime.evaluate', { expression: evalExpr, returnByValue: true });
        const r = result.result.value;

        console.log('\n─────────────────────────────────────────────');
        console.log('         GUARDIAN EXTENSION TEST REPORT');
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
            r.dataSgeHidden         === true &&
            r.sgeClassHidden        === true &&
            r.aiModeTabLinkHidden   === true &&
            r.aiModeHistoryBtnHidden  === true &&
            r.aiModeHistoryLinkHidden === true &&
            r.aiModeNewSearchHidden   === true &&
            r.aiModeHomepageBtnHidden    === true &&
            r.aiModeTabTextmatchHidden   === true &&
            r.aiOverviewHidden           === true &&
            r.result1Visible        === true &&
            r.result2Visible        === true &&
            r.normalButtonVisible   === true &&
            r.normalListitemVisible === true &&
            r.tabAllVisible         === true &&
            r.tabImagesVisible      === true;

        // Screenshot 2: final state (same whether pass or fail — gives visual proof)
        await takeScreenshot(sendCmd, allPassed ? '02-test-passed' : '02-test-failed');

        if (allPassed) {
            console.log('✅ ALL TESTS PASSED — Google AI Mode hiding (stable selectors) verified!');
            process.exitCode = 0;
        } else {
            console.error('❌ SOME TESTS FAILED — see report and screenshots above.');
            process.exitCode = 1;
        }

    } catch (err) {
        console.error('Fatal error:', err);
        // Best-effort screenshot on fatal error
        if (ws) {
            try {
                const sendCmd = makeRpc(ws);
                await takeScreenshot(sendCmd, '02-fatal-error');
            } catch { /* ignore */ }
        }
        process.exitCode = 1;
    } finally {
        if (ws) ws.close();
        if (chromiumProc) {
            chromiumProc.kill('SIGTERM');
            setTimeout(() => { try { chromiumProc.kill('SIGKILL'); } catch { } }, 800);
        }
        if (httpServer) httpServer.kill('SIGKILL');
        console.log('Test runner done.');
        console.log(`Screenshots written to: ${SCREENSHOTS_DIR}`);
    }
}

main();
