#!/usr/bin/env node
/**
 * Selector Stability Audit: AI Mode Button
 * Connects to the live Google Search page and checks which selectors
 * are semantic/stable vs obfuscated CSS classes that will break on Google redeploys.
 */

const PAGE_WS_URL = 'ws://127.0.0.1:9222/devtools/page/5E808C40E7C8EA85A76EF0FA623723D4';

async function main() {
    const ws = new WebSocket(PAGE_WS_URL);
    await new Promise((resolve, reject) => {
        ws.onopen = resolve;
        ws.onerror = (e) => reject(new Error('WS error: ' + e.message));
        setTimeout(() => reject(new Error('Connection timeout')), 5000);
    });

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

    const expr = `(function() {
        const results = {
            stableSelectors: {},
            unstableSelectors: {},
            recommendations: []
        };

        // ── STABLE: semantic href with udm=50 ───────────────────────────────
        const udm50Links = document.querySelectorAll('a[href*="udm=50"]');
        const aiModeUdm50 = Array.from(udm50Links).filter(a => {
            const text = (a.textContent || '').trim().toLowerCase();
            return text === 'ai mode' || text === '';
        });
        results.stableSelectors['a[href*="udm=50"] (AI Mode anchor by URL param)'] = {
            count: aiModeUdm50.length,
            example: aiModeUdm50[0] ? aiModeUdm50[0].outerHTML.substring(0, 300) : null
        };

        // ── STABLE: aria-label containing "AI Mode" ──────────────────────────
        const byAriaLabel = document.querySelectorAll('[aria-label*="AI Mode"], [aria-label*="ai mode"]');
        results.stableSelectors['[aria-label*="AI Mode"]'] = {
            count: byAriaLabel.length,
            tags: Array.from(byAriaLabel).map(el => ({
                tag: el.tagName,
                ariaLabel: el.getAttribute('aria-label'),
                classes: typeof el.className === 'string' ? el.className.substring(0, 80) : ''
            }))
        };

        // ── STABLE: title attribute containing "AI Mode" ────────────────────
        const byTitle = document.querySelectorAll('[title*="AI Mode"], [title*="ai mode"]');
        results.stableSelectors['[title*="AI Mode"]'] = {
            count: byTitle.length,
            tags: Array.from(byTitle).map(el => ({
                tag: el.tagName,
                title: el.getAttribute('title'),
                classes: typeof el.className === 'string' ? el.className.substring(0, 80) : ''
            }))
        };

        // ── STABLE: role="navigation" containing the tabs ───────────────────
        const navWithAiMode = Array.from(document.querySelectorAll('[role="navigation"]'))
            .filter(el => el.textContent.includes('AI Mode'));
        results.stableSelectors['[role="navigation"] containing "AI Mode"'] = {
            count: navWithAiMode.length,
            firstTag: navWithAiMode[0] ? navWithAiMode[0].tagName : null
        };

        // ── SEMI-STABLE: jsname attributes (Google Wiz framework IDs) ────────
        // jsname values are Google-internal component IDs — more stable than CSS classes
        // but still internal and can change across major framework revisions
        const jsnames = {};
        for (const el of document.querySelectorAll('[jsname]')) {
            const jn = el.getAttribute('jsname');
            const text = (el.textContent || '').trim().toLowerCase();
            if (text === 'ai mode' || (el.getAttribute('aria-label') || '').toLowerCase().includes('ai mode')) {
                const attrs = {};
                for (const a of el.attributes) attrs[a.name] = a.value.substring(0, 80);
                jsnames[jn] = { tag: el.tagName, attrs };
            }
        }
        results.unstableSelectors['jsname values (semi-stable, internal Wiz framework)'] = jsnames;

        // ── UNSTABLE: CSS class names (obfuscated, change on redeploy) ───────
        const cssClasses = new Set();
        for (const el of document.querySelectorAll('[aria-label*="AI Mode"], a[href*="udm=50"]')) {
            if (typeof el.className === 'string') {
                el.className.split(' ').filter(Boolean).forEach(c => cssClasses.add(c));
            }
            let parent = el.parentElement;
            for (let i = 0; i < 5; i++) {
                if (!parent) break;
                if (typeof parent.className === 'string') {
                    parent.className.split(' ').filter(Boolean).forEach(c => cssClasses.add(c));
                }
                parent = parent.parentElement;
            }
        }
        results.unstableSelectors['CSS classes (OBFUSCATED, WILL CHANGE)'] = Array.from(cssClasses);

        // ── Check if the jscontroller IDs are also present ───────────────────
        const jscontrollers = {};
        for (const el of document.querySelectorAll('[jscontroller]')) {
            const text = (el.textContent || '').trim();
            if (text === 'AI Mode') {
                jscontrollers[el.getAttribute('jscontroller')] = {
                    tag: el.tagName,
                    classes: typeof el.className === 'string' ? el.className : ''
                };
            }
        }
        results.unstableSelectors['jscontroller values (semi-stable, internal)'] = jscontrollers;

        // ── Recommended stable selectors ─────────────────────────────────────
        // What we SHOULD use vs what we ARE using
        if (aiModeUdm50.length > 0) {
            results.recommendations.push('USE: a[href*="udm=50"] — URL parameter is semantically stable');
        }
        if (byAriaLabel.length > 0) {
            results.recommendations.push('USE: [aria-label*="AI Mode"] — accessibility attribute, stable');
        }
        if (byTitle.length > 0) {
            results.recommendations.push('USE: [title*="AI Mode"] — title attribute, stable');
        }
        results.recommendations.push('AVOID: CSS class selectors like .XVMlrc, .olrp5b — these are obfuscated and WILL change');
        results.recommendations.push('CAUTION: jsname/jscontroller values are semi-stable (Google-internal Wiz IDs)');

        return JSON.stringify(results, null, 2);
    })()`;

    const result = await sendCmd('Runtime.evaluate', { expression: expr, returnByValue: true });
    const data = JSON.parse(result.result.value);

    console.log('\n════════════════════════════════════════════════');
    console.log('   SELECTOR STABILITY AUDIT — Google AI Mode');
    console.log('════════════════════════════════════════════════\n');

    console.log('✅ STABLE SELECTORS (safe to use in production):');
    console.log('─'.repeat(50));
    for (const [selector, info] of Object.entries(data.stableSelectors)) {
        console.log(`\n[${selector}]`);
        console.log(JSON.stringify(info, null, 2));
    }

    console.log('\n⚠️  UNSTABLE / SEMI-STABLE SELECTORS:');
    console.log('─'.repeat(50));
    for (const [selector, info] of Object.entries(data.unstableSelectors)) {
        console.log(`\n[${selector}]`);
        console.log(JSON.stringify(info, null, 2));
    }

    console.log('\n🔧 RECOMMENDATIONS:');
    console.log('─'.repeat(50));
    for (const rec of data.recommendations) {
        console.log(' •', rec);
    }
    console.log('');

    ws.close();
}

main().catch(err => { console.error('Error:', err); process.exitCode = 1; });
