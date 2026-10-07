(() => {
    'use strict';
    const root = document.querySelector('[data-shift-roster]');
    if (!root) return;
    const columns = [...root.querySelectorAll('[data-roster-column]')];
    const sides = [...root.querySelectorAll('[data-roster-side]')];
    const mobile = window.matchMedia('(max-width: 700px)');
    const paging = root.querySelector('[data-roster-paging]');
    const previous = root.querySelector('[data-roster-previous]');
    const next = root.querySelector('[data-roster-next]');
    const range = root.querySelector('[data-roster-range]');
    let side = 'all', page = 0;
    try { const stored = JSON.parse(sessionStorage.getItem('staffing-door-roster') || 'null');
        if (stored && ['all', 'west', 'east'].includes(stored.side)) { side = stored.side; page = stored.page || 0; }
    } catch (_) {}
    const render = () => {
        if (mobile.matches && side === 'all') side = 'west';
        const available = columns.slice(0, columns.length / 2).filter(column => side === 'all' || column.dataset.rosterColumnSide === side);
        const size = mobile.matches ? 3 : available.length;
        page = Math.max(0, Math.min(page, Math.ceil(available.length / size) - 1));
        const visible = available.slice(page * size, (page + 1) * size);
        const keys = new Set(visible.map(column => column.dataset.rosterColumn));
        columns.forEach(column => { column.hidden = !keys.has(column.dataset.rosterColumn); });
        const lastPage = (page + 1) * size >= available.length;
        root.dataset.rosterActiveSide = side;
        root.style.setProperty('--roster-columns', String(size));
        sides.forEach(button => { button.hidden = mobile.matches && button.dataset.rosterSide === 'all';
            button.setAttribute('aria-pressed', String(button.dataset.rosterSide === side)); });
        paging.hidden = !mobile.matches;
        previous.disabled = page === 0;
        next.disabled = lastPage;
        const rangeLabels = visible.map(column => column.dataset.doorLabel);
        range.textContent = rangeLabels.join(' · ');
        try { sessionStorage.setItem('staffing-door-roster', JSON.stringify({side, page})); } catch (_) {}
    };
    sides.forEach(button => button.addEventListener('click', () => { side = button.dataset.rosterSide; page = 0; render(); }));
    previous.addEventListener('click', () => { page--; render(); });
    next.addEventListener('click', () => { page++; render(); });
    mobile.addEventListener('change', () => { page = 0; render(); });
    render();

    const feedback = root.querySelector('[data-roster-feedback]');
    const targets = [...root.querySelectorAll('[data-final-door-target]')];
    let dragged = null, saving = false, toastTimer;
    const announce = (message, error = false) => {
        clearTimeout(toastTimer);
        feedback.textContent = message;
        feedback.classList.toggle('is-error', error);
        feedback.hidden = false;
        if (!error) toastTimer = setTimeout(() => { feedback.hidden = true; }, 3500);
    };
    const refreshColumn = column => {
        const people = column.querySelector('[data-roster-people]');
        // Template keys use the same casefolding as the server's roster order.
        const compare = (a, b) => a === b ? 0 : a < b ? -1 : 1;
        const sorted = [...people.children].sort((a, b) =>
            compare(a.dataset.personLast.toLowerCase(), b.dataset.personLast.toLowerCase()) ||
            compare(a.dataset.personFirst.toLowerCase(), b.dataset.personFirst.toLowerCase()) ||
            Number(a.dataset.rosterPerson || a.dataset.ballmatPerson) - Number(b.dataset.rosterPerson || b.dataset.ballmatPerson));
        people.append(...sorted);
        column.querySelector('[data-roster-count]').textContent = String(sorted.length);
        column.querySelector('[data-roster-empty]').hidden = sorted.length > 0;
    };
    const clearTargets = () => targets.forEach(target => target.classList.remove('is-drop-target'));
    root.querySelectorAll('[data-roster-person][draggable="true"]').forEach(card => {
        card.addEventListener('dragstart', event => {
            if (saving) { event.preventDefault(); return; }
            dragged = card;
            event.dataTransfer.setData('application/x-neostaffing-final-door', card.dataset.rosterPerson);
            event.dataTransfer.effectAllowed = 'move';
        });
        card.addEventListener('dragend', () => { dragged = null; clearTargets(); });
    });
    targets.forEach(target => {
        target.addEventListener('dragover', event => {
            if (!dragged || saving) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
            target.classList.add('is-drop-target');
        });
        target.addEventListener('dragleave', event => {
            if (!target.contains(event.relatedTarget)) target.classList.remove('is-drop-target');
        });
        target.addEventListener('drop', async event => {
            if (!dragged || saving) return;
            event.preventDefault();
            const card = dragged, door = target.dataset.finalDoorTarget;
            dragged = null; clearTargets();
            if (card.dataset.finalDoor === door) return;
            const version = card.dataset.flowVersion;
            saving = true; root.setAttribute('aria-busy', 'true');
            try {
                const response = await fetch(root.dataset.finalDoorUrl.replace('/0/', `/${card.dataset.rosterPerson}/`), {
                    method: 'POST', headers: {'Content-Type':'application/json', 'Accept':'application/json',
                        'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]')?.content || ''},
                    body: JSON.stringify({final_door_work_area_id:door, expected_version:version})
                });
                const payload = await response.json();
                if (!response.ok || !payload.ok) throw new Error(payload.conflict?.message || payload.error || 'Final Door was not saved.');
                const source = card.closest('[data-final-door-target]');
                target.querySelector('[data-roster-people]').append(card);
                card.dataset.finalDoor = String(payload.final_door_work_area_id);
                card.dataset.flowVersion = payload.plan_version;
                ['discharge','at-door','wave-1','wave-2','cleanup'].forEach(color => card.classList.toggle(`is-${color}`, payload.flow_color === color));
                const warning = card.querySelector('[data-flow-warning]');
                warning.hidden = !payload.flow_warning;
                warning.title = payload.flow_warning || '';
                warning.setAttribute('aria-label', `Incomplete Shift Flow: ${payload.flow_warning || ''}`);
                refreshColumn(source); refreshColumn(target);
                // Keep the existing read-only Ballmat grouping aligned by Final Door.
                const ballmat = root.querySelector(`[data-ballmat-person="${card.dataset.rosterPerson}"]`);
                if (ballmat) {
                    const from = ballmat.closest('[data-ballmat-door]');
                    const to = root.querySelector(`[data-ballmat-door="${door}"]`);
                    to.querySelector('[data-roster-people]').append(ballmat);
                    refreshColumn(from); refreshColumn(to);
                }
                const editor = document.querySelector('.neostaffing-shift-flow-drawer form');
                if (editor?.querySelector('[name="expected_version"]')?.value === version) {
                    editor.querySelector('[name="expected_version"]').value = payload.plan_version;
                    editor.querySelector('[name="shift_flow_final_door_work_area_id"]').value = String(payload.final_door_work_area_id);
                    const phase = document.querySelector('[data-phase-editor]');
                    if (phase) phase.dataset.version = payload.plan_version;
                }
                announce(`Final Door saved · ${target.dataset.doorLabel}`);
            } catch (error) { announce(error.message, true); }
            finally { saving = false; root.setAttribute('aria-busy', 'false'); }
        });
    });
})();
