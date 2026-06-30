/**
 * Child profile settings — tabs, sync status, and auto-save groups.
 */
(function () {
    'use strict';

    function i18n(key, params) {
        return typeof window.guardianI18n === 'function' ? window.guardianI18n(key, params) : key;
    }

    function getRoot() {
        return document.getElementById('admin-user-edit-root');
    }

    const TAB_TARGETS = {
        browsing: '#browsing-tab',
        computer: '#computer-tab',
        family: '#family-tab',
        apps: '#apps-tab',
        share: '#share-tab',
    };
    const TAB_HASH_PREFIX = 'profile-';
    let tabHashListener = null;
    let syncStatusInterval = null;

    function tabHashForId(tabId) {
        const entry = Object.entries(TAB_TARGETS).find(([, id]) => id === tabId);
        return entry ? `#${TAB_HASH_PREFIX}${entry[0]}` : `#${TAB_HASH_PREFIX}browsing`;
    }

    function tabIdFromHash(hash) {
        const slug = (hash || '').replace(/^#/, '');
        if (!slug.startsWith(TAB_HASH_PREFIX)) {
            return null;
        }
        return TAB_TARGETS[slug.slice(TAB_HASH_PREFIX.length)] || null;
    }

    function updateTabHash(tabId) {
        const nextHash = tabHashForId(tabId);
        const nextUrl = `${window.location.pathname}${window.location.search}${nextHash}`;
        history.replaceState(history.state, '', nextUrl);
    }

    function teardownProfilePage() {
        if (tabHashListener) {
            window.removeEventListener('hashchange', tabHashListener);
            tabHashListener = null;
        }
        if (syncStatusInterval) {
            window.clearInterval(syncStatusInterval);
            syncStatusInterval = null;
        }
    }

    function bindTabs() {
        const tabButtons = document.querySelectorAll('.admin-user-edit-tabs .segmented-tab-btn');
        const tabPanes = document.querySelectorAll('.admin-user-edit-panes .tab-pane-custom');
        if (!tabButtons.length || !tabPanes.length) return;

        if (tabHashListener) {
            window.removeEventListener('hashchange', tabHashListener);
            tabHashListener = null;
        }

        function switchTab(targetId) {
            const activePane = document.querySelector(targetId);
            if (!activePane) return;
            tabPanes.forEach((pane) => pane.classList.add('d-none'));
            activePane.classList.remove('d-none');
            tabButtons.forEach((btn) => {
                btn.classList.toggle('active', btn.getAttribute('data-tab-target') === targetId);
            });
            const activeBtn = Array.from(tabButtons).find(
                (btn) => btn.getAttribute('data-tab-target') === targetId,
            );
            if (activeBtn && activeBtn.closest('.admin-user-edit-tabs--mobile')) {
                activeBtn.scrollIntoView({ inline: 'nearest', block: 'nearest', behavior: 'smooth' });
            }
        }

        function activateTab(targetId, options) {
            const opts = options || {};
            switchTab(targetId);
            if (opts.updateHash !== false) {
                updateTabHash(targetId);
            }
        }

        tabButtons.forEach((btn) => {
            btn.addEventListener('click', (event) => {
                event.preventDefault();
                const targetId = btn.getAttribute('data-tab-target');
                if (targetId) {
                    activateTab(targetId);
                }
            });
        });

        tabHashListener = () => {
            const targetId = tabIdFromHash(window.location.hash) || TAB_TARGETS.browsing;
            activateTab(targetId, { updateHash: false });
        };
        window.addEventListener('hashchange', tabHashListener);

        const initialTab = tabIdFromHash(window.location.hash) || TAB_TARGETS.browsing;
        activateTab(initialTab, { updateHash: !window.location.hash });
        highlightMappingFromQuery();
    }

    function highlightMappingFromQuery() {
        const params = new URLSearchParams(window.location.search);
        const mappingId = params.get('highlight_mapping');
        if (!mappingId) return;
        const card = document.querySelector(
            `.device-policy-card[data-mapping-id="${CSS.escape(mappingId)}"]`,
        );
        if (!card) return;
        card.classList.add('border-primary', 'shadow-sm');
        card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function bindInPageTabLinks() {
        document.querySelectorAll('[data-profile-tab]').forEach((link) => {
            link.addEventListener('click', (event) => {
                const tabKey = link.getAttribute('data-profile-tab');
                const targetId = TAB_TARGETS[tabKey];
                if (!targetId) return;
                event.preventDefault();
                const tabButtons = document.querySelectorAll('.admin-user-edit-tabs .segmented-tab-btn');
                const tabPanes = document.querySelectorAll('.admin-user-edit-panes .tab-pane-custom');
                if (!tabPanes.length) return;
                tabPanes.forEach((pane) => pane.classList.add('d-none'));
                const activePane = document.querySelector(targetId);
                if (activePane) activePane.classList.remove('d-none');
                tabButtons.forEach((btn) => {
                    btn.classList.toggle('active', btn.getAttribute('data-tab-target') === targetId);
                });
                updateTabHash(targetId);
            });
        });
    }

    function setBlocklistSyncStatus(state, label) {
        const states = ['is-live', 'is-pending', 'is-unknown', 'is-muted'];
        document.querySelectorAll('.js-blocklist-sync-dot').forEach((dot) => {
            dot.classList.remove(...states);
            dot.classList.add(state);
            dot.title = label;
            dot.setAttribute('aria-label', label);
        });
        document.querySelectorAll('.js-blocklist-sync-text').forEach((el) => {
            el.textContent = label;
        });
    }

    function updateSyncStatus() {
        const root = getRoot();
        if (!root) return;
        const userId = root.dataset.userId;
        fetch(`/api/user/${userId}/blocklists/sync-status`)
            .then((response) => response.json())
            .then((blocklistData) => {
                const blocklistSyncedCount = document.getElementById('blocklist-synced-count');
                if (!blocklistData?.success) {
                    setBlocklistSyncStatus('is-unknown', i18n('profile_sync_unknown'));
                    return;
                }
                if (blocklistSyncedCount) {
                    blocklistSyncedCount.textContent = blocklistData.synced_mapping_count;
                }
                if ((blocklistData.assigned_source_count || 0) === 0) {
                    setBlocklistSyncStatus('is-muted', i18n('profile_sync_none'));
                } else if (blocklistData.needs_sync) {
                    if ((blocklistData.awaiting_uid_count || 0) > 0) {
                        setBlocklistSyncStatus('is-pending', i18n('profile_sync_awaiting_uid'));
                    } else {
                        setBlocklistSyncStatus('is-pending', i18n('profile_sync_pending'));
                    }
                } else {
                    setBlocklistSyncStatus('is-live', i18n('profile_sync_live'));
                }

                if (blocklistData.mappings && Array.isArray(blocklistData.mappings)) {
                    blocklistData.mappings.forEach((mapping) => {
                        const card = document.getElementById(`mapping-card-${mapping.mapping_id}`);
                        if (!card) return;

                        const statusBadge = card.querySelector('.mapping-status-badge');
                        if (statusBadge) {
                            let badgeClass = 'badge p-2 mapping-status-badge ';
                            let badgeText = '';
                            if (mapping.status === 'synced') {
                                badgeClass += 'bg-success';
                                badgeText = i18n('profile_sync_status_synced') || 'Up to date';
                            } else if (mapping.status === 'awaiting_uid') {
                                badgeClass += 'bg-warning text-dark';
                                badgeText = i18n('profile_sync_status_awaiting_uid') || 'Finishing setup';
                            } else if (mapping.status === 'not_configured') {
                                badgeClass += 'bg-secondary';
                                badgeText = i18n('profile_sync_status_not_configured') || 'Shields not applied yet';
                            } else {
                                badgeClass += 'bg-warning text-dark';
                                badgeText = i18n('profile_sync_status_pending') || 'Updating...';
                            }
                            statusBadge.className = badgeClass;
                            statusBadge.textContent = badgeText;
                        }

                        const remediationBox = card.querySelector('.mapping-remediation-box');
                        if (remediationBox) {
                            if (mapping.status === 'synced' || mapping.status === 'not_configured') {
                                remediationBox.classList.add('d-none');
                            } else {
                                remediationBox.classList.remove('d-none');
                            }
                        }

                        const errorDetails = card.querySelector('.mapping-error-details');
                        if (errorDetails) {
                            if (mapping.last_error) {
                                errorDetails.classList.remove('d-none');
                                const preEl = errorDetails.querySelector('pre');
                                if (preEl) {
                                    preEl.textContent = mapping.last_error;
                                }
                            } else {
                                errorDetails.classList.add('d-none');
                            }
                        }
                    });
                }
            })
            .catch((error) => {
                console.error('Error fetching sync status:', error);
            });
    }

    function updateLinuxDevicePolicyBadge(mappingId, isSynced) {
        const card = document.querySelector(`.device-policy-card[data-mapping-id="${mappingId}"]`);
        if (!card) return;
        const badge = card.querySelector('.device-policy-sync-badge');
        if (!badge) return;
        badge.className = `badge device-policy-sync-badge ${isSynced ? 'bg-success' : 'bg-warning text-dark'}`;
        badge.textContent = isSynced ? i18n('profile_device_synced') : i18n('profile_device_sync_pending');
    }

    function toggleChromeAllowedExtsVisibility(mappingId) {
        const checkbox = document.querySelector(`.chrome-block-ext[data-mapping-id="${mappingId}"]`);
        const container = document.getElementById(`chrome-allowed-exts-container-${mappingId}`);
        if (!checkbox || !container) return;
        container.classList.toggle('d-none', !checkbox.checked);
    }

    function collectDevicePolicyPayload(mappingId) {
        const q = (selector) => document.querySelector(`${selector}[data-mapping-id="${mappingId}"]`);
        const installSoftware = q('.linux-install-software');
        const uninstallSoftware = q('.linux-uninstall-software');
        const mountMedia = q('.linux-mount-media');
        const modifyAccounts = q('.linux-modify-accounts');
        const powerActions = q('.linux-power-actions');
        const pkexec = q('.linux-pkexec');
        const flatpak = q('.linux-flatpak');
        const snap = q('.linux-snap');
        const bluetooth = q('.linux-bluetooth');
        const terminalAccess = q('.linux-terminal-access');
        const supportMessage = q('.linux-support-message');
        const chromeIncognito = q('.chrome-incognito');
        const chromeSafesearch = q('.chrome-safesearch');
        const chromeYoutube = q('.chrome-youtube');
        const chromeBlockExt = q('.chrome-block-ext');
        const chromeBlockGenai = q('.chrome-block-genai');
        const chromeAllowedExts = q('.chrome-allowed-exts');

        return {
            install_software_disabled: installSoftware ? installSoftware.checked : false,
            uninstall_software_disabled: uninstallSoftware ? uninstallSoftware.checked : false,
            mount_removable_media_disabled: mountMedia ? mountMedia.checked : false,
            modify_accounts_disabled: modifyAccounts ? modifyAccounts.checked : false,
            system_power_actions_disabled: powerActions ? powerActions.checked : false,
            pkexec_elevation_disabled: pkexec ? pkexec.checked : false,
            flatpak_install_disabled: flatpak ? flatpak.checked : false,
            snap_install_disabled: snap ? snap.checked : false,
            bluetooth_disabled: bluetooth ? bluetooth.checked : false,
            terminal_access_disabled: terminalAccess ? terminalAccess.checked : false,
            support_message: supportMessage ? supportMessage.value.trim() : '',
            chrome_policies: {
                incognito_disabled: chromeIncognito ? chromeIncognito.checked : true,
                safesearch_enforced: chromeSafesearch ? chromeSafesearch.checked : true,
                youtube_restrict: chromeYoutube ? parseInt(chromeYoutube.value, 10) : 2,
                block_other_extensions: chromeBlockExt ? chromeBlockExt.checked : false,
                block_genai_features: chromeBlockGenai ? chromeBlockGenai.checked : false,
                allowed_extension_ids: chromeAllowedExts ? chromeAllowedExts.value : '',
            },
        };
    }

    function saveDevicePolicy(mappingId) {
        return fetch(`/api/mappings/${mappingId}/linux-device-policy`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify(collectDevicePolicyPayload(mappingId)),
        })
            .then((response) => response.json())
            .then((payload) => {
                if (payload.success) {
                    updateLinuxDevicePolicyBadge(mappingId, !!payload.policy?.is_synced);
                    return {
                        success: true,
                        message: payload.message,
                        sync_pending: !payload.policy?.is_synced,
                    };
                }
                return payload;
            });
    }

    function saveApprovalSettings(mappingId) {
        const appMode = document.querySelector(`.approval-app-mode[data-mapping-id="${mappingId}"]`);
        const domainMode = document.querySelector(`.approval-domain-mode[data-mapping-id="${mappingId}"]`);
        const registrationMode = document.querySelector(`.approval-registration[data-mapping-id="${mappingId}"]`);
        const aiMode = document.querySelector(`.approval-ai-mode[data-mapping-id="${mappingId}"]`);
        const aiLogging = document.querySelector(`.approval-ai-logging[data-mapping-id="${mappingId}"]`);
        const aiLimit = document.querySelector(`.approval-ai-limit[data-mapping-id="${mappingId}"]`);
        return fetch(`/api/mappings/${mappingId}/approval-settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({
                app_launch_mode: appMode ? appMode.value : 'open',
                domain_access_mode: domainMode ? domainMode.value : 'blocklist_only',
                registration_approval_enabled: registrationMode ? registrationMode.checked : false,
                ai_policy_mode: aiMode ? aiMode.value : 'off',
                ai_prompt_logging: aiLogging ? aiLogging.value : 'metadata_only',
                ai_daily_time_limit: aiLimit && aiLimit.value !== '' ? parseInt(aiLimit.value, 10) : null,
            }),
        }).then((response) => response.json());
    }

    function saveOverlaySettings(userId) {
        const ageTier = document.getElementById('overlay-age-tier');
        const parentNote = document.getElementById('overlay-parent-note');
        return fetch(`/managed-users/${userId}/overlay`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({
                overlay_age_tier: ageTier && ageTier.value ? ageTier.value : null,
                overlay_parent_note: parentNote ? parentNote.value.trim() || null : null,
            }),
        }).then((response) => response.json());
    }

    function bindAutosave(root) {
        if (!window.GuardianAutosave) return;
        const userId = root.dataset.userId;

        const curatedForm = document.getElementById('curated-shields-form');
        if (curatedForm) {
            GuardianAutosave.configure('curated', {
                debounceMs: 400,
                statusEl: document.getElementById('curated-autosave-status'),
            });
            curatedForm.querySelectorAll('input[type="checkbox"]').forEach((input) => {
                input.addEventListener('change', () => {
                    GuardianAutosave.schedule('curated', () => GuardianAutosave.postForm(curatedForm), {
                        onSuccess: () => updateSyncStatus(),
                    });
                });
            });
        }

        const customForm = document.getElementById('custom-shields-form');
        if (customForm) {
            GuardianAutosave.configure('custom-blocklists', {
                debounceMs: 400,
                statusEl: document.getElementById('custom-autosave-status'),
            });
            customForm.querySelectorAll('input[type="checkbox"]').forEach((input) => {
                input.addEventListener('change', () => {
                    GuardianAutosave.schedule('custom-blocklists', () => GuardianAutosave.postForm(customForm), {
                        onSuccess: () => updateSyncStatus(),
                    });
                });
            });
        }

        document.querySelectorAll('.device-policy-card').forEach((card) => {
            const mappingId = card.dataset.mappingId;
            const groupId = `device-policy-${mappingId}`;
            const statusEl = card.querySelector('.guardian-autosave-status');
            GuardianAutosave.configure(groupId, {
                debounceMs: 500,
                statusEl,
            });

            const scheduleDeviceSave = () => {
                GuardianAutosave.schedule(groupId, () => saveDevicePolicy(mappingId));
            };

            card.querySelectorAll('input, select').forEach((input) => {
                const eventName = input.type === 'text' ? 'input' : 'change';
                input.addEventListener(eventName, () => {
                    if (input.classList.contains('chrome-block-ext')) {
                        toggleChromeAllowedExtsVisibility(mappingId);
                    }
                    GuardianAutosave.configure(groupId, { debounceMs: input.type === 'text' ? 800 : 500 });
                    scheduleDeviceSave();
                });
            });
        });

        document.querySelectorAll('.approval-settings-card').forEach((card) => {
            const mappingId = card.dataset.mappingId;
            const groupId = `approval-${mappingId}`;
            const statusEl = card.querySelector('.guardian-autosave-status');
            GuardianAutosave.configure(groupId, { statusEl, debounceMs: 600 });

            const aiModeSelect = card.querySelector('.approval-ai-mode');
            const aiLoggingSec = card.querySelector(`#ai-logging-section-${mappingId}`);
            const aiLimitSec = card.querySelector(`#ai-limit-section-${mappingId}`);

            const toggleAiSections = () => {
                const mode = aiModeSelect ? aiModeSelect.value : 'off';
                if (mode === 'off') {
                    if (aiLoggingSec) aiLoggingSec.classList.add('d-none');
                    if (aiLimitSec) aiLimitSec.classList.remove('d-none');
                } else if (mode === 'block') {
                    if (aiLoggingSec) aiLoggingSec.classList.add('d-none');
                    if (aiLimitSec) aiLimitSec.classList.add('d-none');
                } else { // monitor or approve
                    if (aiLoggingSec) aiLoggingSec.classList.remove('d-none');
                    if (aiLimitSec) aiLimitSec.classList.remove('d-none');
                }
            };

            if (aiModeSelect) {
                aiModeSelect.addEventListener('change', toggleAiSections);
                toggleAiSections();
            }

            card.querySelectorAll('select, input').forEach((input) => {
                const eventName = input.type === 'number' ? 'input' : 'change';
                input.addEventListener(eventName, () => {
                    GuardianAutosave.schedule(groupId, () => saveApprovalSettings(mappingId));
                });
            });
        });

        const overlayCard = document.getElementById('guardian-overlay-card');
        if (overlayCard) {
            GuardianAutosave.configure('overlay', {
                debounceMs: 800,
                statusEl: document.getElementById('overlay-autosave-status'),
            });
            overlayCard.querySelectorAll('select, textarea').forEach((input) => {
                const eventName = input.tagName === 'TEXTAREA' ? 'input' : 'change';
                input.addEventListener(eventName, () => {
                    GuardianAutosave.schedule('overlay', () => saveOverlaySettings(userId));
                });
            });
        }

        const appPoliciesForm = document.getElementById('app-policies-form');
        if (appPoliciesForm) {
            GuardianAutosave.configure('app-policies', {
                debounceMs: 400,
                statusEl: document.getElementById('app-policies-autosave-status'),
            });
            appPoliciesForm.querySelectorAll('input[type="checkbox"]').forEach((input) => {
                input.addEventListener('change', () => {
                    GuardianAutosave.schedule('app-policies', () => GuardianAutosave.postForm(appPoliciesForm));
                });
            });
        }
    }

    function bindAppActions() {
        document.querySelectorAll('[data-revoke-grant]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const grantId = btn.dataset.revokeGrant;
                if (!grantId || !window.confirm(i18n('profile_revoke_confirm') || 'Revoke approval for this app?')) return;
                fetch(`/api/approval-grants/${grantId}/revoke`, { method: 'POST', headers: { Accept: 'application/json' } })
                    .then((response) => response.json())
                    .then((data) => {
                        if (!data.success) {
                            window.showNotification(data.message || i18n('profile_revoke_failed'), 'error');
                            return;
                        }
                        window.showNotification(i18n('profile_app_revoked'));
                        window.location.reload();
                    })
                    .catch(() => window.showNotification(i18n('profile_revoke_failed'), 'error'));
            });
        });

        document.querySelectorAll('[data-preapprove-app]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const mappingId = btn.dataset.mappingId;
                const identifier = btn.dataset.identifier;
                const displayLabel = btn.dataset.displayLabel;
                const platform = btn.dataset.platform;
                const targetKind = (platform === 'linux' || identifier.startsWith('/')) ? 'executable' : 'package';
                fetch(`/api/mappings/${mappingId}/approval-grants`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                    body: JSON.stringify({
                        grant_type: 'app_launch',
                        target_kind: targetKind,
                        target_value: identifier,
                        display_label: displayLabel,
                    }),
                })
                    .then((response) => response.json())
                    .then((data) => {
                        if (!data.success) {
                            window.showNotification(data.message || i18n('profile_approve_failed'), 'error');
                            return;
                        }
                        window.showNotification(i18n('profile_app_approved'));
                        window.location.reload();
                    })
                    .catch(() => window.showNotification(i18n('profile_approve_failed'), 'error'));
            });
        });
    }

    function bindPresetModal() {
        const form = document.getElementById('apply-policy-preset-form');
        if (!form) return;
        form.addEventListener('submit', (event) => {
            const title = form.dataset.confirmTitle || 'Apply this safety baseline?';
            const body = form.dataset.confirmBody || '';
            const msg = body ? `${title}\n\n${body}` : title;
            if (!window.confirm(msg)) {
                event.preventDefault();
            }
        });
    }

    function init() {
        const root = getRoot();
        if (!root || root.dataset.bound === '1') return;
        root.dataset.bound = '1';

        document.querySelectorAll('.device-policy-card').forEach((card) => {
            toggleChromeAllowedExtsVisibility(card.dataset.mappingId);
        });
        bindTabs();
        bindInPageTabLinks();
        bindAutosave(root);
        bindAppActions();
        bindPresetModal();
        updateSyncStatus();
        if (!syncStatusInterval) {
            syncStatusInterval = window.setInterval(updateSyncStatus, 15000);
        }

        if (window.GuardianWizard) {
            GuardianWizard.bindWizard({
                matrixDataId: 'policy-preset-matrix-data',
                presetsDataId: 'marketplace-presets-data',
                ageSelector: '.edit-policy-age-radio',
                maturityInputId: 'edit-policy-maturity-level',
                sliderId: 'edit-responsibility-slider',
                sliderLabelId: 'edit-slider-active-label',
                summaryTitleId: 'edit-preset-summary-title',
                summaryListId: 'edit-preset-summary-details',
                narrativeId: 'edit-responsibility-narrative',
            });
        }
    }

    function boot() {
        if (!getRoot()) return;
        init();
    }

    if (window.GuardianSPA && typeof window.GuardianSPA.onRoute === 'function') {
        window.GuardianSPA.onRoute(() => {
            teardownProfilePage();
            const root = document.getElementById('admin-user-edit-root');
            if (root) {
                delete root.dataset.bound;
            }
        });
    }

    document.addEventListener('guardian:page-ready', boot);
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
    window.applyPresetRole = function (role) {
        const viewCheck = document.getElementById('share-can-view-screentime');
        const manageCheck = document.getElementById('share-can-manage-screentime');
        const monitorCheck = document.getElementById('share-can-view-monitoring');
        const policyCheck = document.getElementById('share-can-manage-policies');

        if (viewCheck) viewCheck.checked = true; // always enabled
        if (role === 'viewer') {
            if (manageCheck) manageCheck.checked = false;
            if (monitorCheck) monitorCheck.checked = false;
            if (policyCheck) policyCheck.checked = false;
        } else if (role === 'manager') {
            if (manageCheck) manageCheck.checked = true;
            if (monitorCheck) monitorCheck.checked = false;
            if (policyCheck) policyCheck.checked = false;
        } else if (role === 'co-parent') {
            if (manageCheck) manageCheck.checked = true;
            if (monitorCheck) monitorCheck.checked = true;
            if (policyCheck) policyCheck.checked = true;
        }
    };

    window.generateShareInvite = function (childId) {
        const payload = {
            can_view_screentime: document.getElementById('share-can-view-screentime').checked,
            can_manage_screentime: document.getElementById('share-can-manage-screentime').checked,
            can_view_monitoring: document.getElementById('share-can-view-monitoring').checked,
            can_manage_policies: document.getElementById('share-can-manage-policies').checked
        };

        fetch(`/api/profiles/${childId}/generate-invite`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                const fullUrl = window.location.origin + data.redeem_url;
                document.getElementById('share-invite-link').value = fullUrl;
                document.getElementById('share-result-container').classList.remove('d-none');
            } else {
                alert(data.message || i18n('profile_invite_generate_failed'));
            }
        })
        .catch(() => alert(i18n('profile_invite_generate_failed')));
    };

    window.copyInviteLinkToClipboard = function () {
        const linkInput = document.getElementById('share-invite-link');
        if (!linkInput) return;
        linkInput.select();
        linkInput.setSelectionRange(0, 99999);
        navigator.clipboard.writeText(linkInput.value)
            .then(() => {
                alert(i18n('profile_invite_copied'));
            })
            .catch(() => {
                alert(i18n('profile_invite_copy_failed'));
            });
    };
})();
