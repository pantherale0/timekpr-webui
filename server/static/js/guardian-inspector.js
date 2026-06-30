(function () {
    'use strict';

    function i18n(key, params) {
        return typeof window.guardianI18n === 'function' ? window.guardianI18n(key, params) : key;
    }

    let blocklistModal = null;
    let activeInspectType = null;
    let activeInspectValue = null;

    // Toast/notifications helper fallback
    function showToast(message, type = 'success') {
        if (window.showNotification) {
            window.showNotification(message, type);
        } else {
            const toast = document.getElementById('notification-toast');
            if (toast) {
                const msgEl = toast.querySelector('.toast-message');
                if (msgEl) msgEl.textContent = message;
                toast.classList.add('show');
                setTimeout(() => toast.classList.remove('show'), window.GUARDIAN_TOAST_VISIBLE_MS || 10000);
            } else {
                alert(message);
            }
        }
    }

    function getUserId() {
        if (window.state && window.state.userId) {
            return window.state.userId;
        }
        const path = window.location.pathname;
        const match = path.match(/\/(?:users|user|stats|history)\/(\d+)/);
        if (match) {
            return parseInt(match[1], 10);
        }
        const el = document.querySelector('[data-user-id]');
        if (el) {
            return parseInt(el.getAttribute('data-user-id'), 10);
        }
        return null;
    }

    function formatDateTime(isoString) {
        if (!isoString) return '—';
        try {
            const d = new Date(isoString);
            return d.toLocaleString([], {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });
        } catch (e) {
            return isoString;
        }
    }

    function formatDuration(seconds) {
        if (!seconds || seconds <= 0) return '0s';
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = seconds % 60;
        
        let out = '';
        if (h > 0) out += `${h}h `;
        if (m > 0 || h > 0) out += `${m}m `;
        out += `${s}s`;
        return out;
    }

    // Toggle Sidebar visibility
    function toggleSidebar(isOpen) {
        const sidebar = document.getElementById('guardian-inspector-sidebar');
        const overlay = document.getElementById('guardian-inspector-overlay');
        
        if (sidebar && overlay) {
            if (isOpen) {
                sidebar.classList.add('open');
                overlay.classList.add('open');
            } else {
                sidebar.classList.remove('open');
                overlay.classList.remove('open');
                activeInspectType = null;
                activeInspectValue = null;
            }
        }
    }

    // Open Inspector details
    async function openInspector(type, value) {
        const userId = getUserId();
        if (!userId) {
            console.error('Guardian Inspector: Could not determine managed user ID.');
            return;
        }

        activeInspectType = type;
        activeInspectValue = value;

        // Show sidebar and loader
        toggleSidebar(true);
        const loader = document.getElementById('inspector-loading');
        const content = document.getElementById('inspector-content');
        if (loader && content) {
            loader.classList.remove('d-none');
            content.classList.add('d-none');
        }

        // Reset UI actions
        const whitelistBtn = document.getElementById('btn-inspect-whitelist');
        const blockBtn = document.getElementById('btn-inspect-block');
        const channelBlockContainer = document.getElementById('inspect-channel-block-container');

        if (whitelistBtn) whitelistBtn.classList.remove('d-none');
        if (blockBtn) blockBtn.classList.remove('d-none');
        if (channelBlockContainer) channelBlockContainer.classList.add('d-none');

        // If inspecting channel/creator: bypass inspect api and display custom UI controls
        if (type === 'channel') {
            if (loader && content) {
                loader.classList.add('d-none');
                content.classList.remove('d-none');
            }
            
            const titleEl = document.getElementById('inspect-title');
            const iconEl = document.getElementById('inspect-icon-container');
            const badgeEl = document.getElementById('inspect-type-badge');
            
            if (titleEl) titleEl.textContent = value;
            if (iconEl) iconEl.innerHTML = '<i class="fab fa-youtube text-danger"></i>';
            if (badgeEl) badgeEl.textContent = 'Content Creator';
            
            const vEl = document.getElementById('inspect-stat-visits');
            const dLabel = document.getElementById('inspect-duration-label');
            const tEl = document.getElementById('inspect-stat-time');
            const lEl = document.getElementById('inspect-stat-last');
            
            if (vEl) vEl.textContent = '—';
            if (dLabel) dLabel.textContent = 'Watch Time';
            if (tEl) tEl.textContent = '—';
            if (lEl) lEl.textContent = '—';
            
            const devEl = document.getElementById('inspect-devices-list');
            if (devEl) devEl.innerHTML = '<div class="list-group-item bg-transparent text-muted small py-1 px-0 border-0">Creator channel stats are aggregated.</div>';
            
            const restEl = document.getElementById('inspect-restrictions-status');
            if (restEl) {
                restEl.className = 'alert alert-info py-2 px-3 small mb-0';
                restEl.innerHTML = '<i class="fas fa-info-circle me-1"></i>Channel/Video restrictions are managed via video policies.';
            }

            if (whitelistBtn) whitelistBtn.classList.add('d-none');
            if (blockBtn) blockBtn.classList.add('d-none');
            if (channelBlockContainer) channelBlockContainer.classList.remove('d-none');
            return;
        }

        // Fetch inspect details from API
        try {
            const res = await fetch(`/api/user/${userId}/inspect?type=${type}&value=${encodeURIComponent(value)}`);
            const data = await res.json();

            if (!data.success) {
                showToast(data.message || 'Failed to inspect activity.', 'danger');
                toggleSidebar(false);
                return;
            }

            if (loader && content) {
                loader.classList.add('d-none');
                content.classList.remove('d-none');
            }

            // Render Title & Icon
            const titleEl = document.getElementById('inspect-title');
            if (titleEl) titleEl.textContent = value;
            
            const iconContainer = document.getElementById('inspect-icon-container');
            const badgeEl = document.getElementById('inspect-type-badge');
            const visitsEl = document.getElementById('inspect-stat-visits');
            const dLabel = document.getElementById('inspect-duration-label');
            const timeEl = document.getElementById('inspect-stat-time');
            const lastEl = document.getElementById('inspect-stat-last');

            if (type === 'domain') {
                if (iconContainer) iconContainer.innerHTML = '<i class="fas fa-globe text-primary"></i>';
                if (badgeEl) badgeEl.textContent = 'Website Domain';
                if (visitsEl) visitsEl.textContent = data.total_visits || 0;
                if (dLabel) dLabel.textContent = 'First Visited';
                if (timeEl) timeEl.textContent = formatDateTime(data.first_seen);
                if (lastEl) lastEl.textContent = formatDateTime(data.last_seen);
            } else {
                if (iconContainer) iconContainer.innerHTML = '<i class="fas fa-cube text-success"></i>';
                if (badgeEl) badgeEl.textContent = 'Application';
                if (visitsEl) visitsEl.textContent = data.total_launches || 0;
                if (dLabel) dLabel.textContent = 'Total Active Duration';
                if (timeEl) timeEl.textContent = formatDuration(data.total_duration);
                if (lastEl) lastEl.textContent = formatDateTime(data.last_seen);
            }

            // Render Device Distribution
            const devicesList = document.getElementById('inspect-devices-list');
            if (devicesList) {
                devicesList.innerHTML = '';
                if (data.device_distribution && Object.keys(data.device_distribution).length > 0) {
                    Object.entries(data.device_distribution).forEach(([device, count]) => {
                        const item = document.createElement('div');
                        item.className = 'list-group-item bg-transparent d-flex justify-content-between align-items-center py-1.5 px-0 border-bottom-0';
                        item.innerHTML = `
                            <span class="small text-slate"><i class="fas fa-desktop me-1.5 text-secondary"></i>${device}</span>
                            <span class="badge bg-secondary-subtle text-secondary border rounded-pill">${count}</span>
                        `;
                        devicesList.appendChild(item);
                    });
                } else {
                    devicesList.innerHTML = '<div class="list-group-item bg-transparent text-muted small py-1 px-0 border-0">No device records.</div>';
                }
            }

            // Restrictions Status
            const restrictionsStatus = document.getElementById('inspect-restrictions-status');
            if (restrictionsStatus) {
                restrictionsStatus.className = 'alert py-2 px-3 small mb-0';
                
                if (data.whitelisted) {
                    restrictionsStatus.classList.add('alert-success');
                    restrictionsStatus.innerHTML = '<i class="fas fa-check-circle me-1 text-success"></i><strong>Allowed:</strong> Whitelist exception active.';
                    if (whitelistBtn) {
                        whitelistBtn.disabled = true;
                        whitelistBtn.innerHTML = '<i class="fas fa-check-circle me-2"></i>Approved';
                    }
                } else {
                    if (whitelistBtn) {
                        whitelistBtn.disabled = false;
                        whitelistBtn.innerHTML = '<i class="fas fa-check-circle me-2"></i>Whitelist (Approve)';
                    }

                    if (type === 'domain' && data.active_shields && data.active_shields.length > 0) {
                        restrictionsStatus.classList.add('alert-danger');
                        const names = data.active_shields.map(s => s.name).join(', ');
                        restrictionsStatus.innerHTML = `<i class="fas fa-shield-alt me-1 text-danger"></i><strong>Blocked:</strong> Contained in shield list(s): <strong>${names}</strong>`;
                    } else if (type === 'app' && data.active_rules && data.active_rules.length > 0) {
                        restrictionsStatus.classList.add('alert-danger');
                        const blockedDevices = data.active_rules.filter(r => r.preset === 'blocked').map(r => r.device).join(', ');
                        if (blockedDevices) {
                            restrictionsStatus.innerHTML = `<i class="fas fa-ban me-1 text-danger"></i><strong>Blocked:</strong> Restrained on: <strong>${blockedDevices}</strong>`;
                        } else {
                            restrictionsStatus.classList.add('alert-info');
                            restrictionsStatus.innerHTML = '<i class="fas fa-info-circle me-1"></i>No restriction blocks active.';
                        }
                    } else {
                        restrictionsStatus.classList.add('alert-info');
                        restrictionsStatus.innerHTML = '<i class="fas fa-info-circle me-1"></i>No active restrictions.';
                    }
                }
            }

        } catch (err) {
            console.error(err);
            showToast('Connection failed. Please retry.', 'danger');
            toggleSidebar(false);
        }
    }

    // Whitelist action handler
    async function handleWhitelist() {
        const userId = getUserId();
        if (!userId || !activeInspectType || !activeInspectValue) return;

        const whitelistBtn = document.getElementById('btn-inspect-whitelist');
        if (whitelistBtn) {
            whitelistBtn.disabled = true;
            whitelistBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Approving...';
        }

        try {
            const formData = new FormData();
            formData.append('type', activeInspectType);
            formData.append('value', activeInspectValue);

            const res = await fetch(`/api/user/${userId}/whitelist`, {
                method: 'POST',
                body: formData
            });
            const data = await res.json();

            if (data.success) {
                showToast(data.message);
                // Refresh inspector content
                openInspector(activeInspectType, activeInspectValue);
            } else {
                showToast(data.message || 'Approval failed.', 'danger');
                if (whitelistBtn) {
                    whitelistBtn.disabled = false;
                    whitelistBtn.innerHTML = '<i class="fas fa-check-circle me-2"></i>Whitelist (Approve)';
                }
            }
        } catch (err) {
            console.error(err);
            showToast('Communication failed.', 'danger');
            if (whitelistBtn) {
                whitelistBtn.disabled = false;
                whitelistBtn.innerHTML = '<i class="fas fa-check-circle me-2"></i>Whitelist (Approve)';
            }
        }
    }

    // Block action trigger
    async function handleBlockTrigger() {
        if (!activeInspectType || !activeInspectValue) return;

        if (activeInspectType === 'app') {
            // Apps block directly without list prompt
            if (confirm(`Are you sure you want to block the application "${activeInspectValue}" immediately?`)) {
                await executeBlockSubmit();
            }
        } else if (activeInspectType === 'domain') {
            // Domains prompt for blocklist selection
            const targetEl = document.getElementById('blocklist-prompt-target');
            const newNameEl = document.getElementById('blocklist-new-name');
            
            if (targetEl) targetEl.textContent = activeInspectValue;
            if (newNameEl) newNameEl.value = '';
            
            // Populate choices from API
            const selectEl = document.getElementById('blocklist-select');
            if (selectEl) {
                selectEl.innerHTML = '<option value="">-- Choose an existing list --</option>';
                
                try {
                    const res = await fetch('/api/blocklists/sources/manual');
                    const data = await res.json();
                    if (data.success && data.sources) {
                        data.sources.forEach(src => {
                            const opt = document.createElement('option');
                            opt.value = src.id;
                            opt.textContent = src.name;
                            selectEl.appendChild(opt);
                        });
                    }
                } catch (e) {
                    console.error('Failed to load blocklists options', e);
                }
            }

            const modalEl = document.getElementById('blocklistPromptModal');
            if (modalEl) {
                if (!blocklistModal) {
                    blocklistModal = new bootstrap.Modal(modalEl);
                }
                blocklistModal.show();
            }
        }
    }

    // Execute Block submission
    async function executeBlockSubmit() {
        const userId = getUserId();
        if (!userId || !activeInspectType || !activeInspectValue) return;

        const blockSourceId = document.getElementById('blocklist-select')?.value || '';
        const newSourceName = document.getElementById('blocklist-new-name')?.value.trim() || '';

        if (activeInspectType === 'domain' && !blockSourceId && !newSourceName) {
            alert(i18n('guardian-inspector.please_select_an_existing_shield'));
            return;
        }

        const confirmBtn = document.getElementById('btn-blocklist-confirm');
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Applying...';
        }

        try {
            const formData = new FormData();
            formData.append('type', activeInspectType);
            formData.append('value', activeInspectValue);
            formData.append('source_id', blockSourceId);
            formData.append('new_source_name', newSourceName);

            const res = await fetch(`/api/user/${userId}/block`, {
                method: 'POST',
                body: formData
            });
            const data = await res.json();

            if (data.success) {
                showToast(data.message);
                if (blocklistModal) blocklistModal.hide();
                // Refresh details
                openInspector(activeInspectType, activeInspectValue);
            } else {
                showToast(data.message || 'Block failed.', 'danger');
            }
        } catch (err) {
            console.error(err);
            showToast('Communication failed.', 'danger');
        } finally {
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.innerHTML = 'Apply Block';
            }
        }
    }

    // Init delegated events
    document.addEventListener('click', function(event) {
        const row = event.target.closest('.interactive-inspect-row');
        if (row) {
            if (event.target.tagName === 'A' || event.target.closest('a')) {
                return;
            }
            const type = row.getAttribute('data-inspect-type');
            const value = row.getAttribute('data-inspect-value');
            if (type && value) {
                openInspector(type, value);
            }
        }
    });

    // Wire elements once DOM loaded (globally in shell)
    const setupListeners = () => {
        const closeBtn = document.getElementById('guardian-inspector-close');
        const overlay = document.getElementById('guardian-inspector-overlay');
        const whitelistBtn = document.getElementById('btn-inspect-whitelist');
        const blockBtn = document.getElementById('btn-inspect-block');
        const confirmBlockBtn = document.getElementById('btn-blocklist-confirm');

        if (closeBtn) closeBtn.addEventListener('click', () => toggleSidebar(false));
        if (overlay) overlay.addEventListener('click', () => toggleSidebar(false));
        if (whitelistBtn) whitelistBtn.addEventListener('click', handleWhitelist);
        if (blockBtn) blockBtn.addEventListener('click', handleBlockTrigger);
        if (confirmBlockBtn) confirmBlockBtn.addEventListener('click', executeBlockSubmit);
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', setupListeners);
    } else {
        setupListeners();
    }

})();
