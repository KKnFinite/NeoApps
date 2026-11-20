(() => {
    const board = document.querySelector('[data-workspace-board]');
    if (!board) return;
    const scroller = board.querySelector('[data-board-scroll]');
    const columns = [...board.querySelectorAll('[data-board-area]')];
    const navigation = document.querySelector('[data-board-navigation]');
    const dialog = board.querySelector('[data-board-dialog]');
    const form = dialog?.querySelector('[data-board-transfer]');
    const destination = form?.elements.destination_work_area_id;
    const feedback = form?.querySelector('[data-board-error]');
    let person;
    let dragged;
    let dropTarget;
    let saving = false;

    if (scroller && navigation) {
        const buttons = [...navigation.querySelectorAll('button')];
        const syncNavigation = () => {
            const end = scroller.scrollWidth - scroller.clientWidth;
            navigation.hidden = end < 2;
            buttons[0].disabled = scroller.scrollLeft < 2;
            buttons[1].disabled = scroller.scrollLeft >= end - 1;
        };
        for (const button of buttons) {
            button.addEventListener('click', () => {
                const step = columns[1].offsetLeft - columns[0].offsetLeft;
                const direction = Number(button.dataset.boardScrollDirection);
                scroller.scrollBy({left: direction * step, behavior: 'instant'});
                syncNavigation();
            });
        }
        scroller.addEventListener('scroll', syncNavigation, {passive: true});
        new ResizeObserver(syncNavigation).observe(scroller);
        const saved = history.state?.staffingBoardScroll;
        if (saved?.scope === board.dataset.scopeStamp) {
            scroller.scrollTo(saved.x, saved.y);
            history.replaceState({...history.state, staffingBoardScroll: null}, '');
        }
        syncNavigation();
    }
    if (!dialog) return;

    function open(button, target = '') {
        person = button;
        dialog.querySelector('[data-board-name]').textContent = button.dataset.label;
        const column = button.closest('[data-board-area]');
        dialog.querySelector('[data-board-source]').textContent = column
            ? column.querySelector('header').title : 'Needs Assignment';
        if (form) {
            for (const option of destination.options) option.disabled = option.value === button.dataset.area;
            destination.value = target;
            feedback.hidden = true;
            form.querySelector('[data-board-save]').textContent = button.dataset.source
                ? 'Confirm Transfer' : 'Confirm Assignment';
        }
        dialog.showModal();
    }

    board.addEventListener('click', event => {
        const button = event.target.closest('[data-board-person]');
        if (button) open(button);
    });
    dialog.querySelector('[data-board-cancel]').addEventListener('click', () => dialog.close());
    dialog.querySelector('[data-board-edit]').addEventListener('click', () => {
        dialog.close();
        document.dispatchEvent(new CustomEvent('neostaffing:edit-employee', {
            detail: {personId: person.dataset.boardPerson},
        }));
    });
    dialog.addEventListener('cancel', event => {
        if (saving) event.preventDefault();
    });
    if (!form) return;

    function highlight(target) {
        if (dropTarget === target) return;
        dropTarget?.removeAttribute('data-drop-active');
        dropTarget = target;
        dropTarget?.setAttribute('data-drop-active', '');
    }

    board.addEventListener('dragstart', event => {
        const button = event.target.closest('[data-board-person][draggable="true"]');
        if (!button) return;
        dragged = button;
        event.dataTransfer.setData('text/plain', button.dataset.boardPerson);
        event.dataTransfer.effectAllowed = 'move';
    });
    board.addEventListener('dragend', () => {
        dragged = null;
        highlight(null);
    });
    scroller.addEventListener('dragover', event => {
        const area = event.target.closest('[data-board-area]');
        if (!dragged || !area || area.dataset.boardArea === dragged.dataset.area) {
            highlight(null);
            return;
        }
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        highlight(area);
    });
    scroller.addEventListener('dragleave', event => {
        if (!scroller.contains(event.relatedTarget)) highlight(null);
    });
    scroller.addEventListener('drop', event => {
        const area = event.target.closest('[data-board-area]');
        if (!dragged || !area || area.dataset.boardArea === dragged.dataset.area) return;
        event.preventDefault();
        open(dragged, area.dataset.boardArea);
        highlight(null);
    });

    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (saving || !person || !destination.value) return;
        const data = new FormData(form);
        data.set('source_assignment_id', person.dataset.source);
        data.set('expected_person_version', person.dataset.personVersion);
        data.set('expected_assignment_version', person.dataset.assignmentVersion);
        data.set('scope_stamp', board.dataset.scopeStamp);
        data.set('confirm_transfer', 'yes');
        saving = true;
        feedback.hidden = true;
        form.setAttribute('aria-busy', 'true');
        const controls = [...form.querySelectorAll('button, select')];
        controls.forEach(control => { control.disabled = true; });
        try {
            const response = await fetch(person.dataset.placementUrl, {method: 'POST', body: data});
            const payload = await response.json().catch(() => null);
            if (!response.ok || payload?.ok !== true) {
                throw new Error(payload?.error || 'Unable to verify the move. Reload the board before trying again.');
            }
            history.replaceState({...history.state, staffingBoardScroll: {
                x: scroller.scrollLeft, y: scroller.scrollTop, scope: board.dataset.scopeStamp,
            }}, '');
            location.reload();
        } catch (error) {
            feedback.textContent = error.message;
            feedback.hidden = false;
        } finally {
            saving = false;
            form.removeAttribute('aria-busy');
            controls.forEach(control => { control.disabled = false; });
        }
    });
})();
