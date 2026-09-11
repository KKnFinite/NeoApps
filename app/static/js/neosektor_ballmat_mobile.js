/* Mobile controls share the page's existing reconciliation/poll controller. */
window.NeoBallmatMobile = {
    create(root, {state, canEdit, send, statusLabels}) {
        const panel = root.querySelector('[data-ballmat-mobile]');
        let current = state;
        let derived = state;
        const queue = [];
        const counts = new Map();
        const drafts = new Map();
        const bays = new Map();
        const backs = new Map();
        let running = false;
        let countInFlight = false;
        const countPending = () => countInFlight || queue.some(task => task.kind === 'count');
        let noticeTimer;
        let sequence = 0;
        const clamp = value => Math.max(0, Math.min(99, value));
        const stepCount = (value, position, delta) => {
            const change = Math.min(Math.max(value[position] + delta, 0), 99 - (value.total - value[position])) - value[position];
            value[position] += change;
            if (position === 'total') value.left = value.total;
            else value.total = value.left + value.right;
        };
        const render = () => {
            const next = derived;
            const detail = current.spotters;
            if (!detail) return;
            panel.dataset.mode = detail.mode;
            panel.querySelectorAll('[data-bm-mode]').forEach(button => {
                button.disabled = !canEdit || !detail.available || running || detail.pending_mode != null
                    || Number(button.dataset.bmMode) === detail.mode;
            });
            const modeStatus = panel.querySelector('[data-bm-mode-status]');
            if (modeStatus) modeStatus.textContent = `${detail.mode} SPOTTER${detail.mode === 2 ? 'S' : ''}`
                + (detail.pending_mode ? ` · REQUEST ${detail.pending_mode} PENDING` : '');
            panel.querySelectorAll('[data-bm-step]').forEach(button => { button.disabled = !canEdit; });
            panel.querySelectorAll('[data-bm-value]').forEach(output => {
                const [key, position] = output.dataset.bmValue.split(':');
                const value = (counts.get(key) || detail.counts[key])[position];
                if (output.matches('[data-bm-input]')) {
                    output.disabled = !canEdit;
                    output.readOnly = detail.mode === 2 ? position === 'total' : position !== 'total';
                    if (!drafts.has(output.dataset.bmValue)) output.value = value;
                } else output.textContent = value;
            });
            const other = current.sides[detail.side === 'east' ? 'west' : 'east'];
            const totals = {first: other.waves[0].count, second: other.waves[1].count, open: other.open_bays};
            panel.querySelectorAll('[data-bm-other]').forEach(e => { e.textContent = totals[e.dataset.bmOther]; });
            ['first', 'second'].forEach((key, i) => {
                panel.querySelector(`[data-bm-arrive="${key}"]`).textContent = current.waves[i].left_to_arrive;
                panel.querySelector(`[data-bm-unload="${key}"]`).textContent = next.waves[i].left;
                panel.querySelector(`[data-bm-route="${key}"]`).textContent = next.ballmat_routing[key];
            });
            current.sides[detail.side].bays.forEach(bay => {
                const input = panel.querySelector(`[data-bm-bay="${bay.bay_name}"]`);
                const status = bays.get(bay.bay_name)?.status ?? bay.status;
                input.value = statusLabels.indexOf(status);
                panel.querySelector(`[data-bm-bay-value="${bay.bay_name}"]`).textContent = status;
                const back = panel.querySelector(`[data-bm-back="${bay.bay_name}"]`);
                if (back) {
                    back.disabled = !canEdit || status !== 'Overflowing';
                    back.checked = status === 'Overflowing' && (backs.get(bay.bay_name)?.enabled ?? bay.back_pickup);
                }
            });
        };
        const apply = next => {
            if (!next.spotters) return;
            const sameSort = next.summary?.sort_date === current.summary?.sort_date
                && next.summary?.sort_name === current.summary?.sort_name;
            // A delayed mutation response must not restore an older mode generation.
            if (sameSort && next.spotters.mode_version < current.spotters.mode_version) return;
            if (!sameSort || next.spotters.mode_version !== current.spotters.mode_version
                    || next.spotters.mode !== current.spotters.mode) {
                // Drop unsent old-generation taps, never reinterpret/retry them.
                for (let i = queue.length - 1; i >= 0; i--) {
                    if (queue[i].kind === 'count' || queue[i].kind === 'request') queue.splice(i, 1);
                }
                counts.clear();
                drafts.clear();
                derived = next;
                const notice = panel.querySelector('[data-bm-notice]');
                if (notice) {
                    notice.textContent = 'MODE CHANGED'; notice.hidden = false;
                    window.clearTimeout(noticeTimer);
                    noticeTimer = window.setTimeout(() => { notice.hidden = true; }, 5200);
                }
            }
            current = next;
            if (!countPending()) derived = next;
            render();
        };
        const drain = async () => {
            if (running) return;
            running = true;
            while (queue.length) {
                const task = queue.shift();
                countInFlight = task.kind === 'count';
                render();
                let succeeded = false;
                try { succeeded = await send(task.payload) !== false; }
                catch (_) { /* The adapter reports/reconciles failures; never retry a write. */ }
                if (task.kind === 'count') {
                    const remaining = queue.filter(item => item.kind === 'count' && item.key === task.key);
                    if (!remaining.length) counts.delete(task.key);
                    else if (!succeeded) {
                        // A failed/uncertain delta is not resent. Rebase only
                        // subsequent deliberate taps on the recovered snapshot.
                        const value = {...current.spotters.counts[task.key]};
                        remaining.forEach(item => {
                            if (item.payload.spotter) stepCount(value, item.position,
                                item.payload.spotter.value === undefined ? item.delta : item.payload.spotter.value - value[item.position]);
                            else value.left = value.total = item.value;
                        });
                        counts.set(task.key, value);
                    }
                }
                if (task.kind === 'bay' && bays.get(task.key)?.sequence === task.sequence) bays.delete(task.key);
                if (task.kind === 'back' && backs.get(task.key)?.sequence === task.sequence) backs.delete(task.key);
                countInFlight = false;
                // Keep only the latest canonical derived snapshot for a burst.
                // No client LTU/routing calculation and no intermediate staircase.
                if (!countPending()) derived = current;
                render();
            }
            running = false;
            render();
        };
        const enqueue = task => {
            // Absolute writes can supersede queued (never in-flight) values.
            // Delta commands are never coalesced: every tap reaches the API.
            if (task.kind === 'bay' || task.kind === 'back' || (task.kind === 'count' && !task.payload.spotter)) {
                const prior = queue.findIndex(item => item.kind === task.kind && item.key === task.key);
                if (prior !== -1) queue.splice(prior, 1);
            }
            queue.push(task);
            render();
            void drain();
        };
        panel.addEventListener('click', event => {
            if (!canEdit) return;
            const mode = event.target.closest('[data-bm-mode]');
            const step = event.target.closest('[data-bm-step]');
            if (mode && !mode.disabled) {
                enqueue({kind: 'request', payload: {mode_request: {expected_mode: current.spotters.mode,
                    expected_mode_version: current.spotters.mode_version, mode: Number(mode.dataset.bmMode)}}});
            }
            if (step && !step.disabled) {
                // Legacy Google-owned 1-spotter operation retains its existing
                // aggregate endpoint. Neo-owned operation always sends deltas.
                const key = step.dataset.metric;
                const delta = Number(step.dataset.bmStep);
                const position = step.dataset.position;
                const value = {...(counts.get(key) || current.spotters.counts[key])};
                let payload;
                if (!current.spotters.available) {
                    value.left = value.total = clamp(value.total + delta);
                    payload = key === 'open' ? {open_bays: value.total} : {waves: {[key]: {count: value.total}}};
                    payload.mode_guard = {expected_mode: current.spotters.mode, expected_mode_version: current.spotters.mode_version};
                } else {
                    stepCount(value, position, delta);
                    payload = {spotter: {expected_mode: current.spotters.mode,
                        expected_mode_version: current.spotters.mode_version, metric: key, position, delta}};
                }
                counts.set(key, value);
                enqueue({kind: 'count', key, position, delta, value: value.total, payload});
            }
        });
        const previewBay = input => {
            const key = input.dataset.bmBay;
            const desired = {status: statusLabels[Number(input.value)], sequence: ++sequence};
            bays.set(key, desired);
            render();
            return {kind: 'bay', key, sequence: desired.sequence, payload: {bay_statuses: {[key]: desired.status}}};
        };
        panel.addEventListener('input', event => {
            const input = event.target;
            if (canEdit && !input.disabled && !input.readOnly && input.matches('[data-bm-input]')) {
                if (!drafts.has(input.dataset.bmValue)) drafts.set(input.dataset.bmValue, {
                    expected_mode: current.spotters.mode, expected_mode_version: current.spotters.mode_version,
                });
            }
            if (canEdit && !event.target.disabled && event.target.matches('[data-bm-bay]')) previewBay(event.target);
        });
        panel.addEventListener('change', event => {
            const input = event.target;
            if (canEdit && !input.disabled && input.matches('[data-bm-back]')) {
                const key = input.dataset.bmBack;
                const desired = {enabled: input.checked, sequence: ++sequence};
                backs.set(key, desired);
                enqueue({kind: 'back', key, sequence: desired.sequence, payload: {back_pickups: {[key]: desired.enabled}}});
            }
            if (canEdit && !input.disabled && !input.readOnly && input.matches('[data-bm-input]')) {
                const guard = drafts.get(input.dataset.bmValue);
                drafts.delete(input.dataset.bmValue);
                // A generation reconciliation clears drafts; never reinterpret one.
                if (!guard || !/^\d+$/.test(input.value)) { render(); return; }
                const [key, position] = input.dataset.bmValue.split(':');
                const absolute = clamp(Number(input.value));
                const value = {...(counts.get(key) || current.spotters.counts[key])};
                let payload;
                if (current.spotters.available) {
                    stepCount(value, position, absolute - value[position]);
                    payload = {spotter: {...guard, metric: key, position, value: absolute}};
                } else {
                    value.left = value.total = absolute;
                    payload = key === 'open' ? {open_bays: absolute} : {waves: {[key]: {count: absolute}}};
                    payload.mode_guard = guard;
                }
                counts.set(key, value);
                enqueue({kind: 'count', key, position, value: value.total, payload});
            }
            if (canEdit && !event.target.disabled && event.target.matches('[data-bm-bay]')) enqueue(previewBay(event.target));
        });
        apply(state);
        return {apply};
    },
};
