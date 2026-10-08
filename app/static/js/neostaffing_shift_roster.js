(() => {
    'use strict';
    const root = document.querySelector('[data-shift-roster]');
    if (!root) return;
    const columns = [...root.querySelectorAll('[data-roster-column]')];
    const doors = columns.slice(0, columns.length / 2);
    const mobile = window.matchMedia('(max-width: 700px)');
    const scroll = root.querySelector('[data-roster-scroll]');
    const paging = root.querySelector('[data-roster-paging]');
    const previous = root.querySelector('[data-roster-previous]');
    const next = root.querySelector('[data-roster-next]');
    const range = root.querySelector('[data-roster-range]');
    let page = 0;
    try { page = Math.max(0, Number(sessionStorage.getItem('staffing-door-page-v2')) || 0); } catch (_) {}
    const render = () => {
        const size = mobile.matches ? (window.innerWidth >= 430 ? 4 : 3) : doors.length;
        page = mobile.matches ? Math.max(0, Math.min(page, Math.ceil(doors.length / size) - 1)) : 0;
        const visible = mobile.matches ? doors.slice(page * size, (page + 1) * size) : doors;
        const keys = new Set(visible.map(column => column.dataset.rosterColumn));
        columns.forEach(column => { column.hidden = !keys.has(column.dataset.rosterColumn); });
        const lastPage = (page + 1) * size >= doors.length;
        root.style.setProperty('--roster-columns', String(size));
        paging.hidden = !mobile.matches;
        previous.disabled = page === 0;
        next.disabled = lastPage;
        range.textContent = visible.length ? `${visible[0].dataset.doorLabel}\u2013${visible.at(-1).dataset.doorLabel}` : '';
        try { sessionStorage.setItem('staffing-door-page-v2', String(page)); } catch (_) {}
    };
    const turn = delta => { const before = page; page += delta; render(); if (page !== before && scroll) scroll.scrollLeft = 0; return page !== before; };
    previous.addEventListener('click', () => turn(-1));
    next.addEventListener('click', () => turn(1));
    mobile.addEventListener('change', () => { page = 0; render(); if (matches.length) showMatch(); });
    window.addEventListener?.('resize', () => { render(); if (matches.length) showMatch(); });
    let touchStart;
    scroll?.addEventListener('touchstart', event => {
        if (!mobile.matches) return;
        const touch = event.touches[0];
        touchStart = touch ? {x: touch.clientX, y: touch.clientY} : null;
    }, {passive:true});
    scroll?.addEventListener('touchend', event => {
        if (!touchStart || !mobile.matches) return;
        const touch = event.changedTouches[0];
        const dx = touch.clientX - touchStart.x, dy = touch.clientY - touchStart.y;
        touchStart = null;
        if (Math.abs(dx) < 42 || Math.abs(dx) < Math.abs(dy) * 1.2 || (dx > 0 && scroll.scrollLeft > 10)) return;
        if (turn(dx < 0 ? 1 : -1)) event.preventDefault();
    }, {passive:false});
    render();

    const search = root.querySelector('[data-roster-search]');
    const searchStatus = root.querySelector('[data-search-status]');
    const searchPrevious = root.querySelector('[data-search-previous]');
    const searchNext = root.querySelector('[data-search-next]');
    const cards = [...root.querySelectorAll('[data-roster-person]')];
    let matches = [], matchIndex = -1;
    const showMatch = () => {
        const card = matches[matchIndex];
        cards.forEach(item => item.classList.toggle('is-search-current', item === card));
        searchStatus.textContent = card ? `${matchIndex + 1} / ${matches.length}` : '';
        searchPrevious.disabled = searchNext.disabled = matches.length < 2;
        if (!card) return;
        const column = card.closest('[data-final-door-target]');
        if (mobile.matches && column) {
            const size = window.innerWidth >= 430 ? 4 : 3;
            page = Math.floor(doors.indexOf(column) / size);
            render();
            if (scroll) scroll.scrollLeft = 0;
        } else if (!column && scroll) {
            scroll.scrollLeft = scroll.scrollWidth;
        }
    };
    const searchRoster = () => {
        const query = search.value.trim().toLocaleLowerCase();
        matches = query ? cards.filter(card => card.dataset.personName.toLocaleLowerCase().includes(query)) : [];
        cards.forEach(card => {
            card.classList.toggle('is-search-match', !!query && matches.includes(card));
            card.classList.toggle('is-search-dimmed', !!query && !matches.includes(card));
        });
        matchIndex = matches.length ? 0 : -1;
        showMatch();
        if (query && !matches.length) searchStatus.textContent = 'No matches';
    };
    search?.addEventListener('input', searchRoster);
    searchPrevious?.addEventListener('click', () => { matchIndex = (matchIndex - 1 + matches.length) % matches.length; showMatch(); });
    searchNext?.addEventListener('click', () => { matchIndex = (matchIndex + 1) % matches.length; showMatch(); });

    const feedback = root.querySelector('[data-roster-feedback]');
    const targets = [...root.querySelectorAll('[data-final-door-target]')];
    const needsPeople = root.querySelector('[data-roster-needs-people]');
    const needsCount = root.querySelector('[data-roster-needs-count]');
    const needsEmpty = root.querySelector('[data-roster-needs-empty]');
    const rosterTotal = root.querySelector('[data-roster-total-count]');
    const plannedCount = document.querySelector('[data-roster-planned-count]');
    const ballmatTotal = root.querySelector('[data-ballmat-total-count]');
    const headerBallmatTotal = document.querySelector('[data-roster-header-ballmat-count]');
    let dragged = null, pendingCard = null, saving = false, toastTimer;
    const draggedClicks = new WeakSet();
    const announce = (message, error = false) => {
        clearTimeout(toastTimer);
        feedback.textContent = message;
        feedback.classList.toggle('is-error', error);
        feedback.hidden = false;
        if (!error) toastTimer = setTimeout(() => { feedback.hidden = true; }, 3500);
    };
    const refreshColumn = column => {
        const people = column.querySelector('[data-roster-people]');
        const colorOrder = {'at-door':0, discharge:1, 'wave-1':2, 'wave-2':3, cleanup:4};
        const colorRank = card => card.dataset.rosterPerson ? (colorOrder[card.dataset.flowColor] ?? 5) : 0;
        // Template keys use the same casefolding as the server's roster order.
        const compare = (a, b) => a === b ? 0 : a < b ? -1 : 1;
        const sorted = [...people.children].sort((a, b) =>
            colorRank(a) - colorRank(b) ||
            compare(a.dataset.personLast.toLowerCase(), b.dataset.personLast.toLowerCase()) ||
            compare(a.dataset.personFirst.toLowerCase(), b.dataset.personFirst.toLowerCase()) ||
            Number(a.dataset.rosterPerson || a.dataset.ballmatPerson) - Number(b.dataset.rosterPerson || b.dataset.ballmatPerson));
        people.append(...sorted);
        column.querySelector('[data-roster-count]').textContent = String(sorted.length);
        column.querySelector('[data-roster-empty]').hidden = sorted.length > 0;
    };
    const adjustCount = (element, delta) => {
        if (element) element.textContent = String(Number(element.textContent) + delta);
    };
    const refreshNeeds = () => {
        if (!needsPeople) return;
        const count = [...needsPeople.children].filter(child => child.dataset.rosterPerson).length;
        needsCount.textContent = String(count);
        needsEmpty.hidden = count > 0;
    };
    const clearTargets = () => targets.forEach(target => target.classList.remove('is-drop-target'));
    root.querySelectorAll('[data-roster-person][draggable="true"]').forEach(card => {
        card.addEventListener('dragstart', event => {
            if (saving) { event.preventDefault(); return; }
            dragged = card;
            draggedClicks.add(card);
            event.dataTransfer.setData('application/x-neostaffing-final-door', card.dataset.rosterPerson);
            event.dataTransfer.effectAllowed = 'move';
        });
        card.addEventListener('dragend', () => { dragged = null; clearTargets(); });
        // A fresh pointer press is an intentional click; the click generated
        // by the drag's release must never navigate to the assignment editor.
        card.addEventListener('pointerdown', () => { draggedClicks.delete(card); });
        card.addEventListener('keydown', event => {
            if (event.key === 'Enter') draggedClicks.delete(card);
        });
        card.addEventListener('click', event => {
            if (draggedClicks.has(card) || pendingCard === card) {
                event.preventDefault(); event.stopPropagation();
                draggedClicks.delete(card);
            }
        });
    });
    // Native drag and the touch-friendly Needs Assignment picker use one save path.
    const moveToDoor = async (card, target) => {
        if (!card || saving) return;
        const door = target.dataset.finalDoorTarget;
        if (card.dataset.finalDoor === door) return;
        const version = card.dataset.flowVersion;
        const source = card.closest('[data-final-door-target]') || card.closest('[data-roster-needs-people]');
        if (!source) return;
        const fromNeeds = source === needsPeople;
        const reason = fromNeeds && root.querySelector(`[data-roster-needs-reason="${card.dataset.rosterPerson}"]`);
        const assignSelect = fromNeeds && root.querySelector(`[data-needs-door-select="${card.dataset.rosterPerson}"]`);
        const directAssignment = !!(assignSelect && assignSelect.value === door);
        const ballmat = root.querySelector(`[data-ballmat-person="${card.dataset.rosterPerson}"]`);
        const ballmatSource = ballmat?.closest('[data-ballmat-door]');
        const ballmatTarget = (ballmat || (fromNeeds && card.dataset.ballmatStart === 'true')) && root.querySelector(`[data-ballmat-door="${door}"]`);
        const newBallmat = fromNeeds && !ballmat && ballmatTarget ? document.createElement('span') : null;
        if (newBallmat) {
            newBallmat.className = 'shift-ballmat-person';
            newBallmat.dataset.ballmatPerson = card.dataset.rosterPerson;
            newBallmat.dataset.personLast = card.dataset.personLast;
            newBallmat.dataset.personFirst = card.dataset.personFirst;
            newBallmat.textContent = card.dataset.personName;
        }
        const marker = ballmat || newBallmat;
        // Retain exact positions as well as field state until accepted.
        const nextCard = card.nextSibling, nextBallmat = ballmat?.nextSibling;
        if (reason) reason.hidden = true;
        if (assignSelect) assignSelect.hidden = true;
        target.querySelector('[data-roster-people]').append(card);
        if (fromNeeds) {
            refreshNeeds(); adjustCount(rosterTotal, 1); adjustCount(plannedCount, 1);
        } else refreshColumn(source);
        refreshColumn(target);
        if (ballmatTarget) {
            ballmatTarget.querySelector('[data-roster-people]').append(marker);
            if (ballmatSource) refreshColumn(ballmatSource);
            refreshColumn(ballmatTarget);
            if (newBallmat) { adjustCount(ballmatTotal, 1); adjustCount(headerBallmatTotal, 1); }
        }
        saving = true; pendingCard = card; root.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(root.dataset.finalDoorUrl.replace('/0/', `/${card.dataset.rosterPerson}/`), {
                method: 'POST', headers: {'Content-Type':'application/json', 'Accept':'application/json',
                    'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]')?.content || ''},
                body: JSON.stringify({final_door_work_area_id:door, expected_version:version})
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) throw new Error(payload.conflict?.message || payload.error || 'Final Door was not saved.');
            card.dataset.finalDoor = String(payload.final_door_work_area_id);
            card.dataset.flowVersion = payload.plan_version;
            card.dataset.flowColor = payload.flow_color || '';
            ['discharge','at-door','wave-1','wave-2','cleanup'].forEach(color => card.classList.toggle(`is-${color}`, payload.flow_color === color));
            const setupStrip = card.querySelector('[data-setup-strip]');
            if (setupStrip) setupStrip.hidden = !payload.has_setup;
            const warning = card.querySelector('[data-flow-warning]');
            warning.hidden = !payload.flow_warning;
            warning.title = payload.flow_warning || '';
            warning.setAttribute('aria-label', `Incomplete Shift Flow: ${payload.flow_warning || ''}`);
            refreshColumn(target);
            if (reason) reason.remove();
            if (assignSelect) assignSelect.remove();
            if (fromNeeds || payload.previous_ballmat_side !== payload.ballmat_side) {
                for (const [side, delta] of [[fromNeeds ? null : payload.previous_ballmat_side, -1], [payload.ballmat_side, 1]]) {
                    const count = side && root.querySelector(`[data-ballmat-side-count="${side}"]`);
                    if (count) count.textContent = String(Number(count.textContent) + delta);
                }
            }
            const editor = document.querySelector('.neostaffing-shift-flow-drawer form');
            if (editor?.querySelector('[name="expected_version"]')?.value === version) {
                editor.querySelector('[name="expected_version"]').value = payload.plan_version;
                const changes = payload.editor_changes || {shift_flow_final_door_work_area_id:payload.final_door_work_area_id};
                Object.entries(changes).forEach(([name, value]) => {
                    const field = editor.querySelector(`[name="${name}"]`);
                    if (field) field.value = value == null ? '' : String(value);
                });
                const phase = document.querySelector('[data-phase-editor]');
                if (phase) phase.dataset.version = payload.plan_version;
            }
            if (matches.length) showMatch();
            if (directAssignment) {
                if (mobile.matches) {
                    const size = window.innerWidth >= 430 ? 4 : 3;
                    page = Math.floor(doors.indexOf(target) / size);
                    render();
                    if (scroll) scroll.scrollLeft = 0;
                } else if (scroll?.getBoundingClientRect && target.getBoundingClientRect) {
                    scroll.scrollLeft += target.getBoundingClientRect().left - scroll.getBoundingClientRect().left - 8;
                }
            }
            announce(`Final Door saved · ${target.dataset.doorLabel}`);
        } catch (error) {
            (fromNeeds ? source : source.querySelector('[data-roster-people]')).insertBefore(card, nextCard);
            if (reason) reason.hidden = false;
            if (assignSelect) { assignSelect.hidden = false; assignSelect.value = ''; }
            if (fromNeeds) {
                refreshNeeds(); adjustCount(rosterTotal, -1); adjustCount(plannedCount, -1);
            } else refreshColumn(source);
            refreshColumn(target);
            if (ballmatTarget) {
                if (newBallmat) {
                    newBallmat.remove(); adjustCount(ballmatTotal, -1); adjustCount(headerBallmatTotal, -1);
                } else if (ballmatSource) {
                    ballmatSource.querySelector('[data-roster-people]').insertBefore(ballmat, nextBallmat);
                    refreshColumn(ballmatSource);
                }
                refreshColumn(ballmatTarget);
            }
            if (matches.length) showMatch();
            announce(error.message, true);
        }
        finally { saving = false; pendingCard = null; root.setAttribute('aria-busy', 'false'); }
    };
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
        target.addEventListener('drop', event => {
            if (!dragged || saving) return;
            event.preventDefault();
            const card = dragged;
            dragged = null;
            clearTargets();
            void moveToDoor(card, target);
        });
    });
    root.querySelectorAll('[data-needs-door-select]').forEach(select => {
        select.addEventListener('change', () => {
            const card = cards.find(item => item.dataset.rosterPerson === select.dataset.needsDoorSelect
                && item.closest('[data-roster-needs-people]') === needsPeople);
            const target = targets.find(item => item.dataset.finalDoorTarget === select.value);
            if (!card || !target || saving) { select.value = ''; return; }
            void moveToDoor(card, target);
        });
    });
})();
