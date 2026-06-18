/**
 * Guardian profile wizard — live summary and responsibility slider.
 */
(function () {
    const MATURITY_FROM_SLIDER = ['low', 'medium', 'high'];
    const SLIDER_LABELS = ['Open Dialogue', 'Balanced Watch', 'Assisted Steps'];

    function friendlyPackName(id, presetNameById) {
        const names = {
            malware_phishing: 'Malware & phishing shields',
            adult_explicit: 'Adult & explicit content',
            gambling: 'Gambling sites',
            social_media: 'Social networks',
            fake_news: 'Misleading news sources',
            vpn_proxy_bypass: 'Anti-Bypass Watching (VPN & proxy)',
            ai_chat: 'AI chat assistants',
        };
        return names[id] || presetNameById[id] || id;
    }

    function formatHoursLine(hours) {
        const weekday = hours.weekday;
        const weekend = hours.weekend != null ? hours.weekend : weekday;
        if (weekday == null) return null;
        const wd = Number(weekday);
        const we = Number(weekend);
        if (wd <= 0 && we <= 0) return null;
        return `Routine Locks: about ${wd} hour${wd === 1 ? '' : 's'} on school nights and ${we} hour${we === 1 ? '' : 's'} on weekends — sessions wind down automatically when the allowance is spent.`;
    }

    function buildSummaryBullets(bundle, ageMeta, matMeta, presetNameById) {
        const bullets = [];
        const hoursLine = formatHoursLine(bundle.weekly_schedule_hours || {});
        if (hoursLine) bullets.push(hoursLine);

        const packs = (bundle.marketplace_preset_ids || []).map(id =>
            friendlyPackName(id, presetNameById),
        );
        if (packs.length) {
            bullets.push(`Shielded Browsing: ${packs.join(', ')} active across managed devices.`);
        }

        const approval = bundle.approval_settings || {};
        if (approval.domain_access_mode === 'approval_on_block') {
            bullets.push('Family Dialogue Settings: blocked sites open a calm request channel instead of a hard dead-end.');
        }
        if (approval.app_launch_mode === 'allowlist') {
            bullets.push('Routine Locks: new apps need your approval before they can launch.');
        }

        const linux = bundle.linux_device_policy || {};
        if (linux.terminal_access_disabled) {
            bullets.push('Assisted Steps: command-line tools stay closed to keep exploits off the table.');
        }
        if (linux.install_software_disabled) {
            bullets.push('Assisted Steps: unapproved downloads are paused until you say yes.');
        }
        const chrome = linux.chrome_policies || {};
        if (chrome.block_other_extensions) {
            bullets.push('Anti-Bypass Watching: only Guardian-approved browser tools may run.');
        }

        if (!bullets.length) {
            bullets.push('A gentle baseline tuned for this growth milestone.');
        }

        return bullets;
    }

    function maturityFromSliderValue(value) {
        const idx = Math.max(0, Math.min(2, parseInt(value, 10) || 0));
        return MATURITY_FROM_SLIDER[idx];
    }

    function updateWizardSummary(options) {
        const {
            matrix,
            presetNameById,
            ageSelector,
            maturityInputId,
            sliderId,
            summaryTitleId,
            summaryListId,
            narrativeId,
            sliderLabelId,
        } = options;

        const ageEl = document.querySelector(`${ageSelector}:checked`) || document.querySelector(ageSelector);
        const age = ageEl ? ageEl.value : null;
        const slider = document.getElementById(sliderId);
        const maturityInput = document.getElementById(maturityInputId);
        let maturity = maturityInput ? maturityInput.value : null;

        if (slider && maturityInput) {
            const idx = parseInt(slider.value, 10) || 0;
            maturity = maturityFromSliderValue(idx);
            maturityInput.value = maturity;
            if (sliderLabelId) {
                const labelEl = document.getElementById(sliderLabelId);
                if (labelEl) labelEl.textContent = SLIDER_LABELS[idx] || '';
            }
        }

        const titleEl = document.getElementById(summaryTitleId);
        const listEl = document.getElementById(summaryListId);
        const narrativeEl = narrativeId ? document.getElementById(narrativeId) : null;

        if (!titleEl || !listEl || !matrix.bundles) return;

        const ageMeta = age && matrix.age_brackets ? matrix.age_brackets[age] : null;
        const matMeta = maturity && matrix.maturity_levels ? matrix.maturity_levels[maturity] : null;
        const key = age && maturity ? `${age}_${maturity}` : null;
        const bundle = key ? matrix.bundles[key] : null;

        if (narrativeEl && matMeta) {
            narrativeEl.textContent = matMeta.parent_translation || matMeta.description || '';
        }

        if (!bundle || !ageMeta) {
            titleEl.textContent = 'Baseline Summary';
            listEl.innerHTML = '<li>Choose a growth milestone and responsibility level to preview your family baseline.</li>';
            return;
        }

        const profileTitle = ageMeta.profile_title || ageMeta.label;
        const ageLabel = ageMeta.label || age;
        titleEl.textContent = `Baseline Summary: ${profileTitle} Profile (Age ${ageLabel})`;

        const bullets = buildSummaryBullets(bundle, ageMeta, matMeta, presetNameById);
        listEl.innerHTML = bullets.map(line => `<li>${line}</li>`).join('');
    }

    function bindWizard(options) {
        const matrixEl = document.getElementById(options.matrixDataId);
        const presetsEl = document.getElementById(options.presetsDataId);
        if (!matrixEl) return;

        const matrix = JSON.parse(matrixEl.textContent || '{}');
        const marketplacePresets = presetsEl ? JSON.parse(presetsEl.textContent || '[]') : [];
        const presetNameById = Object.fromEntries(marketplacePresets.map(p => [p.id, p.name]));
        const fullOptions = { matrix, presetNameById, ...options };

        const refresh = () => updateWizardSummary(fullOptions);

        document.querySelectorAll(options.ageSelector).forEach(el => {
            el.addEventListener('change', refresh);
        });

        const slider = document.getElementById(options.sliderId);
        if (slider) {
            slider.addEventListener('input', refresh);
        }

        refresh();
        window.guardianRefreshWizardSummary = refresh;
    }

    function bindCreateStepper(options) {
        const {
            modalId,
            formId,
            panelSelector = '.guardian-wizard-panel',
            prevBtnId,
            nextBtnId,
            submitBtnId,
            cancelBtnId,
            progressDotSelector = '.guardian-wizard-progress-dot',
            usernameInputId,
            totalSteps = 4,
            onStepChange,
        } = options;

        const modal = document.getElementById(modalId);
        const form = document.getElementById(formId);
        const prevBtn = document.getElementById(prevBtnId);
        const nextBtn = document.getElementById(nextBtnId);
        const submitBtn = document.getElementById(submitBtnId);
        const usernameInput = usernameInputId ? document.getElementById(usernameInputId) : null;
        const panels = modal ? modal.querySelectorAll(panelSelector) : [];
        const dots = modal ? modal.querySelectorAll(progressDotSelector) : [];

        if (!modal || !form || !panels.length) return;

        let currentStep = 1;

        function showStep(step) {
            currentStep = Math.max(1, Math.min(totalSteps, step));
            panels.forEach(panel => {
                const panelStep = parseInt(panel.getAttribute('data-wizard-step'), 10);
                panel.classList.toggle('active', panelStep === currentStep);
            });
            dots.forEach(dot => {
                const dotStep = parseInt(dot.getAttribute('data-wizard-step'), 10);
                dot.classList.toggle('active', dotStep === currentStep);
                dot.classList.toggle('done', dotStep < currentStep);
            });
            if (prevBtn) {
                prevBtn.disabled = currentStep === 1;
                prevBtn.classList.toggle('invisible', currentStep === 1);
            }
            if (nextBtn) {
                nextBtn.classList.toggle('d-none', currentStep === totalSteps);
            }
            if (submitBtn) {
                submitBtn.classList.toggle('d-none', currentStep !== totalSteps);
            }
            if (typeof onStepChange === 'function') {
                onStepChange(currentStep);
            }
        }

        function validateStep(step) {
            if (step === 1 && usernameInput) {
                const name = (usernameInput.value || '').trim();
                if (!name) {
                    usernameInput.focus();
                    usernameInput.classList.add('is-invalid');
                    return false;
                }
                usernameInput.classList.remove('is-invalid');
            }
            return true;
        }

        function resetWizard() {
            if (usernameInput) {
                usernameInput.classList.remove('is-invalid');
            }
            showStep(1);
            if (window.guardianRefreshWizardSummary) {
                window.guardianRefreshWizardSummary();
            }
        }

        if (prevBtn) {
            prevBtn.addEventListener('click', () => showStep(currentStep - 1));
        }
        if (nextBtn) {
            nextBtn.addEventListener('click', () => {
                if (!validateStep(currentStep)) return;
                showStep(currentStep + 1);
            });
        }
        if (usernameInput) {
            usernameInput.addEventListener('input', () => {
                usernameInput.classList.remove('is-invalid');
            });
        }

        form.addEventListener('submit', (event) => {
            if (currentStep !== totalSteps) {
                event.preventDefault();
                return;
            }
            if (!validateStep(1)) {
                event.preventDefault();
                showStep(1);
            }
        });

        modal.addEventListener('hidden.bs.modal', resetWizard);
        showStep(1);
    }

    window.GuardianWizard = {
        bindWizard,
        bindCreateStepper,
        updateWizardSummary,
        maturityFromSliderValue,
    };
})();
