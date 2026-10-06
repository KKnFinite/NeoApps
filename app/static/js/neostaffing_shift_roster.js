(() => {
    const root = document.querySelector('[data-shift-roster]');
    if (!root) return;
    const columns = [...root.querySelectorAll('[data-roster-column]')];
    const discharge = root.querySelector('[data-roster-discharge]');
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
        const showDischarge = !!discharge && (!mobile.matches || (side === 'east' && lastPage));
        if (discharge) discharge.hidden = !showDischarge;
        root.dataset.rosterActiveSide = side;
        root.style.setProperty('--roster-columns', String(size));
        root.style.setProperty('--start-roster-columns', String(Math.max(1, visible.length + (showDischarge ? 1 : 0))));
        sides.forEach(button => { button.hidden = mobile.matches && button.dataset.rosterSide === 'all';
            button.setAttribute('aria-pressed', String(button.dataset.rosterSide === side)); });
        paging.hidden = !mobile.matches;
        previous.disabled = page === 0;
        next.disabled = lastPage;
        const rangeLabels = visible.map(column => column.dataset.doorLabel);
        if (showDischarge && mobile.matches) rangeLabels.push('DISCHARGE');
        range.textContent = rangeLabels.join(' · ');
        try { sessionStorage.setItem('staffing-door-roster', JSON.stringify({side, page})); } catch (_) {}
    };
    sides.forEach(button => button.addEventListener('click', () => { side = button.dataset.rosterSide; page = 0; render(); }));
    previous.addEventListener('click', () => { page--; render(); });
    next.addEventListener('click', () => { page++; render(); });
    mobile.addEventListener('change', () => { page = 0; render(); });
    render();
})();
