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
    if (scroller) {
        const key = 'neostaffing-final-door-view';
        const search = root.querySelector('[data-staffing-search]');
        const status = root.querySelector('[data-staffing-search-status]');
        const people = Array.from(root.querySelectorAll('[data-staffing-person]'));
        const buttons = root.querySelectorAll('[data-staffing-side]');
        let side = 'all', matches = [], matchIndex = -1, saved = {};
        try { saved = JSON.parse(window.sessionStorage.getItem(key) || '{}'); } catch (_) {}
        const applySide = value => {
            side = ['west','east'].includes(value) ? value : 'all';
            buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.staffingSide === side)));
            root.querySelectorAll('[data-final-side]').forEach(cell => { cell.hidden = side !== 'all' && cell.dataset.finalSide !== side; });
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
            if (person.dataset.personSide && side !== 'all' && person.dataset.personSide !== side) applySide('all');
            person.scrollIntoView({block:'center', inline:'center'});
            person.focus({preventScroll:true});
            status.textContent = `${matchIndex % matches.length + 1} of ${matches.length} matches`;
        };
        applySide(saved.side);
        search.value = saved.search || ''; highlight();
        scroller.scrollLeft = Number(saved.left) || 0; scroller.scrollTop = Number(saved.top) || 0;
        saveView = () => {
            try { window.sessionStorage.setItem(key, JSON.stringify({side, search:search.value,
                left:scroller.scrollLeft, top:scroller.scrollTop, page:document.querySelector('[data-phase-editor]') ? saved.page || 0 : window.scrollY})); } catch (_) {}
        };
        buttons.forEach(button => button.addEventListener('click', () => { applySide(button.dataset.staffingSide); scroller.scrollLeft = 0; saveView(); }));
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
