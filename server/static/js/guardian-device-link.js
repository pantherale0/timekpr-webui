/**
 * Shared child-to-device linking UI for admin_users and onboarding wizard.
 */
(function () {
    function i18n(key, params) {
        return typeof window.guardianI18n === 'function' ? window.guardianI18n(key, params) : key;
    }

    function parseUsers(raw) {
        if (!raw) return [];
        try {
            const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
            return Array.isArray(parsed) ? parsed : [];
        } catch (_err) {
            return [];
        }
    }

    function selectedDeviceOption(panel) {
        const select = panel.querySelector('.device-link-device-select');
        if (!select || select.selectedIndex < 0) return null;
        return select.options[select.selectedIndex];
    }

    function currentPlatform(panel) {
        const option = selectedDeviceOption(panel);
        if (option) return option.getAttribute('data-platform') || 'linux';
        return panel.dataset.platform || 'linux';
    }

    function currentUsers(panel) {
        if (panel._deviceUsers) return panel._deviceUsers;
        const option = selectedDeviceOption(panel);
        if (!option) return [];
        return parseUsers(option.getAttribute('data-linux-users'));
    }

    function setAccountMode(panel, platform) {
        const accountLabel = panel.querySelector('.device-link-account-label');
        const accountSelect = panel.querySelector('.device-link-account-select');
        const playerSelect = panel.querySelector('.device-link-player-select');
        const isConsole = platform === 'nintendo' || platform === 'xbox';

        if (accountLabel) {
            if (platform === 'nintendo') {
                accountLabel.textContent = i18n('device_link_player_nintendo');
            } else if (platform === 'xbox') {
                accountLabel.textContent = i18n('device_link_player_xbox');
            } else if (platform === 'android') {
                accountLabel.textContent = i18n('device_link_android_profile');
            } else {
                accountLabel.textContent = i18n('device_link_who_signs_in');
            }
        }

        if (accountSelect) {
            accountSelect.classList.toggle('d-none', isConsole);
            accountSelect.disabled = isConsole;
            accountSelect.required = !isConsole;
        }
        if (playerSelect) {
            playerSelect.classList.toggle('d-none', !isConsole);
            playerSelect.disabled = !isConsole;
            playerSelect.required = isConsole;
        }
    }

    function populateAccountSelect(panel, users, platform) {
        const accountSelect = panel.querySelector('.device-link-account-select');
        const playerSelect = panel.querySelector('.device-link-player-select');
        const isConsole = platform === 'nintendo' || platform === 'xbox';
        const target = isConsole ? playerSelect : accountSelect;
        if (!target) return;

        target.innerHTML = '';
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.disabled = true;
        placeholder.selected = true;
        placeholder.textContent = isConsole
            ? i18n('device_link_select_player')
            : i18n('device_link_select_account');
        target.appendChild(placeholder);

        if (!users.length) {
            const empty = document.createElement('option');
            empty.value = '';
            empty.textContent = i18n('device_link_no_accounts');
            target.appendChild(empty);
            return;
        }

        users.forEach((user) => {
            const option = document.createElement('option');
            if (isConsole) {
                option.value = user.username || user.player_id || '';
                option.textContent = (user.nickname || '').trim() || option.value;
            } else {
                option.value = user.username || '';
                option.textContent = user.username || '';
                if (user.uid != null) option.dataset.uid = String(user.uid);
            }
            target.appendChild(option);
        });
    }

    function syncUidFromSelection(panel) {
        const platform = currentPlatform(panel);
        const uidInput = panel.querySelector('.device-link-uid-input');
        const provisionSelect = panel.querySelector('.device-link-provision-select');
        if (!uidInput) return;

        if (platform === 'nintendo' || platform === 'xbox') {
            uidInput.value = '';
            return;
        }
        if (provisionSelect && provisionSelect.value) {
            uidInput.value = '';
            return;
        }

        const accountSelect = panel.querySelector('.device-link-account-select');
        if (!accountSelect || accountSelect.selectedIndex < 0) return;
        const opt = accountSelect.options[accountSelect.selectedIndex];
        uidInput.value = opt && opt.dataset.uid ? opt.dataset.uid : '';
    }

    function updateProvisionVisibility(panel) {
        const platform = currentPlatform(panel);
        const option = selectedDeviceOption(panel);
        const isDeviceOwner = option && option.getAttribute('data-is-device-owner') === 'true';
        const provisionRow = panel.querySelector('.device-link-provision-row');
        const provisionSelect = panel.querySelector('.device-link-provision-select');
        const show = platform === 'android' && isDeviceOwner;
        if (provisionRow) provisionRow.classList.toggle('d-none', !show);
        if (!show && provisionSelect) provisionSelect.value = '';
    }

    function updateDeviceSubtitle(panel) {
        const subtitle = panel.querySelector('.device-link-device-subtitle');
        const option = selectedDeviceOption(panel);
        if (!subtitle) return;
        const text = option ? (option.getAttribute('data-subtitle') || '').trim() : '';
        subtitle.textContent = text;
        subtitle.classList.toggle('d-none', !text);
    }

    function applyDeviceContext(panel, device) {
        if (!device) return;
        panel._deviceUsers = parseUsers(device.linux_users || device.players);
        panel.dataset.platform = device.platform || 'linux';
        const hidden = panel.querySelector('.device-link-system-id');
        if (hidden) hidden.value = device.system_id || '';
        const deviceSelect = panel.querySelector('.device-link-device-select');
        if (deviceSelect && device.system_id) {
            deviceSelect.value = device.system_id;
        }
        setAccountMode(panel, panel.dataset.platform);
        populateAccountSelect(panel, panel._deviceUsers, panel.dataset.platform);
        updateProvisionVisibility(panel);
        updateDeviceSubtitle(panel);
        syncUidFromSelection(panel);
    }

    function onDeviceChange(panel) {
        const option = selectedDeviceOption(panel);
        panel._deviceUsers = option ? parseUsers(option.getAttribute('data-linux-users')) : [];
        const platform = currentPlatform(panel);
        setAccountMode(panel, platform);
        populateAccountSelect(panel, panel._deviceUsers, platform);
        updateProvisionVisibility(panel);
        updateDeviceSubtitle(panel);
        const hidden = panel.querySelector('.device-link-system-id');
        if (hidden && option) hidden.value = option.value;
        syncUidFromSelection(panel);
    }

    function resolveLinuxUsername(panel) {
        const platform = currentPlatform(panel);
        const manual = (panel.querySelector('.device-link-manual-account')?.value || '').trim();
        if (manual) return manual;

        if (platform === 'nintendo' || platform === 'xbox') {
            const playerSelect = panel.querySelector('.device-link-player-select');
            return playerSelect ? playerSelect.value : '';
        }
        const accountSelect = panel.querySelector('.device-link-account-select');
        if (accountSelect && accountSelect.value) return accountSelect.value;
        if (platform === 'android') return 'android-agent';
        return '';
    }

    function getPayload(panel) {
        const uidRaw = (panel.querySelector('.device-link-uid-input')?.value || '').trim();
        const provisionSelect = panel.querySelector('.device-link-provision-select');
        const hidden = panel.querySelector('.device-link-system-id');
        const deviceSelect = panel.querySelector('.device-link-device-select');
        const systemId = (hidden?.value || deviceSelect?.value || '').trim();
        const payload = {
            userId: panel.dataset.userId || null,
            systemId,
            linuxUsername: resolveLinuxUsername(panel),
            linuxUid: uidRaw || null,
            androidProfileType: provisionSelect?.value || null,
        };
        if (payload.androidProfileType !== 'restricted' && payload.androidProfileType !== 'standard') {
            payload.androidProfileType = null;
        }
        return payload;
    }

    function showError(panel, message) {
        const el = panel.querySelector('.device-link-error');
        if (!el) return;
        if (message) {
            el.textContent = message;
            el.classList.remove('d-none');
        } else {
            el.textContent = '';
            el.classList.add('d-none');
        }
    }

    function setBusy(panel, busy) {
        const btn = panel.querySelector('.device-link-connect-btn');
        if (btn) btn.disabled = busy;
    }

    async function submitDeviceLink(payload) {
        const response = await fetch(`/api/managed-users/${payload.userId}/mappings/connect`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                system_id: payload.systemId,
                linux_username: payload.linuxUsername,
                linux_uid: payload.linuxUid,
                android_profile_type: payload.androidProfileType,
            }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.message || i18n('device_link_connect_failed'));
        }
        return data;
    }

    function initDeviceLinkPanel(rootEl, options = {}) {
        if (!rootEl) return;
        const panel = rootEl.classList.contains('guardian-device-link-panel')
            ? rootEl
            : rootEl.querySelector('.guardian-device-link-panel');
        if (!panel) return;

        const deviceSelect = panel.querySelector('.device-link-device-select');
        if (deviceSelect) {
            deviceSelect.addEventListener('change', () => onDeviceChange(panel));
        }

        const accountSelect = panel.querySelector('.device-link-account-select');
        if (accountSelect) {
            accountSelect.addEventListener('change', () => syncUidFromSelection(panel));
        }

        const provisionSelect = panel.querySelector('.device-link-provision-select');
        if (provisionSelect) {
            provisionSelect.addEventListener('change', () => syncUidFromSelection(panel));
        }

        const connectBtn = panel.querySelector('.device-link-connect-btn');
        if (connectBtn) {
            connectBtn.addEventListener('click', async () => {
                showError(panel, '');
                const payload = getPayload(panel);
                if (!payload.systemId) {
                    showError(panel, i18n('device_link_device_required'));
                    return;
                }
                if (!payload.linuxUsername) {
                    showError(panel, i18n('device_link_account_required'));
                    return;
                }
                if (!payload.userId) {
                    if (typeof options.onConnect === 'function') {
                        options.onConnect(payload);
                    }
                    return;
                }
                setBusy(panel, true);
                try {
                    const result = await submitDeviceLink(payload);
                    if (typeof options.onSuccess === 'function') {
                        options.onSuccess(result);
                    } else {
                        window.location.reload();
                    }
                } catch (err) {
                    const message = err.message || i18n('device_link_connect_failed');
                    showError(panel, message);
                    if (typeof options.onError === 'function') options.onError(err);
                } finally {
                    setBusy(panel, false);
                }
            });
        }

        if (options.device) {
            applyDeviceContext(panel, options.device);
        } else if (deviceSelect && deviceSelect.value) {
            onDeviceChange(panel);
        } else {
            setAccountMode(panel, panel.dataset.platform || 'linux');
        }

        panel._guardianDeviceLinkOptions = options;
    }

    window.GuardianDeviceLink = {
        initDeviceLinkPanel,
        submitDeviceLink,
        getPayload,
        applyDeviceContext,
    };
})();
