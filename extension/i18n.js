// Shared chrome.i18n helper for extension pages and the service worker.
'use strict';

/**
 * Resolve a localized string from the bundled _locales catalog.
 * @param {string} key Chrome message name (camelCase).
 * @param {string|string[]|undefined} substitutions Optional placeholder values.
 * @returns {string}
 */
function guardianExtI18n(key, substitutions) {
    if (typeof chrome !== 'undefined' && chrome.i18n && chrome.i18n.getMessage) {
        const message = chrome.i18n.getMessage(key, substitutions);
        if (message) {
            return message;
        }
    }
    return key;
}

// Service workers and classic scripts share the same global scope pattern.
if (typeof globalThis !== 'undefined') {
    globalThis.guardianExtI18n = guardianExtI18n;
}
