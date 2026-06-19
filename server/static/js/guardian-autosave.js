/**
 * Shared debounced auto-save helper for Guardian web UI.
 */
(function () {
    'use strict';

    function i18n(key, params) {
        return typeof window.guardianI18n === 'function' ? window.guardianI18n(key, params) : key;
    }

    function notify(message, type) {
        if (typeof window.showNotification === 'function') {
            window.showNotification(message, type);
        }
    }

    function setStatusEl(statusEl, state, message) {
        if (!statusEl) return;
        statusEl.textContent = message || '';
        statusEl.classList.remove('is-saving', 'is-saved', 'is-error');
        if (state) statusEl.classList.add(state);
    }

    const groups = new Map();

    function getGroup(groupId) {
        if (!groups.has(groupId)) {
            groups.set(groupId, {
                timer: null,
                inFlight: false,
                queued: null,
                debounceMs: 500,
                statusEl: null,
                toastOnSuccess: true,
            });
        }
        return groups.get(groupId);
    }

    function runSave(groupId) {
        const group = getGroup(groupId);
        if (!group.queued || group.inFlight) return;

        const job = group.queued;
        group.queued = null;
        group.inFlight = true;
        setStatusEl(group.statusEl, 'is-saving', i18n('save_saving'));

        Promise.resolve()
            .then(() => job.saveFn())
            .then((result) => {
                group.inFlight = false;
                if (!result || result.success === false) {
                    const message = (result && result.message) || i18n('save_failed');
                    setStatusEl(group.statusEl, 'is-error', message);
                    notify(message, 'error');
                    if (group.queued) runSave(groupId);
                    return;
                }

                const syncPending = !!result.sync_pending;
                const message = syncPending ? i18n('save_sync_pending') : i18n('save_saved');
                setStatusEl(group.statusEl, 'is-saved', message);
                if (group.toastOnSuccess) {
                    notify(result.message || message, syncPending ? 'warning' : 'success');
                }
                if (typeof job.onSuccess === 'function') {
                    job.onSuccess(result);
                }
                if (group.statusEl) {
                    window.setTimeout(() => {
                        if (group.statusEl && !group.inFlight && !group.queued) {
                            setStatusEl(group.statusEl, null, '');
                        }
                    }, 2500);
                }
                if (group.queued) runSave(groupId);
            })
            .catch(() => {
                group.inFlight = false;
                const message = i18n('save_failed');
                setStatusEl(group.statusEl, 'is-error', message);
                notify(message, 'error');
                if (group.queued) runSave(groupId);
            });
    }

    window.GuardianAutosave = {
        configure(groupId, options) {
            const group = getGroup(groupId);
            if (options.debounceMs != null) group.debounceMs = options.debounceMs;
            if (options.statusEl != null) group.statusEl = options.statusEl;
            if (options.toastOnSuccess != null) group.toastOnSuccess = options.toastOnSuccess;
        },

        schedule(groupId, saveFn, options) {
            const group = getGroup(groupId);
            if (options) {
                if (options.debounceMs != null) group.debounceMs = options.debounceMs;
                if (options.statusEl != null) group.statusEl = options.statusEl;
                if (options.onSuccess) {
                    group.queued = { saveFn, onSuccess: options.onSuccess };
                } else {
                    group.queued = { saveFn };
                }
            } else {
                group.queued = { saveFn };
            }

            if (group.timer) window.clearTimeout(group.timer);
            group.timer = window.setTimeout(() => {
                group.timer = null;
                runSave(groupId);
            }, group.debounceMs);
        },

        postForm(form, options) {
            const formEl = typeof form === 'string' ? document.querySelector(form) : form;
            if (!formEl) {
                return Promise.resolve({ success: false, message: i18n('save_failed') });
            }
            const body = new FormData(formEl);
            return fetch(formEl.action, {
                method: (formEl.method || 'POST').toUpperCase(),
                headers: {
                    Accept: 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body,
            }).then((response) => response.json().catch(() => ({ success: false, message: i18n('save_failed') })));
        },
    };
})();
