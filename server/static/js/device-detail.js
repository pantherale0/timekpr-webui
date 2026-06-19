/**
 * Device detail page interactions.
 */
(function () {
    'use strict';

    const TAB_ALIASES = {
        overview: '#overview-tab',
        activity: '#activity-tab',
        settings: '#settings-tab',
        policies: '#settings-tab',
        advanced: '#advanced-tab',
        diagnostics: '#advanced-tab',
    };

    function getRoot() {
        return document.getElementById('device-detail-root');
    }

    function getConfig() {
        const root = getRoot();
        if (!root) return null;
        return {
            systemId: root.dataset.systemId,
            deviceLabel: JSON.parse(root.dataset.deviceLabel || '""'),
            adminDevicesUrl: root.dataset.adminDevicesUrl || '/admin/devices',
            screenshotSettings: root.dataset.screenshotSettings === 'true',
        };
    }

    let deviceDetailAbort = null;

    /** Soft-refresh via SPA so nav chrome and mobile tab rail stay in sync (avoids full reload). */
    function refreshDeviceDetailPreserveTab() {
        const hash = window.location.hash || '';
        if (window.GuardianSPA && typeof window.GuardianSPA.navigate === 'function') {
            const path = typeof window.GuardianSPA.getCurrentPath === 'function'
                ? window.GuardianSPA.getCurrentPath()
                : window.location.pathname;
            window.GuardianSPA.navigate(path, { force: true });
            return;
        }
        window.location.href = window.location.pathname + window.location.search + hash;
    }

    function bindTabs(signal) {
        const tabButtons = document.querySelectorAll('.device-detail-tabs .segmented-tab-btn');
        const tabPanes = document.querySelectorAll('.device-detail-main .tab-pane-custom');
        if (!tabButtons.length || !tabPanes.length) return;

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
            if (activeBtn && activeBtn.closest('.device-detail-tabs--mobile')) {
                activeBtn.scrollIntoView({ inline: 'nearest', block: 'nearest', behavior: 'smooth' });
            }
        }

        function hashToTabId(hash) {
            const slug = (hash || '').replace(/^#/, '');
            return TAB_ALIASES[slug] || null;
        }

        tabButtons.forEach((btn) => {
            btn.addEventListener('click', () => {
                const targetId = btn.getAttribute('data-tab-target');
                const slug = Object.entries(TAB_ALIASES).find(([, id]) => id === targetId)?.[0] || 'overview';
                window.location.hash = slug;
            }, { signal });
        });

        const tabHashListener = () => {
            const mapped = hashToTabId(window.location.hash);
            switchTab(mapped || '#overview-tab');
        };

        window.addEventListener('hashchange', tabHashListener, { signal });
        tabHashListener();
    }

    function teardownDeviceDetailPage() {
        if (deviceDetailAbort) {
            deviceDetailAbort.abort();
            deviceDetailAbort = null;
        }
    }

    function init() {
        const config = getConfig();
        if (!config) return;
        const systemId = config.systemId;
        const deviceLabel = config.deviceLabel;

        if (deviceDetailAbort) {
            deviceDetailAbort.abort();
        }
        deviceDetailAbort = new AbortController();
        const signal = deviceDetailAbort.signal;

        bindTabs(signal);

const nintendoSyncBtn = document.getElementById('nintendo-sync-btn');
        const nintendoSyncError = document.getElementById('nintendo-sync-error');
        if (nintendoSyncBtn) {
            const defaultSyncLabel = nintendoSyncBtn.innerHTML;
            nintendoSyncBtn.addEventListener('click', async () => {
                nintendoSyncBtn.disabled = true;
                if (nintendoSyncError) {
                    nintendoSyncError.classList.add('d-none');
                    nintendoSyncError.textContent = '';
                }
                nintendoSyncBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span> Syncing…';
                try {
                    const response = await fetch('/api/nintendo/sync', { method: 'POST' });
                    const data = await response.json();
                    if (!response.ok || !data.success) {
                        throw new Error(data.message || 'Sync failed');
                    }
                    refreshDeviceDetailPreserveTab();
                } catch (err) {
                    if (nintendoSyncError) {
                        nintendoSyncError.textContent = 'Nintendo sync failed: ' + err.message;
                        nintendoSyncError.classList.remove('d-none');
                    }
                    nintendoSyncBtn.disabled = false;
                    nintendoSyncBtn.innerHTML = defaultSyncLabel;
                }
            });
        }

        const xboxSyncBtn = document.getElementById('xbox-sync-btn');
        const xboxSyncError = document.getElementById('xbox-sync-error');
        if (xboxSyncBtn) {
            const defaultSyncLabel = xboxSyncBtn.innerHTML;
            xboxSyncBtn.addEventListener('click', async () => {
                xboxSyncBtn.disabled = true;
                if (xboxSyncError) {
                    xboxSyncError.classList.add('d-none');
                    xboxSyncError.textContent = '';
                }
                xboxSyncBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span> Syncing…';
                try {
                    const response = await fetch('/api/xbox/sync', { method: 'POST' });
                    const data = await response.json();
                    if (!response.ok || !data.success) {
                        throw new Error(data.message || 'Sync failed');
                    }
                    refreshDeviceDetailPreserveTab();
                } catch (err) {
                    if (xboxSyncError) {
                        xboxSyncError.textContent = 'Xbox sync failed: ' + err.message;
                        xboxSyncError.classList.remove('d-none');
                    }
                    xboxSyncBtn.disabled = false;
                    xboxSyncBtn.innerHTML = defaultSyncLabel;
                }
            });
        }

        const unenrollBtn = document.getElementById('unenroll-device-btn');
        const factoryResetBtn = document.getElementById('factory-reset-device-btn');
        const lifecycleResult = document.getElementById('lifecycle-result');

        function showLifecycleResult(message, isSuccess) {
            if (!lifecycleResult) return;
            lifecycleResult.textContent = message;
            lifecycleResult.className = `alert mt-3 mb-0 alert-${isSuccess ? 'success' : 'danger'}`;
            lifecycleResult.classList.remove('d-none');
        }

        async function postUnenroll(mode) {
            const response = await fetch(`/api/device/${encodeURIComponent(systemId)}/unenroll`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode }),
            });
            const data = await response.json();
            if (!response.ok || !data.success) {
                throw new Error(data.message || 'Request failed');
            }
            return data;
        }

        if (unenrollBtn) {
            unenrollBtn.addEventListener('click', async () => {
                const confirmed = confirm(
                    'Remove this device from family management? The child device will stop receiving policies; you can re-pair later.'
                );
                if (!confirmed) return;

                unenrollBtn.disabled = true;
                try {
                    const data = await postUnenroll('unenroll');
                    const details = [];
                    if (data.delivered_to_agent) details.push('agent cleanup delivered');
                    if (data.server_revoked) details.push('server trust revoked');
                    showLifecycleResult(
                        `${data.message || 'Device unenrolled successfully'}${details.length ? ' (' + details.join(', ') + ')' : ''}`,
                        true
                    );
                    setTimeout(() => { window.location.href = config.adminDevicesUrl; }, 1200);
                } catch (error) {
                    showLifecycleResult(error.message || 'Failed to unenroll device', false);
                    unenrollBtn.disabled = false;
                }
            });
        }

        if (factoryResetBtn) {
            factoryResetBtn.addEventListener('click', async () => {
                const expected = factoryResetBtn.dataset.deviceLabel || 'RESET';
                const typed = prompt(
                    `Erase this Android device and remove Guardian management. All personal data will be deleted.\n\nType "${expected}" or RESET to confirm:`
                );
                if (!typed || (typed !== expected && typed !== 'RESET')) return;

                factoryResetBtn.disabled = true;
                if (unenrollBtn) unenrollBtn.disabled = true;
                try {
                    const data = await postUnenroll('factory_reset');
                    const details = [];
                    if (data.factory_reset_requested) details.push('factory reset requested');
                    if (data.pending_factory_reset) details.push('queued for next connection');
                    if (data.server_revoked) details.push('server trust revoked');
                    showLifecycleResult(
                        `${data.message || 'Factory reset requested'}${details.length ? ' (' + details.join(', ') + ')' : ''}`,
                        true
                    );
                    setTimeout(() => { window.location.href = config.adminDevicesUrl; }, 1500);
                } catch (error) {
                    showLifecycleResult(error.message || 'Failed to request factory reset', false);
                    factoryResetBtn.disabled = false;
                    if (unenrollBtn) unenrollBtn.disabled = false;
                }
            });
        }
    

const alertConfig = {
            managedUserId: null,
            systemId: systemId,
            page: 1,
            perPage: 50,
            search: '',
            eventType: '',
            sortBy: 'date',
            sortDir: 'desc'
        };

        const elements = {
            tbody: document.getElementById('alerts-tbody'),
            loading: document.getElementById('alerts-loading'),
            empty: document.getElementById('alerts-empty'),
            table: document.getElementById('alerts-table-container'),
            summary: document.getElementById('alert-summary-stats'),
            paginationInfo: document.getElementById('pagination-info'),
            prevBtn: document.getElementById('prev-page'),
            nextBtn: document.getElementById('next-page'),
            searchInput: document.getElementById('alert-search-input'),
            typeFilter: document.getElementById('alert-type-filter'),
            sortBySelect: document.getElementById('alert-sort-by'),
            sortDirSelect: document.getElementById('alert-sort-dir'),
            pruneBtn: document.getElementById('alert-prune-btn')
        };

        if (elements.tbody) {
        let searchTimeout = null;

        async function fetchAlerts() {
            elements.loading.style.display = 'block';
            elements.table.style.display = 'none';
            elements.empty.style.display = 'none';

            const params = new URLSearchParams({
                page: alertConfig.page,
                per_page: alertConfig.perPage,
                search: alertConfig.search,
                event_type: alertConfig.eventType,
                sort_by: alertConfig.sortBy,
                sort_dir: alertConfig.sortDir
            });
            if (alertConfig.managedUserId) params.append('managed_user_id', alertConfig.managedUserId);
            if (alertConfig.systemId) params.append('system_id', alertConfig.systemId);

            try {
                const response = await fetch(`/api/alerts?${params.toString()}`);
                const result = await response.json();

                if (result.success) {
                    renderAlerts(result.data.alerts);
                    updatePagination(result.data.pagination);
                    updateFilters(result.data.filters);
                    updateSummaryStats(result.data.pagination.total_items);
                }
            } catch (error) {
                console.error('Failed to fetch alerts:', error);
            } finally {
                elements.loading.style.display = 'none';
            }
        }

        function renderAlerts(alerts) {
            if (!alerts || alerts.length === 0) {
                elements.empty.style.display = 'block';
                return;
            }

            elements.table.style.display = 'block';
            elements.tbody.innerHTML = alerts.map(alert => `
            <tr>
                <td class="ps-3 small fw-bold text-secondary text-nowrap">
                    ${alert.occurred_at ? formatDate(alert.occurred_at) : 'Unknown'}
                </td>
                <td>
                    <strong class="text-body small">${alert.event_label}</strong>
                    <div class="text-secondary small fw-bold" style="font-size: 0.7rem;">ID: #${alert.id}</div>
                </td>
                <td><span class="badge bg-secondary">${alert.scope_label}</span></td>
                <td>
                    <div class="fw-bold small text-body">${formatStatus(alert.delivery_status)}</div>
                    <div class="text-secondary small fw-bold" style="font-size: 0.7rem;">
                        ${alert.delivery_attempts} attempt(s)
                        ${alert.last_delivery_error ? `<br><span class="text-danger">Error: ${alert.last_delivery_error}</span>` : ''}
                    </div>
                </td>
                <td class="pe-3 font-monospace text-secondary small" style="font-size: 0.75rem; max-width: 320px; overflow-wrap: break-word;">${alert.details_text}</td>
            </tr>
        `).join('');
        }

        function formatDate(dateStr) {
            const date = new Date(dateStr);
            return date.getFullYear() + '-' +
                String(date.getMonth() + 1).padStart(2, '0') + '-' +
                String(date.getDate()).padStart(2, '0') + ' ' +
                String(date.getHours()).padStart(2, '0') + ':' +
                String(date.getMinutes()).padStart(2, '0');
        }

        function formatStatus(status) {
            if (!status) return 'Unknown';
            return status.split('_').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
        }

        function updatePagination(pagination) {
            const start = (pagination.page - 1) * pagination.per_page + 1;
            const end = Math.min(pagination.page * pagination.per_page, pagination.total_items);

            elements.paginationInfo.textContent = pagination.total_items > 0
                ? `Showing ${start}-${end} of ${pagination.total_items} alerts`
                : 'No alerts found';

            elements.prevBtn.disabled = !pagination.has_prev;
            elements.nextBtn.disabled = !pagination.has_next;
        }

        function updateFilters(filters) {
            const currentType = elements.typeFilter.value;
            const options = ['<option value="">All Event Types</option>'];
            filters.event_types.forEach(et => {
                options.push(`<option value="${et.value}" ${et.value === currentType ? 'selected' : ''}>${et.label}</option>`);
            });
            elements.typeFilter.innerHTML = options.join('');
        }

        function updateSummaryStats(total) {
            elements.summary.innerHTML = `
            <div class="text-center px-3 py-1 bg-body border rounded shadow-sm" style="min-width: 100px;">
                <div class="text-secondary small fw-bold text-uppercase" style="font-size: 0.65rem; letter-spacing: 0.05em;">Filtered Results</div>
                <div class="fs-5 fw-bold text-body">${total}</div>
            </div>
        `;
        }

        elements.prevBtn.addEventListener('click', () => {
            if (alertConfig.page > 1) {
                alertConfig.page--;
                fetchAlerts();
            }
        });

        elements.nextBtn.addEventListener('click', () => {
            alertConfig.page++;
            fetchAlerts();
        });

        elements.searchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                alertConfig.search = e.target.value;
                alertConfig.page = 1;
                fetchAlerts();
            }, 400);
        });

        elements.typeFilter.addEventListener('change', (e) => {
            alertConfig.eventType = e.target.value;
            alertConfig.page = 1;
            fetchAlerts();
        });

        elements.sortBySelect.addEventListener('change', (e) => {
            alertConfig.sortBy = e.target.value;
            alertConfig.page = 1;
            fetchAlerts();
        });

        elements.sortDirSelect.addEventListener('change', (e) => {
            alertConfig.sortDir = e.target.value;
            alertConfig.page = 1;
            fetchAlerts();
        });

        elements.pruneBtn.addEventListener('click', async () => {
            const days = prompt('Prune alerts older than how many days?', '30');
            if (days === null) return;

            const daysInt = parseInt(days);
            if (isNaN(daysInt) || daysInt < 0) {
                alert('Please enter a valid number of days.');
                return;
            }

            if (!confirm(`Are you sure you want to permanently delete all alerts older than ${daysInt} days for this device?`)) {
                return;
            }

            try {
                const response = await fetch('/api/alerts/prune', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        older_than_days: daysInt,
                        system_id: alertConfig.systemId
                    })
                });
                const result = await response.json();
                if (result.success) {
                    alert(result.message);
                    alertConfig.page = 1;
                    fetchAlerts();
                }
            } catch (error) {
                alert('Failed to prune alerts.');
            }
        });

        fetchAlerts();
        }

const form = document.getElementById('android-policy-form');
        if (form) {

        const saveBtn = document.getElementById('save-policy-btn');
        const policyAlert = document.getElementById('policy-alert');
        const syncBadge = document.getElementById('policy-sync-badge');

        const forceAppsList = document.getElementById('force-apps-list');
        const addForceAppBtn = document.getElementById('add-force-app-btn');

        if (addForceAppBtn && forceAppsList) {
            const wizardModalEl = document.getElementById('add-app-wizard-modal');
            const wizardModal = new bootstrap.Modal(wizardModalEl);
            const validateBtn = document.getElementById('wizard-btn-validate');
            const apkUrlInput = document.getElementById('wizard-apk-url');
            const urlFeedback = document.getElementById('wizard-url-feedback');
            const confirmBtn = document.getElementById('wizard-btn-confirm');
            const backBtn = document.getElementById('wizard-btn-back');

            function showStep(stepNum) {
                document.querySelectorAll('.wizard-step').forEach(step => {
                    step.classList.add('d-none');
                });
                document.getElementById(`wizard-step-${stepNum}`).classList.remove('d-none');
                document.getElementById('wizard-error-box').classList.add('d-none');
            }

            function showWizardError(msg) {
                const errorBox = document.getElementById('wizard-error-box');
                const errorMsg = document.getElementById('wizard-error-message');
                errorMsg.textContent = msg;
                errorBox.classList.remove('d-none');
            }

            addForceAppBtn.addEventListener('click', () => {
                showStep(1);
                apkUrlInput.value = '';
                apkUrlInput.classList.remove('is-invalid');
                wizardModal.show();
            });

            validateBtn.addEventListener('click', async () => {
                const apkUrl = apkUrlInput.value.trim();
                if (!apkUrl) {
                    apkUrlInput.classList.add('is-invalid');
                    urlFeedback.textContent = 'APK URL is required.';
                    return;
                }
                
                try {
                    const parsedUrl = new URL(apkUrl);
                    if (parsedUrl.protocol !== 'https:') {
                        apkUrlInput.classList.add('is-invalid');
                        urlFeedback.textContent = 'URL must use HTTPS protocol.';
                        return;
                    }
                } catch (_) {
                    apkUrlInput.classList.add('is-invalid');
                    urlFeedback.textContent = 'Please enter a valid URL.';
                    return;
                }
                
                apkUrlInput.classList.remove('is-invalid');
                showStep(2);
                
                try {
                    const response = await fetch(`/api/devices/${encodeURIComponent(systemId)}/validate-apk-url`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                        },
                        body: JSON.stringify({ apk_url: apkUrl })
                    });
                    
                    const data = await response.json();
                    if (response.ok && data.success) {
                        document.getElementById('wizard-confirm-package').textContent = data.package_name;
                        document.getElementById('wizard-confirm-checksum').textContent = data.sha256_checksum;
                        document.getElementById('wizard-confirm-url').textContent = apkUrl;
                        showStep(3);
                    } else {
                        showStep(1);
                        showWizardError(data.message || 'Validation failed. Please verify the URL is correct and public.');
                    }
                } catch (err) {
                    showStep(1);
                    showWizardError(err.message || 'An error occurred during verification.');
                }
            });

            backBtn.addEventListener('click', () => {
                showStep(1);
            });

            confirmBtn.addEventListener('click', () => {
                const packageName = document.getElementById('wizard-confirm-package').textContent;
                const checksum = document.getElementById('wizard-confirm-checksum').textContent;
                const apkUrl = document.getElementById('wizard-confirm-url').textContent;
                
                const tr = document.createElement('tr');
                tr.className = 'force-app-row';
                tr.innerHTML = `
                    <td>
                        <input type="text" class="form-control form-control-sm app-package-name bg-body-secondary" placeholder="com.example.app" required readonly value="${packageName}">
                    </td>
                    <td>
                        <input type="url" class="form-control form-control-sm app-apk-url bg-body-secondary" placeholder="https://example.com/app.apk" required readonly value="${apkUrl}">
                    </td>
                    <td>
                        <input type="text" class="form-control form-control-sm app-checksum bg-body-secondary" placeholder="SHA-256 Hash" readonly value="${checksum}">
                    </td>
                    <td class="text-center">
                        <button type="button" class="btn btn-sm btn-outline-danger border-0 remove-app-btn"><i class="fas fa-trash-alt"></i></button>
                    </td>
                `;
                forceAppsList.appendChild(tr);
                
                tr.querySelector('.remove-app-btn').addEventListener('click', () => {
                    tr.remove();
                });
                
                wizardModal.hide();
            });

            forceAppsList.querySelectorAll('.remove-app-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.currentTarget.closest('tr').remove();
                });
            });
        }

        function showAlert(message, isSuccess) {
            policyAlert.textContent = message;
            policyAlert.className = `alert mb-3 alert-${isSuccess ? 'success' : 'danger'}`;
            policyAlert.classList.remove('d-none');
        }

        form.addEventListener('submit', async (e) => {
            e.preventDefault();

            saveBtn.disabled = true;
            const originalText = saveBtn.innerHTML;
            saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span> Saving...';
            policyAlert.classList.add('d-none');

            const forceApps = [];
            if (forceAppsList) {
                forceAppsList.querySelectorAll('.force-app-row').forEach(row => {
                    const packageName = row.querySelector('.app-package-name').value.trim();
                    const apkUrl = row.querySelector('.app-apk-url').value.trim();
                    const checksum = row.querySelector('.app-checksum').value.trim();
                    if (packageName && apkUrl) {
                        forceApps.push({
                            package_name: packageName,
                            apk_url: apkUrl,
                            sha256_checksum: checksum
                        });
                    }
                });
            }

            const payload = {
                screen_capture_disabled: document.getElementById('screen_capture_disabled').checked,
                camera_access: document.getElementById('camera_access').value,
                microphone_access: document.getElementById('microphone_access').value,
                usb_data_access: document.getElementById('usb_data_access').value,
                developer_settings: document.getElementById('developer_settings').value,
                install_apps_disabled: document.getElementById('install_apps_disabled').checked,
                uninstall_apps_disabled: document.getElementById('uninstall_apps_disabled').checked,
                factory_reset_disabled: document.getElementById('factory_reset_disabled').checked,
                adjust_volume_disabled: document.getElementById('adjust_volume_disabled').checked,
                modify_accounts_disabled: document.getElementById('modify_accounts_disabled').checked,
                mount_physical_media_disabled: document.getElementById('mount_physical_media_disabled').checked,
                bluetooth_disabled: document.getElementById('bluetooth_disabled').checked,
                outgoing_calls_disabled: document.getElementById('outgoing_calls_disabled').checked,
                sms_disabled: document.getElementById('sms_disabled').checked,
                block_wifi_tethering: document.getElementById('block_wifi_tethering').checked,
                block_nfc: document.getElementById('block_nfc').checked,
                short_support_message: document.getElementById('short_support_message').value,
                long_support_message: document.getElementById('long_support_message').value,
                force_installed_apps: forceApps
            };

            try {
                const response = await fetch(`/api/devices/${encodeURIComponent(systemId)}/android-device-policy`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                    },
                    body: JSON.stringify(payload)
                });
                const data = await response.json();

                if (response.ok && data.success) {
                    showAlert(data.message || 'Restrictions saved successfully', true);
                    if (data.policy && data.policy.is_synced) {
                        syncBadge.className = 'badge bg-success-subtle text-success border border-success-subtle px-2.5 py-1.5 fw-bold';
                        syncBadge.innerHTML = '<i class="fas fa-check-circle me-1"></i> Synced to Device';
                    } else {
                        syncBadge.className = 'badge bg-warning-subtle text-warning border border-warning-subtle px-2.5 py-1.5 fw-bold';
                        syncBadge.innerHTML = '<i class="fas fa-sync me-1"></i> Sync Pending';
                    }
                } else {
                    showAlert(data.message || 'Failed to save restrictions', false);
                }
            } catch (err) {
                showAlert(err.message || 'An error occurred while saving the policy', false);
            } finally {
                saveBtn.disabled = false;
                saveBtn.innerHTML = originalText;
            }
        });
        }

    if (config.screenshotSettings) {
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
        const historyAlert = document.getElementById('screen-history-alert');
        const historyLoading = document.getElementById('screen-history-loading');
        const historyEmpty = document.getElementById('screen-history-empty');
        const historySyncBadge = document.getElementById('screen-history-sync-badge');

        // Timeline components (Linux Only)
        const timelineContainer = document.getElementById('screen-history-timeline-container');
        const previewImg = document.getElementById('timeline-preview-img');
        const overlayTime = document.getElementById('timeline-overlay-time');
        const overlayWindow = document.getElementById('timeline-overlay-window');
        const overlayUser = document.getElementById('timeline-overlay-user');
        const downloadLink = document.getElementById('timeline-download-link');
        
        const timeStart = document.getElementById('timeline-time-start');
        const activeTimeLabel = document.getElementById('timeline-active-time');
        const timeEnd = document.getElementById('timeline-time-end');
        
        const slider = document.getElementById('timeline-range-slider');
        const ticksContainer = document.getElementById('timeline-ticks');
        
        const prevBtn = document.getElementById('timeline-prev-btn');
        const playBtn = document.getElementById('timeline-play-btn');
        const playLabel = document.getElementById('timeline-play-label');
        const nextBtn = document.getElementById('timeline-next-btn');
        const speedSelect = document.getElementById('timeline-speed-select');
        const counterLabel = document.getElementById('timeline-counter-label');

        let screenshotItems = [];
        let currentIndex = 0;
        let playInterval = null;

        function showHistoryAlert(message, isSuccess) {
            if (!historyAlert) return;
            historyAlert.textContent = message;
            historyAlert.className = `alert mb-3 alert-${isSuccess ? 'success' : 'danger'}`;
            historyAlert.classList.remove('d-none');
        }

        function updateHistorySyncBadge(isSynced) {
            if (!historySyncBadge) return;
            if (isSynced) {
                historySyncBadge.className = 'badge bg-success-subtle text-success border border-success-subtle px-2.5 py-1.5 fw-bold';
                historySyncBadge.innerHTML = '<i class="fas fa-check-circle me-1"></i> Synced to Agent';
            } else {
                historySyncBadge.className = 'badge bg-warning-subtle text-warning border border-warning-subtle px-2.5 py-1.5 fw-bold';
                historySyncBadge.innerHTML = '<i class="fas fa-sync me-1"></i> Sync Pending';
            }
        }

        function renderActiveFrame() {
            if (!screenshotItems.length || currentIndex < 0 || currentIndex >= screenshotItems.length) return;
            const item = screenshotItems[currentIndex];
            
            if (previewImg) {
                previewImg.src = `/api/screenshots/${item.id}`;
            }
            if (overlayTime) {
                const capturedDate = new Date(item.captured_at);
                overlayTime.innerHTML = `<i class="fas fa-clock me-1 text-warning"></i>${capturedDate.toLocaleTimeString()}`;
                if (activeTimeLabel) {
                    activeTimeLabel.textContent = capturedDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                }
            }
            if (overlayWindow) {
                overlayWindow.textContent = item.active_window_title || 'No active window';
                overlayWindow.title = item.active_window_title || 'No active window';
            }
            if (overlayUser) {
                overlayUser.textContent = item.linux_username || 'unknown';
            }
            if (downloadLink) {
                downloadLink.href = `/api/screenshots/${item.id}`;
            }
            if (slider) {
                slider.value = currentIndex;
            }
            if (counterLabel) {
                counterLabel.textContent = `${currentIndex + 1} / ${screenshotItems.length}`;
            }
        }

        async function loadHistoryScreenshots() {
            if (historyLoading) historyLoading.style.display = 'block';
            if (historyEmpty) historyEmpty.style.display = 'none';
            if (timelineContainer) timelineContainer.classList.add('d-none');
            stopAutoplay();

            try {
                const response = await fetch(`/api/devices/${encodeURIComponent(systemId)}/screenshots?page=1&per_page=100`);
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.message || 'Failed to load screenshots');
                }

                // Reverse screenshots so they are rendered from oldest to newest (chronological order)
                screenshotItems = (data.items || []).reverse();

                if (!screenshotItems.length) {
                    if (historyEmpty) historyEmpty.style.display = 'block';
                    return;
                }

                if (timelineContainer) timelineContainer.classList.remove('d-none');

                if (slider) {
                    slider.min = 0;
                    slider.max = screenshotItems.length - 1;
                    slider.value = screenshotItems.length - 1;
                }
                currentIndex = screenshotItems.length - 1;

                if (ticksContainer) {
                    ticksContainer.innerHTML = '';
                    const numTicks = 10;
                    for (let i = 0; i < numTicks; i++) {
                        const tick = document.createElement('div');
                        tick.className = 'timeline-tick-line';
                        ticksContainer.appendChild(tick);
                    }
                }

                if (timeStart && screenshotItems[0]) {
                    const firstTime = new Date(screenshotItems[0].captured_at);
                    timeStart.textContent = firstTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                }
                if (timeEnd && screenshotItems[screenshotItems.length - 1]) {
                    const lastTime = new Date(screenshotItems[screenshotItems.length - 1].captured_at);
                    timeEnd.textContent = lastTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                }

                renderActiveFrame();

            } catch (error) {
                showHistoryAlert(error.message || 'Failed to load screenshots', false);
            } finally {
                if (historyLoading) historyLoading.style.display = 'none';
            }
        }

        if (slider) {
            slider.addEventListener('input', () => {
                currentIndex = parseInt(slider.value, 10);
                renderActiveFrame();
            });
        }

        if (prevBtn) {
            prevBtn.addEventListener('click', () => {
                stopAutoplay();
                if (currentIndex > 0) {
                    currentIndex--;
                    renderActiveFrame();
                }
            });
        }

        if (nextBtn) {
            nextBtn.addEventListener('click', () => {
                stopAutoplay();
                if (currentIndex < screenshotItems.length - 1) {
                    currentIndex++;
                    renderActiveFrame();
                }
            });
        }

        if (playBtn) {
            playBtn.addEventListener('click', () => {
                if (playInterval) {
                    stopAutoplay();
                } else {
                    startAutoplay();
                }
            });
        }

        function startAutoplay() {
            if (screenshotItems.length <= 1) return;
            
            if (currentIndex >= screenshotItems.length - 1) {
                currentIndex = 0;
                renderActiveFrame();
            }

            const speed = speedSelect ? parseInt(speedSelect.value, 10) : 1000;
            playInterval = setInterval(() => {
                if (currentIndex < screenshotItems.length - 1) {
                    currentIndex++;
                    renderActiveFrame();
                } else {
                    stopAutoplay();
                }
            }, speed);

            if (playBtn) {
                playBtn.classList.remove('btn-warning');
                playBtn.classList.add('btn-secondary');
                if (playLabel) playLabel.textContent = 'Pause';
                const playIcon = playBtn.querySelector('i');
                if (playIcon) {
                    playIcon.className = 'fas fa-pause me-1';
                }
            }
        }

        function stopAutoplay() {
            if (playInterval) {
                clearInterval(playInterval);
                playInterval = null;
            }
            if (playBtn) {
                playBtn.classList.remove('btn-secondary');
                playBtn.classList.add('btn-warning');
                if (playLabel) playLabel.textContent = 'Play';
                const playIcon = playBtn.querySelector('i');
                if (playIcon) {
                    playIcon.className = 'fas fa-play me-1';
                }
            }
        }

        if (speedSelect) {
            speedSelect.addEventListener('change', () => {
                if (playInterval) {
                    stopAutoplay();
                    startAutoplay();
                }
            });
        }

        // Global hotkey controls
        window.addEventListener('keydown', (event) => {
            const activityTab = document.getElementById('activity-tab');
            if (!activityTab || activityTab.classList.contains('d-none')) return;
            
            if (document.activeElement && (
                document.activeElement.tagName === 'INPUT' || 
                document.activeElement.tagName === 'TEXTAREA' || 
                document.activeElement.tagName === 'SELECT'
            )) {
                return;
            }

            if (event.code === 'Space') {
                event.preventDefault();
                if (playBtn) playBtn.click();
            } else if (event.code === 'ArrowLeft') {
                event.preventDefault();
                if (prevBtn) prevBtn.click();
            } else if (event.code === 'ArrowRight') {
                event.preventDefault();
                if (nextBtn) nextBtn.click();
            }
        }, { signal });

        document.getElementById('screen-history-save-btn')?.addEventListener('click', async () => {
            const saveBtn = document.getElementById('screen-history-save-btn');
            const enabledInput = document.getElementById('screen-history_enabled');
            const enabledBeforeSave = enabledInput?.checked ?? false;
            saveBtn.disabled = true;
            if (historyAlert) historyAlert.classList.add('d-none');
            try {
                const response = await fetch(`/api/devices/${encodeURIComponent(systemId)}/screenshot-settings`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken,
                    },
                    body: JSON.stringify({
                        enabled: enabledInput.checked,
                        interval_seconds: Number(document.getElementById('screen-history_interval_seconds').value),
                        retention_hours: Number(document.getElementById('screen-history_retention_hours').value),
                    }),
                });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.message || 'Failed to save screen history settings');
                }
                showHistoryAlert('Screen history settings saved.', true);
                updateHistorySyncBadge(Boolean(data.settings?.is_synced));
                const enabledAfterSave = Boolean(data.settings?.enabled ?? enabledInput.checked);
                if (enabledAfterSave !== enabledBeforeSave) {
                    refreshDeviceDetailPreserveTab();
                }
            } catch (error) {
                showHistoryAlert(error.message || 'Failed to save screen history settings', false);
            } finally {
                saveBtn.disabled = false;
            }
        });

        document.getElementById('screen-history-capture-btn')?.addEventListener('click', async () => {
            const captureBtn = document.getElementById('screen-history-capture-btn');
            captureBtn.disabled = true;
            if (historyAlert) historyAlert.classList.add('d-none');
            try {
                const response = await fetch(`/api/devices/${encodeURIComponent(systemId)}/screenshots/capture`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken,
                    },
                    body: JSON.stringify({}),
                });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.message || 'Failed to request screenshot');
                }
                showHistoryAlert('Screenshot capture requested. Refreshing gallery shortly...', true);
                setTimeout(() => loadHistoryScreenshots(), 3000);
            } catch (error) {
                showHistoryAlert(error.message || 'Failed to request screenshot', false);
            } finally {
                captureBtn.disabled = false;
            }
        });

        document.getElementById('screen-history-capture-now-btn-empty')?.addEventListener('click', () => {
            document.getElementById('screen-history-capture-btn')?.click();
        });

        document.getElementById('screen-history-clear-btn')?.addEventListener('click', async () => {
            if (!window.confirm('Delete all stored screenshots for this device?')) {
                return;
            }
            const clearBtn = document.getElementById('screen-history-clear-btn');
            clearBtn.disabled = true;
            if (historyAlert) historyAlert.classList.add('d-none');
            try {
                const response = await fetch(`/api/devices/${encodeURIComponent(systemId)}/screenshots`, {
                    method: 'DELETE',
                    headers: { 'X-CSRFToken': csrfToken },
                });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.message || 'Failed to clear screenshots');
                }
                showHistoryAlert(`Deleted ${data.deleted || 0} screenshot(s).`, true);
                await loadHistoryScreenshots();
            } catch (error) {
                showHistoryAlert(error.message || 'Failed to clear screenshots', false);
            } finally {
                clearBtn.disabled = false;
            }
        });

        loadHistoryScreenshots();
    }

    }

    let lastBootedRoot = null;

    function boot() {
        const root = getRoot();
        if (!root) return;
        if (lastBootedRoot === root) return;
        lastBootedRoot = root;
        init();
    }

    if (window.deviceDetailScriptLoaded) {
        boot();
        return;
    }
    window.deviceDetailScriptLoaded = true;

    if (window.GuardianSPA && typeof window.GuardianSPA.onRoute === 'function') {
        window.GuardianSPA.onRoute(teardownDeviceDetailPage);
    }
    document.addEventListener('guardian:page-ready', boot);
    document.addEventListener('guardian:route', teardownDeviceDetailPage);
    if (document.readyState !== 'loading') boot();
    else document.addEventListener('DOMContentLoaded', boot);
})();
