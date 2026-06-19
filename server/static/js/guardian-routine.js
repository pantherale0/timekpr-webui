/**
 * Guardian Routine Blueprint — weekly schedule editor interactions.
 */
(function () {
    'use strict';

    function i18n(key, params) {
        return typeof window.guardianI18n === 'function' ? window.guardianI18n(key, params) : key;
    }

    function dayLabel(day) {
        const keys = {
            1: 'routine_day_monday',
            2: 'routine_day_tuesday',
            3: 'routine_day_wednesday',
            4: 'routine_day_thursday',
            5: 'routine_day_friday',
            6: 'routine_day_saturday',
            7: 'routine_day_sunday',
        };
        return i18n(keys[day] || '');
    }

    const SEGMENTS_PER_DAY = 96;
    const MINUTES_PER_SEGMENT = 15;
    const DAY_NUMBERS = [1, 2, 3, 4, 5, 6, 7];
    const WEEKDAY_NUMBERS = [1, 2, 3, 4, 5];
    const WEEKEND_NUMBERS = [6, 7];
    const DAY_KEYS = {
        1: 'monday',
        2: 'tuesday',
        3: 'wednesday',
        4: 'thursday',
        5: 'friday',
        6: 'saturday',
        7: 'sunday',
    };
    const INTERVAL_STEP_MINUTES = 15;
    const INTERVAL_STEP_SECONDS = INTERVAL_STEP_MINUTES * 60;
    const MAX_TIME_VALUE = '23:45';
    const DEFAULT_INTERVAL = Object.freeze({ start_time: '08:30', end_time: '20:30' });

    let intervals = initializeIntervals();
    let paintState = null;
    let bulkTarget = 'weekdays';
    const bulkWindowState = { start: 34, end: 82 };

    function pad(value) {
        return String(value).padStart(2, '0');
    }

    function partsToTimeValue(hour, minute) {
        return `${pad(hour)}:${pad(minute)}`;
    }

    function timeValueToMinutes(value) {
        if (typeof value !== 'string' || !value.includes(':')) {
            return NaN;
        }
        const [hours, minutes] = value.split(':').map(Number);
        if (!Number.isInteger(hours) || !Number.isInteger(minutes)) {
            return NaN;
        }
        return (hours * 60) + minutes;
    }

    function minutesToTimeValue(totalMinutes) {
        const bounded = Math.max(0, Math.min(1439, totalMinutes));
        return partsToTimeValue(Math.floor(bounded / 60), bounded % 60);
    }

    function formatDisplayTime(value) {
        const minutes = timeValueToMinutes(value);
        if (!Number.isFinite(minutes)) {
            return value;
        }
        const hour24 = Math.floor(minutes / 60);
        const minute = minutes % 60;
        const period = hour24 >= 12 ? 'PM' : 'AM';
        const hour12 = hour24 % 12 || 12;
        return `${hour12}:${pad(minute)} ${period}`;
    }

    function initializeIntervals() {
        return DAY_NUMBERS.reduce((map, day) => {
            map[day] = [];
            return map;
        }, {});
    }

    function cloneInterval(interval) {
        return {
            start_time: interval.start_time,
            end_time: interval.end_time,
        };
    }

    function getDayIntervals(day) {
        if (!intervals[day]) {
            intervals[day] = [];
        }
        return intervals[day];
    }

    function mergeIntervals(dayIntervals) {
        if (!dayIntervals.length) {
            return [];
        }

        const ranges = dayIntervals.map((interval) => ({
            start: timeValueToMinutes(interval.start_time),
            end: timeValueToMinutes(interval.end_time),
        })).sort((a, b) => a.start - b.start);

        const merged = [ranges[0]];
        for (let i = 1; i < ranges.length; i += 1) {
            const last = merged[merged.length - 1];
            const current = ranges[i];
            if (current.start <= last.end) {
                last.end = Math.max(last.end, current.end);
            } else {
                merged.push(current);
            }
        }

        return merged.map((range) => ({
            start_time: minutesToTimeValue(range.start),
            end_time: minutesToTimeValue(range.end),
        }));
    }

    function intervalsToSegments(dayIntervals) {
        const segments = new Array(SEGMENTS_PER_DAY).fill(false);
        dayIntervals.forEach((interval) => {
            const start = Math.floor(timeValueToMinutes(interval.start_time) / MINUTES_PER_SEGMENT);
            const end = Math.ceil(timeValueToMinutes(interval.end_time) / MINUTES_PER_SEGMENT);
            for (let i = Math.max(0, start); i < Math.min(SEGMENTS_PER_DAY, end); i += 1) {
                segments[i] = true;
            }
        });
        return segments;
    }

    function segmentEndTime(segmentIndex) {
        if (segmentIndex >= SEGMENTS_PER_DAY) {
            return MAX_TIME_VALUE;
        }
        return minutesToTimeValue(segmentIndex * MINUTES_PER_SEGMENT);
    }

    function segmentsToIntervals(segments) {
        const dayIntervals = [];
        let index = 0;
        while (index < segments.length) {
            if (!segments[index]) {
                index += 1;
                continue;
            }
            const start = index;
            while (index < segments.length && segments[index]) {
                index += 1;
            }
            dayIntervals.push({
                start_time: minutesToTimeValue(start * MINUTES_PER_SEGMENT),
                end_time: segmentEndTime(index),
            });
        }
        return mergeIntervals(dayIntervals);
    }

    function getTargetDayNumbers() {
        if (bulkTarget === 'weekends') {
            return WEEKEND_NUMBERS.slice();
        }
        if (bulkTarget === 'all') {
            return DAY_NUMBERS.slice();
        }
        return WEEKDAY_NUMBERS.slice();
    }

    function updateSummaryMetrics() {
        const dayKeys = Object.values(DAY_KEYS);
        let weekdayTotal = 0;
        let weekendTotal = 0;

        dayKeys.forEach((key, index) => {
            const input = document.getElementById(key);
            if (!input) {
                return;
            }
            const hours = parseFloat(input.value) || 0;
            if (index < 5) {
                weekdayTotal += hours;
            } else {
                weekendTotal += hours;
            }
        });

        const weekTotal = weekdayTotal + weekendTotal;
        const weekdayAvg = weekdayTotal / 5;
        const weekendAvg = weekendTotal / 2;

        const totalEl = document.getElementById('summary-week-total');
        const weekdayEl = document.getElementById('summary-weekday-avg');
        const weekendEl = document.getElementById('summary-weekend-avg');
        if (totalEl) {
            totalEl.textContent = `${formatHours(weekTotal)}${i18n('routine_allowed_suffix')}`;
        }
        if (weekdayEl) {
            weekdayEl.textContent = `${formatHours(weekdayAvg)}${i18n('routine_per_day_suffix')}`;
        }
        if (weekendEl) {
            weekendEl.textContent = `${formatHours(weekendAvg)}${i18n('routine_per_day_suffix')}`;
        }
    }

    function formatHours(value) {
        const rounded = Math.round(value * 100) / 100;
        if (Number.isInteger(rounded)) {
            return `${rounded} Hours`;
        }
        return `${rounded} Hours`;
    }

    function updateBulkHoursDisplay() {
        const slider = document.getElementById('bulk-hours-slider');
        const display = document.getElementById('bulk-hours-display');
        if (!slider || !display) {
            return;
        }
        const hours = (Number(slider.value) || 0) / 2;
        if (hours >= 24) {
            display.textContent = i18n('routine_no_limit');
        } else if (hours === 0) {
            display.textContent = i18n('routine_hard_lock');
        } else {
            display.textContent = `${hours.toFixed(1)}h`;
        }
    }

    function setBulkHoursFromSlider() {
        const slider = document.getElementById('bulk-hours-slider');
        if (!slider) {
            return;
        }
        const hours = (Number(slider.value) || 0) / 2;
        const value = hours >= 24 ? 24 : hours;
        getTargetDayNumbers().forEach((dayNum) => {
            const input = document.getElementById(DAY_KEYS[dayNum]);
            if (input) {
                input.value = String(value);
            }
        });
        updateSummaryMetrics();
    }

    function applyMilestoneHours(hours) {
        const slider = document.getElementById('bulk-hours-slider');
        if (slider) {
            slider.value = String(Math.round(hours * 2));
            updateBulkHoursDisplay();
        }
        document.querySelectorAll('.guardian-milestone-pill').forEach((pill) => {
            pill.classList.toggle('active', Number(pill.dataset.hours) === hours);
        });
        getTargetDayNumbers().forEach((dayNum) => {
            const input = document.getElementById(DAY_KEYS[dayNum]);
            if (input) {
                input.value = String(hours >= 24 ? 24 : hours);
            }
        });
        updateSummaryMetrics();
    }

    function updateDualRangeFill() {
        const fill = document.getElementById('bulk-window-fill');
        const handleStart = document.getElementById('bulk-window-handle-start');
        const handleEnd = document.getElementById('bulk-window-handle-end');
        if (!fill) {
            return;
        }

        let start = bulkWindowState.start;
        let end = bulkWindowState.end;
        if (start > end) {
            [start, end] = [end, start];
        }

        const startPct = (start / SEGMENTS_PER_DAY) * 100;
        const endPct = (end / SEGMENTS_PER_DAY) * 100;
        fill.style.left = `${startPct}%`;
        fill.style.width = `${Math.max(0, endPct - startPct)}%`;

        if (handleStart) {
            handleStart.style.left = `${(bulkWindowState.start / SEGMENTS_PER_DAY) * 100}%`;
        }
        if (handleEnd) {
            handleEnd.style.left = `${(bulkWindowState.end / SEGMENTS_PER_DAY) * 100}%`;
        }

        updateWindowHint(start, end);
    }

    function initDualRangeControl() {
        const track = document.getElementById('bulk-window-track');
        const handleStart = document.getElementById('bulk-window-handle-start');
        const handleEnd = document.getElementById('bulk-window-handle-end');
        if (!track || !handleStart || !handleEnd) {
            return;
        }
        if (track.dataset.rangeBound === '1') {
            updateDualRangeFill();
            return;
        }
        track.dataset.rangeBound = '1';

        let activeHandle = null;
        let captureTarget = null;

        function segmentFromClientX(clientX) {
            const rect = track.getBoundingClientRect();
            const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
            return Math.round(ratio * SEGMENTS_PER_DAY);
        }

        function endDrag() {
            if (captureTarget) {
                captureTarget.removeEventListener('pointermove', onPointerMove);
                captureTarget.removeEventListener('pointerup', endDrag);
                captureTarget.removeEventListener('lostpointercapture', endDrag);
            }
            activeHandle = null;
            captureTarget = null;
        }

        function onPointerMove(event) {
            if (!activeHandle) {
                return;
            }
            const value = segmentFromClientX(event.clientX);
            if (activeHandle === 'start') {
                bulkWindowState.start = value;
            } else {
                bulkWindowState.end = value;
            }
            updateDualRangeFill();
        }

        function beginDrag(handle, event) {
            activeHandle = handle;
            captureTarget = event.currentTarget;
            event.preventDefault();
            captureTarget.setPointerCapture(event.pointerId);
            captureTarget.addEventListener('pointermove', onPointerMove);
            captureTarget.addEventListener('pointerup', endDrag);
            captureTarget.addEventListener('lostpointercapture', endDrag);
            onPointerMove(event);
        }

        handleStart.addEventListener('pointerdown', (event) => beginDrag('start', event));
        handleEnd.addEventListener('pointerdown', (event) => beginDrag('end', event));

        track.addEventListener('pointerdown', (event) => {
            if (event.target === handleStart || event.target === handleEnd) {
                return;
            }
            event.preventDefault();
            const value = segmentFromClientX(event.clientX);
            const distStart = Math.abs(value - bulkWindowState.start);
            const distEnd = Math.abs(value - bulkWindowState.end);
            const handle = distStart <= distEnd ? 'start' : 'end';
            if (handle === 'start') {
                bulkWindowState.start = value;
            } else {
                bulkWindowState.end = value;
            }
            updateDualRangeFill();
            beginDrag(handle, event);
        });

        updateDualRangeFill();
    }

    function updateWindowHint(startSegment, endSegment) {
        const hint = document.getElementById('bulk-window-hint');
        if (!hint) {
            return;
        }

        let start = startSegment;
        let end = endSegment;
        if (start > end) {
            [start, end] = [end, start];
        }

        const startTime = minutesToTimeValue(start * MINUTES_PER_SEGMENT);
        const endTime = minutesToTimeValue(end * MINUTES_PER_SEGMENT);
        hint.innerHTML = i18n('routine_window_hint', {
            start: formatDisplayTime(startTime),
            end: formatDisplayTime(endTime),
        });
    }

    function applyBulkWindow() {
        let start = bulkWindowState.start;
        let end = bulkWindowState.end;
        if (start > end) {
            [start, end] = [end, start];
        }
        if (start === end) {
            showNotification(i18n('routine_choose_window'), 'error');
            return;
        }

        const bulkIntervals = [{
            start_time: minutesToTimeValue(start * MINUTES_PER_SEGMENT),
            end_time: minutesToTimeValue(end * MINUTES_PER_SEGMENT),
        }];

        const validationError = validateDayIntervals(WEEKDAY_NUMBERS[0], bulkIntervals);
        if (validationError) {
            showNotification(validationError, 'error');
            return;
        }

        getTargetDayNumbers().forEach((day) => {
            intervals[day] = bulkIntervals.map(cloneInterval);
            syncTimelineFromIntervals(day);
            renderIntervalEditor(day);
        });
    }

    function inferTimelineMarkers(day, segments) {
        const markers = [];
        const dayKey = DAY_KEYS[day];
        const isWeekend = day >= 6;

        const blockedOvernight = segments.slice(0, 24).every((allowed) => !allowed)
            || segments.slice(84).every((allowed) => !allowed);
        if (blockedOvernight) {
            markers.push(i18n('routine_marker_bedtime'));
        }

        const schoolWindow = segments.slice(32, 60);
        const schoolAllowedRatio = schoolWindow.filter(Boolean).length / schoolWindow.length;
        if (!isWeekend && schoolAllowedRatio >= 0.5) {
            markers.push(i18n('routine_marker_school'));
        } else if (isWeekend && schoolAllowedRatio >= 0.4) {
            markers.push(i18n('routine_marker_family'));
        }

        const eveningBlocked = segments.slice(84, 96).filter((allowed) => !allowed).length;
        if (eveningBlocked >= 8) {
            markers.push(i18n('routine_marker_bedtime_peace'));
        }

        return markers;
    }

    function renderTimelineMarkers(day) {
        const container = document.getElementById(`timeline-markers-${day}`);
        const bar = document.getElementById(`timeline-bar-${day}`);
        if (!container || !bar) {
            return;
        }

        const segments = Array.from(bar.querySelectorAll('.guardian-timeline-segment')).map((segment) => (
            segment.classList.contains('is-allowed')
        ));
        const markers = inferTimelineMarkers(day, segments);
        container.innerHTML = markers.map((label) => (
            `<span class="guardian-timeline-marker">${label}</span>`
        )).join('');
    }

    function getSegmentsFromBar(bar) {
        return Array.from(bar.querySelectorAll('.guardian-timeline-segment')).map((segment) => (
            segment.classList.contains('is-allowed')
        ));
    }

    function segmentIndexFromEvent(bar, event) {
        const rect = bar.getBoundingClientRect();
        if (rect.width <= 0) {
            return 0;
        }
        const ratio = (event.clientX - rect.left) / rect.width;
        const index = Math.floor(ratio * SEGMENTS_PER_DAY);
        return Math.max(0, Math.min(SEGMENTS_PER_DAY - 1, index));
    }

    function applyPaintToBar(bar, day, segmentIndex, mode) {
        const segments = getSegmentsFromBar(bar);
        if (paintState && paintState.bar === bar && paintState.lastIndex != null) {
            const from = Math.min(paintState.lastIndex, segmentIndex);
            const to = Math.max(paintState.lastIndex, segmentIndex);
            for (let index = from; index <= to; index += 1) {
                segments[index] = mode === 'allow';
            }
        } else {
            segments[segmentIndex] = mode === 'allow';
        }
        paintState.lastIndex = segmentIndex;
        bar.querySelectorAll('.guardian-timeline-segment').forEach((segment, index) => {
            segment.classList.toggle('is-allowed', segments[index]);
        });
        intervals[day] = segmentsToIntervals(segments);
    }

    function bindPaintHandlers(bar, day) {
        if (bar.dataset.paintBound === '1') {
            return;
        }
        bar.dataset.paintBound = '1';

        bar.addEventListener('pointerdown', (event) => {
            event.preventDefault();
            const index = segmentIndexFromEvent(bar, event);
            const segments = getSegmentsFromBar(bar);
            const mode = segments[index] ? 'block' : 'allow';
            paintState = { day, mode, bar, lastIndex: index };
            applyPaintToBar(bar, day, index, mode);
            bar.setPointerCapture(event.pointerId);
        });

        bar.addEventListener('pointermove', (event) => {
            if (!paintState || paintState.bar !== bar || paintState.day !== day) {
                return;
            }
            const index = segmentIndexFromEvent(bar, event);
            applyPaintToBar(bar, day, index, paintState.mode);
        });

        const finishPaint = () => {
            if (paintState?.bar !== bar) {
                return;
            }
            renderTimelineMarkers(day);
            renderIntervalEditor(day);
            paintState = null;
        };

        bar.addEventListener('pointerup', finishPaint);
        bar.addEventListener('lostpointercapture', finishPaint);
    }

    function syncTimelineFromIntervals(day) {
        const bar = document.getElementById(`timeline-bar-${day}`);
        if (!bar) {
            return;
        }

        const segments = intervalsToSegments(getDayIntervals(day));

        if (!bar.children.length) {
            segments.forEach((allowed, index) => {
                const segment = document.createElement('div');
                segment.className = `guardian-timeline-segment${allowed ? ' is-allowed' : ''}`;
                segment.dataset.index = String(index);
                segment.title = `${minutesToTimeValue(index * MINUTES_PER_SEGMENT)} – ${minutesToTimeValue((index + 1) * MINUTES_PER_SEGMENT)}`;
                bar.appendChild(segment);
            });
            bindPaintHandlers(bar, day);
        } else {
            bar.querySelectorAll('.guardian-timeline-segment').forEach((segment, index) => {
                segment.classList.toggle('is-allowed', segments[index]);
            });
        }

        renderTimelineMarkers(day);
        updateIntervalSummary(day);
    }

    function updateIntervalSummary(day) {
        const summary = document.getElementById(`interval-summary-${day}`);
        const dayIntervals = getDayIntervals(day);
        if (!summary) {
            return;
        }
        if (!dayIntervals.length) {
            summary.textContent = i18n('routine_unrestricted');
            return;
        }
        if (dayIntervals.length === 1) {
            summary.textContent = `${dayIntervals[0].start_time} – ${dayIntervals[0].end_time}`;
            return;
        }
        summary.textContent = dayIntervals.map((interval) => (
            `${interval.start_time} – ${interval.end_time}`
        )).join(', ');
    }

    function renderIntervalEditor(day) {
        const listEl = document.getElementById(`interval-list-${day}`);
        if (!listEl) {
            return;
        }

        const dayIntervals = getDayIntervals(day);
        listEl.innerHTML = '';

        if (!dayIntervals.length) {
            listEl.innerHTML = `<p class="small text-secondary mb-0">${i18n('routine_no_clock_ranges')}</p>`;
            updateIntervalSummary(day);
            return;
        }

        dayIntervals.forEach((interval, index) => {
            const row = document.createElement('div');
            row.className = 'd-flex align-items-center gap-2 flex-wrap p-2 border rounded bg-body-secondary mb-2';
            row.innerHTML = `
                <span class="small fw-bold text-secondary">#${index + 1}</span>
                <input type="time" class="form-control form-control-sm interval-time-input" style="width: 7rem;" step="${INTERVAL_STEP_SECONDS}" value="${interval.start_time}">
                <span class="small fw-bold text-muted">to</span>
                <input type="time" class="form-control form-control-sm interval-time-input" style="width: 7rem;" step="${INTERVAL_STEP_SECONDS}" value="${interval.end_time}">
                <button type="button" class="btn btn-sm btn-outline-danger ms-auto"><i class="fas fa-trash"></i></button>
            `;

            const [startInput, endInput] = row.querySelectorAll('input');
            startInput.addEventListener('change', () => {
                dayIntervals[index].start_time = startInput.value;
                intervals[day] = mergeIntervals(dayIntervals);
                syncTimelineFromIntervals(day);
                renderIntervalEditor(day);
            });
            endInput.addEventListener('change', () => {
                dayIntervals[index].end_time = endInput.value;
                intervals[day] = mergeIntervals(dayIntervals);
                syncTimelineFromIntervals(day);
                renderIntervalEditor(day);
            });
            row.querySelector('button').addEventListener('click', () => {
                dayIntervals.splice(index, 1);
                intervals[day] = dayIntervals.slice();
                syncTimelineFromIntervals(day);
                renderIntervalEditor(day);
            });
            listEl.appendChild(row);
        });
        updateIntervalSummary(day);
    }

    function renderAllTimelines() {
        DAY_NUMBERS.forEach((day) => {
            syncTimelineFromIntervals(day);
            renderIntervalEditor(day);
        });
    }

    function validateDayIntervals(day, dayIntervals) {
        let previousEnd = null;
        for (const interval of dayIntervals) {
            const startMinutes = timeValueToMinutes(interval.start_time);
            const endMinutes = timeValueToMinutes(interval.end_time);

            if (!Number.isFinite(startMinutes) || !Number.isFinite(endMinutes)) {
                return `Invalid time value for ${dayLabel(day)}.`;
            }
            if ((startMinutes % INTERVAL_STEP_MINUTES) !== 0 || (endMinutes % INTERVAL_STEP_MINUTES) !== 0) {
                return `${dayLabel(day)} intervals must use ${INTERVAL_STEP_MINUTES}-minute increments.`;
            }
            if (startMinutes >= endMinutes) {
                return `${dayLabel(day)} has an interval where the start time is not before the end time.`;
            }
            if (previousEnd !== null && startMinutes < previousEnd) {
                return `${dayLabel(day)} intervals cannot overlap.`;
            }
            previousEnd = endMinutes;
        }
        return null;
    }

    function buildIntervalsPayload() {
        const intervalData = {};
        for (const day of DAY_NUMBERS) {
            const dayIntervals = getDayIntervals(day).map(cloneInterval);
            const validationError = validateDayIntervals(day, dayIntervals);
            if (validationError) {
                throw new Error(validationError);
            }
            intervalData[day] = dayIntervals.map((interval, index) => {
                const startMinutes = timeValueToMinutes(interval.start_time);
                const endMinutes = timeValueToMinutes(interval.end_time);
                return {
                    sort_order: index,
                    start_hour: Math.floor(startMinutes / 60),
                    start_minute: startMinutes % 60,
                    end_hour: Math.floor(endMinutes / 60),
                    end_minute: endMinutes % 60,
                    is_enabled: true,
                };
            });
        }
        return intervalData;
    }

    function normalizeServerIntervals(serverIntervals) {
        const normalized = initializeIntervals();
        for (const day of DAY_NUMBERS) {
            const rawIntervals = serverIntervals?.[day] ?? serverIntervals?.[String(day)] ?? [];
            const intervalList = Array.isArray(rawIntervals) ? rawIntervals : (rawIntervals ? [rawIntervals] : []);
            normalized[day] = intervalList.map((interval) => ({
                start_time: partsToTimeValue(interval.start_hour, interval.start_minute),
                end_time: partsToTimeValue(interval.end_hour, interval.end_minute),
            }));
        }
        return normalized;
    }

    function saveAll() {
        const form = document.getElementById('schedule-form');
        if (!form) {
            return;
        }
        const formData = new FormData(form);

        fetch(form.action, {
            method: 'POST',
            body: formData,
        })
            .then((response) => {
                if (!response.ok) {
                    throw new Error('Failed to save schedule');
                }
                return saveIntervals(false);
            })
            .then(() => {
                showNotification(i18n('routine_save_success'));
                updateSyncStatus();
            })
            .catch((error) => {
                showNotification(`Error saving changes: ${error.message}`, 'error');
            });
    }

    function saveIntervals(showAlert = true) {
        return new Promise((resolve, reject) => {
            let intervalData;
            try {
                intervalData = buildIntervalsPayload();
            } catch (error) {
                if (showAlert) {
                    showNotification(error.message, 'error');
                }
                reject(error);
                return;
            }

            const userId = document.getElementById('routine-user-id')?.value;
            fetch(`/api/user/${userId}/intervals/update`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ intervals: intervalData }),
            })
                .then(async (response) => {
                    const data = await response.json();
                    if (!response.ok || !data.success) {
                        throw new Error(data.message || 'Failed to save intervals');
                    }
                    return loadIntervals();
                })
                .then(() => resolve())
                .catch((error) => {
                    if (showAlert) {
                        showNotification(`Error: ${error.message}`, 'error');
                    }
                    reject(error);
                });
        });
    }

    function loadIntervals() {
        const userId = document.getElementById('routine-user-id')?.value;
        return fetch(`/api/user/${userId}/intervals`)
            .then((response) => response.json())
            .then((data) => {
                if (!data.success) {
                    throw new Error(data.message || 'Failed to load time intervals');
                }
                intervals = normalizeServerIntervals(data.intervals);
                renderAllTimelines();
            });
    }

    function resetForm() {
        if (confirm(i18n('routine_reset_confirm'))) {
            window.location.reload();
        }
    }

    function setRoutineSyncStatus(state, label) {
        const states = ['is-live', 'is-pending', 'is-unknown', 'is-muted'];
        document.querySelectorAll('#routine-sync-dot, #routine-sync-dot-mobile').forEach((dot) => {
            dot.classList.remove(...states);
            dot.classList.add(state);
            dot.title = label;
            dot.setAttribute('aria-label', label);
        });
        const labelEl = document.getElementById('routine-sync-label');
        if (labelEl) {
            labelEl.textContent = label;
        }
    }

    function updateSyncStatus() {
        const userId = document.getElementById('routine-user-id')?.value;
        if (!document.getElementById('routine-sync-label') || !userId) {
            return;
        }

        Promise.all([
            fetch(`/api/schedule-sync-status/${userId}`).then((response) => response.json()),
            fetch(`/api/user/${userId}/intervals/sync-status`).then((response) => response.json()),
        ])
            .then(([scheduleData, intervalData]) => {
                const hasSchedule = Boolean(scheduleData?.schedule);
                const hasIntervals = (intervalData?.total_intervals || 0) > 0;
                const needsSync = (
                    (hasSchedule && scheduleData.success && !scheduleData.is_synced)
                    || (intervalData.success && intervalData.needs_sync)
                );

                if (!hasSchedule && !hasIntervals) {
                    setRoutineSyncStatus('is-muted', i18n('routine_sync_not_configured'));
                    return;
                }

                if (needsSync) {
                    setRoutineSyncStatus('is-pending', i18n('routine_sync_pending'));
                } else {
                    setRoutineSyncStatus('is-live', i18n('routine_sync_online'));
                }
            })
            .catch(() => {
                setRoutineSyncStatus('is-unknown', i18n('routine_sync_unknown'));
            });
    }

    function bindWizardControls(signal) {
        const hoursSlider = document.getElementById('bulk-hours-slider');
        if (hoursSlider) {
            hoursSlider.addEventListener('input', () => {
                updateBulkHoursDisplay();
                setBulkHoursFromSlider();
            }, { signal });
        }

        document.querySelectorAll('.guardian-milestone-pill').forEach((pill) => {
            pill.addEventListener('click', () => {
                applyMilestoneHours(Number(pill.dataset.hours));
            }, { signal });
        });

        document.querySelectorAll('[data-bulk-target]').forEach((button) => {
            button.addEventListener('click', () => {
                bulkTarget = button.dataset.bulkTarget;
                document.querySelectorAll('[data-bulk-target]').forEach((item) => {
                    item.classList.toggle('active', item === button);
                });
            }, { signal });
        });

        initDualRangeControl();

        const applyWindowBtn = document.getElementById('apply-bulk-window');
        if (applyWindowBtn) {
            applyWindowBtn.addEventListener('click', applyBulkWindow, { signal });
        }

        document.querySelectorAll('.day-hours-input').forEach((input) => {
            input.addEventListener('input', updateSummaryMetrics, { signal });
        });

        const saveBtn = document.getElementById('save-routine-btn');
        if (saveBtn) {
            saveBtn.addEventListener('click', saveAll, { signal });
        }

        document.querySelectorAll('.js-reset-routine').forEach((button) => {
            button.addEventListener('click', resetForm, { signal });
        });
    }

    let syncIntervalId = null;
    let routineAbort = null;

    function initRoutineBlueprint() {
        if (routineAbort) {
            routineAbort.abort();
        }
        routineAbort = new AbortController();

        if (syncIntervalId) {
            clearInterval(syncIntervalId);
            syncIntervalId = null;
        }
        bindWizardControls(routineAbort.signal);
        updateBulkHoursDisplay();
        updateSummaryMetrics();
        renderAllTimelines();
        loadIntervals()
            .catch((error) => showNotification(`Error loading intervals: ${error.message}`, 'error'))
            .finally(updateSyncStatus);
        syncIntervalId = setInterval(updateSyncStatus, 15000);
    }

    window.GuardianRoutine = {
        initRoutineBlueprint,
        saveAll,
        resetForm,
    };

    function maybeInitRoutineBlueprint() {
        if (document.getElementById('routine-blueprint-root')) {
            initRoutineBlueprint();
        }
    }

    function teardownRoutineBlueprint() {
        if (routineAbort) {
            routineAbort.abort();
            routineAbort = null;
        }
        if (syncIntervalId) {
            clearInterval(syncIntervalId);
            syncIntervalId = null;
        }
    }

    document.addEventListener('DOMContentLoaded', maybeInitRoutineBlueprint);
    document.addEventListener('guardian:page-ready', maybeInitRoutineBlueprint);
    document.addEventListener('guardian:route', teardownRoutineBlueprint);
})();
