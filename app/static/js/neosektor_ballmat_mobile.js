/* Mobile controls share the page's existing reconciliation/poll controller. */
window.NeoBallmatMobile = {
    create(root, {state, canEdit, send, statusLabels}) {
        const panel = root.querySelector('[data-ballmat-mobile]');
        let current = state;
        const queue = [];
        const counts = new Map();
        const bays = new Map();
        let running = false;
        let changingMode = false;
        let sequence = 0;
        const clamp = value => Math.max(0, Math.min(99, value));
        const stepCount = (value, position, delta) => {
            const change = Math.min(Math.max(value[position] + delta, 0), 99 - (value.total - value[position])) - value[position];
            value[position] += change;
            if (position === 'total') value.left = value.total;
            else value.total = value.left + value.right;
        };
        const render = () => {
            const next = current;
            const detail = current.spotters;
            if (!detail) return;
            panel.dataset.mode = detail.mode;
            panel.querySelectorAll('[data-bm-mode]').forEach(button => {
                button.setAttribute('aria-pressed', String(Number(button.dataset.bmMode) === detail.mode));
                // Only mode changes need a semantic barrier; ordinary saves
                // never disable count controls, sliders, or the whole panel.
                button.disabled = !canEdit || !detail.available || running || bays.size > 0;
            });
            panel.querySelectorAll('[data-bm-step]').forEach(button => { button.disabled = !canEdit || changingMode; });
            panel.querySelectorAll('[data-bm-value]').forEach(output => {
                const [key, position] = output.dataset.bmValue.split(':');
                output.textContent = (counts.get(key) || detail.counts[key])[position];
            });
            const other = next.sides[detail.side === 'east' ? 'west' : 'east'];
            const totals = {first: other.waves[0].count, second: other.waves[1].count, open: other.open_bays};
            panel.querySelectorAll('[data-bm-other]').forEach(e => { e.textContent = totals[e.dataset.bmOther]; });
            ['first', 'second'].forEach((key, i) => {
                panel.querySelector(`[data-bm-arrive="${key}"]`).textContent = next.waves[i].left_to_arrive;
                panel.querySelector(`[data-bm-unload="${key}"]`).textContent = next.waves[i].left;
                panel.querySelector(`[data-bm-route="${key}"]`).textContent = next.ballmat_routing[key];
            });
            next.sides[detail.side].bays.forEach(bay => {
                const input = panel.querySelector(`[data-bm-bay="${bay.bay_name}"]`);
                const status = bays.get(bay.bay_name)?.status ?? bay.status;
                input.value = statusLabels.indexOf(status);
                panel.querySelector(`[data-bm-bay-value="${bay.bay_name}"]`).textContent = status;
            });
        };
        const apply = next => { current = next; render(); };
        const drain = async () => {
            if (running) return;
            running = true;
            while (queue.length) {
                const task = queue.shift();
                changingMode = task.kind === 'mode';
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
                            if (item.payload.spotter) stepCount(value, item.position, item.delta);
                            else value.left = value.total = item.value;
                        });
                        counts.set(task.key, value);
                    }
                }
                if (task.kind === 'bay' && bays.get(task.key)?.sequence === task.sequence) bays.delete(task.key);
                changingMode = false;
                render();
            }
            running = false;
            render();
        };
        const enqueue = task => {
            // Absolute writes can supersede queued (never in-flight) values.
            // Delta commands are never coalesced: every tap reaches the API.
            if (task.kind === 'bay' || (task.kind === 'count' && !task.payload.spotter)) {
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
            if (mode && !mode.disabled && !running && !bays.size && Number(mode.dataset.bmMode) !== current.spotters.mode) {
                enqueue({kind: 'mode', payload: {spotter: {expected_mode: current.spotters.mode, mode: Number(mode.dataset.bmMode)}}});
            }
            if (step && !step.disabled && !changingMode) {
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
                } else {
                    stepCount(value, position, delta);
                    payload = {spotter: {expected_mode: current.spotters.mode, metric: key, position, delta}};
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
            if (canEdit && !event.target.disabled && event.target.matches('[data-bm-bay]')) previewBay(event.target);
        });
        panel.addEventListener('change', event => {
            if (canEdit && !event.target.disabled && event.target.matches('[data-bm-bay]')) enqueue(previewBay(event.target));
        });
        apply(state);
        return {apply};
    },
};
