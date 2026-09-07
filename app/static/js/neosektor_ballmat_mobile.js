/* Mobile controls share the page's existing reconciliation/poll controller. */
window.NeoBallmatMobile = {
    create(root, {state, canEdit, send, statusLabels}) {
        const panel = root.querySelector('[data-ballmat-mobile]');
        let current = state;
        let pending = false;
        const apply = (next) => {
            current = next;
            const detail = next.spotters;
            if (!detail) return;
            panel.dataset.mode = detail.mode;
            panel.querySelectorAll('[data-bm-mode]').forEach(button => {
                button.setAttribute('aria-pressed', String(Number(button.dataset.bmMode) === detail.mode));
            });
            panel.querySelectorAll('[data-bm-value]').forEach(output => {
                const [key, position] = output.dataset.bmValue.split(':');
                output.textContent = detail.counts[key][position];
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
                if (document.activeElement !== input) input.value = statusLabels.indexOf(bay.status);
                panel.querySelector(`[data-bm-bay-value="${bay.bay_name}"]`).textContent = bay.status;
            });
        };
        const submit = async payload => {
            if (!canEdit || pending) return;
            pending = true;
            panel.setAttribute('aria-busy', 'true');
            const controls = [...panel.querySelectorAll('button,input')];
            const disabled = controls.map(e => e.disabled);
            controls.forEach(e => { e.disabled = true; });
            try { await send(payload); }
            finally {
                pending = false;
                panel.removeAttribute('aria-busy');
                controls.forEach((e, i) => { e.disabled = disabled[i]; });
            }
        };
        panel.addEventListener('click', event => {
            const mode = event.target.closest('[data-bm-mode]');
            const step = event.target.closest('[data-bm-step]');
            if (mode) submit({spotter: {expected_mode: current.spotters.mode, mode: Number(mode.dataset.bmMode)}});
            if (step) {
                // Legacy Google-owned 1-spotter operation retains its existing
                // aggregate endpoint. Neo-owned operation always sends deltas.
                const key = step.dataset.metric;
                const delta = Number(step.dataset.bmStep);
                if (!current.spotters.available) {
                    const value = Math.max(0, Math.min(99, current.spotters.counts[key].total + delta));
                    submit(key === 'open' ? {open_bays: value} : {waves: {[key]: {count: value}}});
                } else submit({spotter: {expected_mode: current.spotters.mode,
                    metric: key, position: step.dataset.position, delta}});
            }
        });
        panel.addEventListener('change', event => {
            if (event.target.matches('[data-bm-bay]')) {
                submit({bay_statuses: {[event.target.dataset.bmBay]: statusLabels[Number(event.target.value)]}});
            }
        });
        apply(state);
        return {apply};
    },
};
