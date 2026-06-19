/**
 * Guardian SPA client router — swaps HTML fragments without full page reloads.
 */
(function () {
    'use strict';

    const FRAGMENT_HEADER = 'X-Guardian-SPA';
    const NAV_SLOT_IDS = {
        top_nav_left: 'spa-slot-top-nav-left',
        top_nav_brand_status: 'spa-slot-top-nav-brand-status',
        top_nav_center: 'spa-slot-top-nav-center',
        top_nav_secondary: 'spa-slot-top-nav-secondary',
        top_nav_extra: 'spa-slot-top-nav-extra',
        top_nav_primary: 'spa-slot-top-nav-primary',
        top_nav_overflow: 'spa-slot-top-nav-overflow',
    };
    const REGION_NAMES = Object.keys(NAV_SLOT_IDS).concat(['extra_head']);

    const mainEl = document.getElementById('spa-main');
    if (!mainEl) {
        return;
    }

    const navDefaults = {};
    const teardownCallbacks = [];
    let managedHeadNodes = [];
    let currentPath = normalizePath(mainEl.dataset.initialPath || window.location.pathname);

    window.GuardianSPA = {
        onRoute(fn) {
            if (typeof fn === 'function') {
                teardownCallbacks.push(fn);
            }
        },
        navigate(path, options) {
            return navigateTo(normalizePath(path), options || {});
        },
        getCurrentPath() {
            return currentPath;
        },
    };

    function normalizePath(path) {
        if (!path) {
            return '/dashboard';
        }
        const url = new URL(path, window.location.origin);
        return url.pathname.replace(/\/+$/, '') || '/dashboard';
    }

    function captureNavDefaults() {
        Object.entries(NAV_SLOT_IDS).forEach(([key, id]) => {
            const el = document.getElementById(id);
            if (el) {
                navDefaults[key] = el.outerHTML;
            }
        });
    }

    function restoreNavSlot(key) {
        const id = NAV_SLOT_IDS[key];
        const html = navDefaults[key];
        const current = document.getElementById(id);
        if (current && html) {
            current.outerHTML = html;
        }
    }

    function resetNavChrome() {
        Object.keys(NAV_SLOT_IDS).forEach(restoreNavSlot);
    }

    function applyNavSlot(key, html) {
        const id = NAV_SLOT_IDS[key];
        const trimmed = (html || '').trim();
        if (!trimmed) {
            restoreNavSlot(key);
            return;
        }

        let el = document.getElementById(id);
        if (!el) {
            restoreNavSlot(key);
            el = document.getElementById(id);
        }
        if (!el) {
            return;
        }

        if (trimmed.startsWith('<li')) {
            el.outerHTML = trimmed.replace(/^\s*<li\b/i, (match) => {
                return match.replace('<li', `<li id="${id}" data-spa-nav-slot="${key}"`);
            });
            return;
        }

        el.innerHTML = trimmed;
        el.style.display = '';
        if (key === 'top_nav_extra' || key === 'top_nav_primary') {
            el.style.display = '';
        }
        if (key === 'top_nav_brand_status' && trimmed) {
            el.style.display = '';
        }
    }

    function applyNavChrome(regions) {
        resetNavChrome();
        Object.keys(NAV_SLOT_IDS).forEach((key) => {
            if (regions[key]) {
                applyNavSlot(key, regions[key]);
            }
        });
    }

    function clearManagedHead() {
        managedHeadNodes.forEach((node) => node.remove());
        managedHeadNodes = [];
    }

    function applyExtraHead(html) {
        clearManagedHead();
        const trimmed = (html || '').trim();
        if (!trimmed) {
            return;
        }

        const wrapper = document.createElement('div');
        wrapper.innerHTML = trimmed;
        Array.from(wrapper.children).forEach((node) => {
            if (!['LINK', 'STYLE', 'SCRIPT'].includes(node.tagName)) {
                return;
            }
            const clone = node.cloneNode(true);
            clone.setAttribute('data-spa-managed', 'true');
            if (clone.tagName === 'SCRIPT' && clone.src) {
                const script = document.createElement('script');
                Array.from(clone.attributes).forEach((attr) => script.setAttribute(attr.name, attr.value));
                script.setAttribute('data-spa-managed', 'true');
                document.head.appendChild(script);
                managedHeadNodes.push(script);
            } else {
                document.head.appendChild(clone);
                managedHeadNodes.push(clone);
            }
        });
    }

    function extractFragment(html) {
        const regions = {};
        let content = html;

        const titleMatch = content.match(/<!--\s*spa-title:([\s\S]*?)-->/);
        const title = titleMatch ? titleMatch[1].trim() : null;
        content = content.replace(/<!--\s*spa-title:[\s\S]*?-->\n?/, '');

        REGION_NAMES.forEach((name) => {
            const pattern = new RegExp(
                `<!--\\s*spa-region:${name}\\s*-->([\\s\\S]*?)<!--\\s*/spa-region:${name}\\s*-->`,
                'i',
            );
            const match = content.match(pattern);
            if (match) {
                regions[name] = match[1].trim();
                content = content.replace(match[0], '');
            }
        });

        return { regions, content: content.trim(), title };
    }

    function isSpaLink(anchor) {
        if (!anchor || anchor.tagName !== 'A') {
            return false;
        }
        const href = anchor.getAttribute('href');
        if (!href || href.startsWith('#') || href.startsWith('mailto:') || href.startsWith('tel:')) {
            return false;
        }
        if (anchor.target === '_blank' || anchor.hasAttribute('download')) {
            return false;
        }
        if (anchor.dataset.spaIgnore === 'true') {
            return false;
        }
        const url = new URL(href, window.location.origin);
        if (url.origin !== window.location.origin) {
            return false;
        }
        if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/static/')) {
            return false;
        }
        if (url.pathname === '/' || url.pathname === '/logout' || url.pathname === '/callback') {
            return false;
        }
        return true;
    }

    function runTeardown() {
        while (teardownCallbacks.length) {
            const fn = teardownCallbacks.pop();
            try {
                fn(currentPath);
            } catch (err) {
                console.error('GuardianSPA teardown error', err);
            }
        }
        clearManagedHead();
    }

    function markExistingScripts(container) {
        document.querySelectorAll('script[src]').forEach((script) => {
            if (container && container.contains(script)) {
                return;
            }
            if (script.dataset.spaLoadedSrc) {
                return;
            }
            try {
                script.setAttribute('data-spa-loaded-src', new URL(script.src, window.location.origin).pathname);
            } catch (_) {
                // ignore malformed script URLs
            }
        });
    }

    function executeScripts(container) {
        markExistingScripts(container);
        const scripts = Array.from(container.querySelectorAll('script'));
        scripts.forEach((oldScript) => {
            const src = oldScript.getAttribute('src');
            if (src) {
                const canonicalSrc = new URL(src, window.location.origin).pathname;
                if (document.querySelector(`script[data-spa-loaded-src="${canonicalSrc}"]`)) {
                    oldScript.remove();
                    return;
                }
                const script = document.createElement('script');
                Array.from(oldScript.attributes).forEach((attr) => {
                    script.setAttribute(attr.name, attr.value);
                });
                script.setAttribute('data-spa-loaded-src', canonicalSrc);
                oldScript.replaceWith(script);
                return;
            }

            const code = (oldScript.textContent || '').trim();
            oldScript.remove();
            if (!code) {
                return;
            }

            try {
                // Run in a fresh function scope on every navigation (avoids const redeclaration).
                const runner = new Function(code);
                runner();
            } catch (err) {
                console.error('GuardianSPA fragment script failed:', err);
            }
        });
    }

    function dispatchPageReady(path) {
        document.dispatchEvent(new CustomEvent('guardian:page-ready', {
            detail: { path },
        }));
    }

    function renderFragment(html, path, options) {
        document.dispatchEvent(new CustomEvent('guardian:route', { detail: { path } }));

        const parsed = extractFragment(html);
        applyNavChrome(parsed.regions);
        applyExtraHead(parsed.regions.extra_head);
        mainEl.innerHTML = parsed.content;
        executeScripts(mainEl);

        if (parsed.title) {
            document.title = parsed.title;
        }

        currentPath = path;
        updateSidebarActive(path);

        if (options.pushState !== false) {
            const hash = options.preserveHash === false ? '' : (window.location.hash || '');
            history.pushState({ spaPath: path }, parsed.title || document.title, path + (options.search || window.location.search) + hash);
        }

        window.scrollTo(0, 0);
        dispatchPageReady(path);
    }

    function updateSidebarActive(path) {
        document.querySelectorAll('[data-spa-nav]').forEach((link) => {
            const navPath = link.dataset.spaNav;
            const isActive = path === navPath
                || (navPath !== '/dashboard' && path.startsWith(navPath + '/'))
                || (navPath === '/admin/users' && path === '/admin');
            link.classList.toggle('active', isActive);
        });
    }

    async function navigateTo(path, options) {
        const pushState = options.pushState !== false;
        const search = options.search != null ? options.search : window.location.search;

        if (path === currentPath && !options.force && search === window.location.search) {
            return;
        }

        runTeardown();

        let html;
        try {
            const fragmentPath = path.replace(/^\//, '') + search;
            const response = await fetch('/ui/fragment/' + fragmentPath, {
                headers: { [FRAGMENT_HEADER]: 'fragment' },
                credentials: 'same-origin',
            });
            if (response.status === 401) {
                window.location.href = '/';
                return;
            }
            if (!response.ok) {
                throw new Error('Fragment load failed: ' + response.status);
            }
            html = await response.text();
        } catch (err) {
            console.error('SPA navigation failed, falling back to full load', err);
            window.location.href = path + search;
            return;
        }

        renderFragment(html, path, { pushState, search });
    }

    function handleClick(event) {
        const anchor = event.target.closest('a');
        if (!isSpaLink(anchor)) {
            return;
        }
        event.preventDefault();
        const url = new URL(anchor.href, window.location.origin);
        navigateTo(normalizePath(url.pathname), { search: url.search, force: true });
    }

    document.addEventListener('click', handleClick);

    window.addEventListener('popstate', (event) => {
        const path = normalizePath(
            (event.state && event.state.spaPath) || window.location.pathname,
        );
        navigateTo(path, { pushState: false, force: true });
    });

    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) {
            return;
        }
        if (form.dataset.spaIgnore === 'true') {
            return;
        }
        const method = (form.method || 'GET').toUpperCase();
        if (method !== 'GET') {
            return;
        }
        const action = form.getAttribute('action') || window.location.pathname;
        const url = new URL(action, window.location.origin);
        if (url.origin !== window.location.origin) {
            return;
        }
        event.preventDefault();
        const params = new URLSearchParams(new FormData(form));
        const search = params.toString() ? '?' + params.toString() : '';
        navigateTo(normalizePath(url.pathname), { search, force: true });
    });

    captureNavDefaults();
    history.replaceState({ spaPath: currentPath }, document.title, window.location.pathname + window.location.search);
    updateSidebarActive(currentPath);

    if (mainEl.querySelector('#spa-loading')) {
        navigateTo(currentPath, { pushState: false, force: true });
    } else if (mainEl.innerHTML.trim()) {
        renderFragment(mainEl.innerHTML, currentPath, { pushState: false, search: window.location.search });
    }
})();
