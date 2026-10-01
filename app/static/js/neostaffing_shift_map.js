(() => {
    "use strict";
    const root = document.querySelector("[data-shift-map]");
    if (!root) return;
    const picker = root.querySelector("[data-route-picker]");
    const select = root.querySelector("[data-route-employee]");
    const feedback = root.querySelector("[data-route-feedback]");
    const preview = root.querySelector("[data-route-preview]");
    const setup = root.querySelector("[data-route-setup]");
    const scroller = root.querySelector('[data-staffing-scroll]');
    let saveView = () => {};
    const routeData = root.querySelector('[data-route-grid-data]');
    const gridData = routeData ? JSON.parse(routeData.textContent) : null;
    const svg = root.querySelector('[data-flow-lines]');
    const grid = root.querySelector('[data-route-grid]');
    const body = root.querySelector('[data-route-stage-body]');
    const mobile = window.matchMedia?.('(max-width: 900px)');
    let selectedPerson = null;
    const ns = 'http://www.w3.org/2000/svg';
    const svgNode = (tag, attrs, text) => {
        const node = document.createElementNS(ns, tag);
        for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
        if (text !== undefined) node.textContent = text;
        return node;
    };
    const color = key => `hsl(${(Number(key) * 137.5 + 160) % 360} 60% 68%)`;
    const drawRoutes = () => {
        if (!gridData || !body || !svg) return;
        const headers = [...grid.querySelectorAll('.shift-route-location-head [data-grid-column]')].filter(cell => !cell.hidden);
        grid.style.setProperty('--route-columns', headers.length);
        const visible = new Set(headers.map(cell => cell.dataset.gridColumn));
        const routes = gridData.routes.filter(route => route.key === selectedPerson?.dataset.routeKey || route.points.some(point => visible.has(point.column)));
        const occupancy = {};
        for (const route of routes) route.points.forEach((point, stage) => {
            const column = visible.has(point.column) ? point.column : 'offside';
            const key = `${stage}:${column}`;
            occupancy[key] = (occupancy[key] || 0) + 1;
        });
        const height = Math.max(68, Math.max(0, ...Object.values(occupancy)) * 22 + 16);
        grid.style.setProperty('--route-stage-height', `${height}px`);
        const rect = body.getBoundingClientRect();
        const centers = new Map(headers.map(cell => {
            const box = cell.getBoundingClientRect();
            return [cell.dataset.gridColumn, box.left + box.width / 2 - rect.left];
        }));
        const slots = {};
        svg.setAttribute('viewBox', `0 0 ${rect.width} ${height * gridData.phases.length}`);
        svg.replaceChildren();
        for (const route of routes) {
            const points = route.points.map((point, stage) => {
                const column = visible.has(point.column) ? point.column : 'offside';
                const key = `${stage}:${column}`;
                const slot = slots[key] || 0; slots[key] = slot + 1;
                return {x:centers.get(column), y:stage * height + 16 + slot * 22};
            });
            const group = svgNode('g', {'data-flow-route':route.key, style:`--route-color:${color(route.key)}`});
            let path = `M ${points[0].x} ${points[0].y}`;
            for (let i = 1; i < points.length; i++) {
                const previous = points[i - 1], point = points[i], middle = (previous.y + point.y) / 2;
                path += ` C ${previous.x} ${middle}, ${point.x} ${middle}, ${point.x} ${point.y}`;
            }
            group.append(svgNode('path', {d:path, fill:'none'}));
            points.forEach((point, stage) => {
                const marker = svgNode('g', {class:'shift-route-marker'});
                marker.append(svgNode('title', {}, `Route ${route.key} · ${route.count} employees · ${gridData.phases[stage].label}: ${route.points[stage].label}`));
                marker.append(svgNode('circle', {cx:point.x,cy:point.y,r:9}));
                marker.append(svgNode('text', {x:point.x,y:point.y,'text-anchor':'middle','dominant-baseline':'central'}, route.key));
                group.append(marker);
            });
            svg.append(group);
        }
        updateSelection();
    };
    const updateSelection = () => {
        if (!gridData) return;
        root.classList.toggle('has-route-selection', Boolean(selectedPerson));
        root.querySelectorAll('[data-staffing-person]').forEach(person => {
            const selected = person === selectedPerson;
            person.classList.toggle('is-route-selected', selected);
            person.setAttribute('aria-pressed', String(selected));
            person.style.setProperty('--route-color', color(person.dataset.routeKey));
        });
        svg.querySelectorAll('[data-flow-route]').forEach(route => route.classList.toggle('is-route-selected', route.dataset.flowRoute === selectedPerson?.dataset.routeKey));
        const focus = root.querySelector('[data-route-focus]');
        focus.hidden = !selectedPerson;
        if (!selectedPerson) return;
        const route = gridData.routes.find(route => route.key === selectedPerson.dataset.routeKey);
        const stages = root.querySelector('[data-route-stages]'); stages.replaceChildren();
        route.points.forEach((point, index) => {
            const item = document.createElement('span');
            const label = document.createElement('small'); label.textContent = gridData.phases[index].label;
            const value = document.createElement('strong'); value.textContent = point.label;
            item.append(label, value); stages.append(item);
        });
        const edit = root.querySelector('[data-route-edit]');
        edit.href = selectedPerson.href; edit.textContent = selectedPerson.draggable ? 'EDIT ROUTE' : 'VIEW DETAILS'; edit.hidden = false;
    };
    if (scroller) {
        const key = 'neostaffing-final-door-view';
        const search = root.querySelector('[data-staffing-search]');
        const status = root.querySelector('[data-staffing-search-status]');
        const people = Array.from(root.querySelectorAll('[data-staffing-person]'));
        const buttons = root.querySelectorAll('[data-staffing-side]');
        let side = 'all', matches = [], matchIndex = -1, saved = {};
        try { saved = JSON.parse(window.sessionStorage.getItem(key) || '{}'); } catch (_) {}
        const applySide = value => {
            side = ['west','east'].includes(value) ? value : mobile?.matches ? 'west' : 'all';
            buttons.forEach(button => { button.setAttribute('aria-pressed', String(button.dataset.staffingSide === side)); button.hidden = Boolean(gridData && mobile?.matches && button.dataset.staffingSide === 'all'); });
            root.querySelectorAll('[data-final-side]').forEach(cell => { cell.hidden = side !== 'all' && cell.dataset.finalSide !== 'shared' && cell.dataset.finalSide !== side; });
            root.querySelectorAll('[data-route-offside]').forEach(cell => { cell.hidden = side === 'all'; cell.textContent = cell.closest('.shift-route-location-head') ? (side === 'west' ? 'EAST' : 'WEST') : ''; });
            drawRoutes();
        };
        const highlight = () => {
            const query = search.value.trim().toLocaleLowerCase();
            matches = people.filter(person => query && person.dataset.personSearch.toLocaleLowerCase().includes(query));
            people.forEach(person => person.classList.toggle('is-search-match', matches.includes(person)));
            matchIndex = -1;
            status.textContent = query ? `${matches.length} matches · all doors` : '';
        };
        const nextMatch = () => {
            if (!matches.length) return;
            const person = matches[++matchIndex % matches.length];
            if (person.dataset.personSide && side !== 'all' && person.dataset.personSide !== side) applySide(gridData && mobile?.matches ? person.dataset.personSide : 'all');
            if (gridData) { selectedPerson = person; drawRoutes(); }
            person.scrollIntoView({block:'center', inline:'center'});
            person.focus({preventScroll:true});
            status.textContent = `${matchIndex % matches.length + 1} of ${matches.length} matches`;
        };
        selectedPerson = people.find(person => person.dataset.staffingPerson === saved.person) || null;
        applySide(saved.side);
        search.value = saved.search || ''; highlight();
        scroller.scrollLeft = Number(saved.left) || 0; scroller.scrollTop = Number(saved.top) || 0;
        saveView = () => {
            try { window.sessionStorage.setItem(key, JSON.stringify({side, person:selectedPerson?.dataset.staffingPerson, search:search.value,
                left:scroller.scrollLeft, top:scroller.scrollTop, page:document.querySelector('[data-phase-editor]') ? saved.page || 0 : window.scrollY})); } catch (_) {}
        };
        buttons.forEach(button => button.addEventListener('click', () => { applySide(button.dataset.staffingSide); scroller.scrollLeft = 0; saveView(); }));
        if (gridData) {
            people.forEach(person => {
                person.addEventListener('click', event => {
                    if (event.ctrlKey || event.metaKey || event.shiftKey) return;
                    event.preventDefault(); selectedPerson = selectedPerson === person ? null : person;
                    drawRoutes(); saveView();
                });
                person.addEventListener('keydown', event => {
                    if (event.key === ' ') { event.preventDefault(); person.click(); }
                });
            });
            root.querySelector('[data-route-clear]').addEventListener('click', () => { selectedPerson = null; drawRoutes(); saveView(); });
            let frame;
            window.addEventListener('resize', () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => applySide(side)); });
        }
        search.addEventListener('input', highlight);
        search.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); nextMatch(); } });
        root.querySelector('[data-staffing-next]').addEventListener('click', nextMatch);
        window.addEventListener('pagehide', saveView);
        window.addEventListener('beforeunload', saveView);
        window.addEventListener('load', () => { if (!document.querySelector('.neostaffing-shift-flow-drawer')) window.scrollTo(0, Number(saved.page) || 0); });
    }
    let saving = false;
    const apply = async (target) => {
        if (saving || !select?.value) return;
        saving = true;
        feedback.textContent = "Saving route…";
        try {
            const response = await fetch(root.dataset.routeUrl.replace("/0/", `/${select.value}/`), {
                method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || ""},
                body: JSON.stringify({final_door_id: target.dataset.door, band: target.dataset.band,
                    expected_version: select.selectedOptions[0].dataset.version, complete_route: true,
                    setup_mode: setup.value}),
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.conflict?.message || payload.error || "Route was not saved.");
            // A route can change every phase and Home: render the canonical
            // server projection once after the transaction, never infer a flow.
            saveView();
            window.location.reload();
        } catch (error) { feedback.textContent = error.message; }
        finally { saving = false; }
    };
    root.querySelectorAll("[data-route-person]").forEach(card => {
        card.addEventListener("dragstart", event => {
            select.value = card.dataset.routePerson;
            picker.open = true;
            event.dataTransfer.setData("text/plain", select.value);
            event.dataTransfer.effectAllowed = "move";
        });
    });
    root.querySelectorAll("[data-route-target]").forEach(target => {
        const show = () => { preview.textContent = target.dataset.preview.replace("NO SETUP", `SETUP: ${setup.selectedOptions[0].textContent}`); };
        target.addEventListener("focus", show);
        target.addEventListener("mouseenter", show);
        target.addEventListener("dragover", event => { event.preventDefault(); show(); target.classList.add("is-target"); });
        target.addEventListener("dragleave", () => target.classList.remove("is-target"));
        target.addEventListener("drop", event => { event.preventDefault(); target.classList.remove("is-target"); apply(target); });
        target.addEventListener("click", () => apply(target));
    });
    const editor = document.querySelector('[data-phase-editor]');
    if (editor) {
        const phase = editor.querySelector('[data-phase-choice]');
        const location = editor.querySelector('[data-phase-location]');
        const status = editor.querySelector('[data-phase-feedback]');
        const updateOptions = () => {
            const allowed = phase.value === 'setup' ? ['No Setup', 'Door', 'Ballmat'] : phase.value === 'sort_start' ? ['Door', 'Ballmat', 'Discharge'] : ['Door', 'Ballmat'];
            Array.from(location.options).forEach(option => { option.hidden = option.disabled = !allowed.includes(option.dataset.kind); });
            if (location.selectedOptions[0]?.disabled) location.value = Array.from(location.options).find(option => !option.disabled)?.value || '';
        };
        phase.addEventListener('change', updateOptions); updateOptions();
        editor.querySelector('[data-phase-save]').addEventListener('click', async () => {
            if (saving) return;
            saving = true; status.textContent = 'Saving…';
            try {
                const response = await fetch(editor.dataset.url, {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]')?.content || ''},
                    body:JSON.stringify({phase:phase.value, destination_id:location.value, ballmat_transition:editor.querySelector('[data-phase-transition]').value, expected_version:editor.dataset.version})});
                const payload = await response.json();
                if (!response.ok || !payload.ok) throw new Error(payload.conflict?.message || payload.error || 'Move not saved.');
                saveView();
                window.location.reload();
            } catch (error) { status.textContent = error.message; }
            finally { saving = false; }
        });
    }
})();
