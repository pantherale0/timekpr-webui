/**
 * Client-side translation helper for strings from #i18n-data (js.* subtree).
 */
(function () {
    'use strict';

    function getCatalog() {
        if (window.__guardianI18nCatalog) {
            return window.__guardianI18nCatalog;
        }
        const el = document.getElementById('i18n-data');
        window.__guardianI18nCatalog = el
            ? JSON.parse(el.textContent || '{}')
            : {};
        return window.__guardianI18nCatalog;
    }

    function format(text, params) {
        if (!params) {
            return text;
        }
        return Object.entries(params).reduce((result, [key, value]) => {
            return result.replace(new RegExp(`\\{${key}\\}`, 'g'), String(value));
        }, text);
    }

    window.guardianI18n = function (key, params) {
        const catalog = getCatalog();
        const text = Object.prototype.hasOwnProperty.call(catalog, key)
            ? catalog[key]
            : key;
        return format(text, params);
    };
})();
