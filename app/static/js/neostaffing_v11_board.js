(() => {
    const board = document.querySelector('[data-workspace-board]');
    if (!board) return;
    const form = board.querySelector('[data-board-transfer]');
    const assignment = board.querySelector('[data-board-assignment]');
    const people = [...board.querySelectorAll('[data-board-person]')];
    const selection = board.querySelector('[data-board-selection]');
    let person;
    let draggedPerson;

    function select(button) {
        person = button;
        for (const candidate of people) candidate.setAttribute('aria-pressed', String(candidate === person));
        const area = button.closest('[data-board-area]').querySelector('h2').textContent;
        selection.textContent = `${button.dataset.label} · ${area}`;
        if (form) {
            form.hidden = false;
            const destination = form.elements.destination;
            destination.value = '';
            for (const option of destination.options) option.disabled = option.value === button.dataset.area;
            form.querySelector('button').textContent = button.dataset.assignmentUrl
                ? 'Preview assignment' : 'Preview permanent transfer';
        }
    }

    for (const button of people) {
        button.addEventListener('click', () => {
            select(button);
            if (form && matchMedia('(max-width: 760px)').matches) form.elements.destination.focus();
        });
        button.addEventListener('dragstart', event => {
            draggedPerson = button;
            event.dataTransfer.setData('text/plain', button.dataset.boardPerson);
            event.dataTransfer.effectAllowed = 'move';
        });
        button.addEventListener('dragend', () => { draggedPerson = null; });
    }
    if (!form) return;
    for (const area of board.querySelectorAll('[data-board-area]')) {
        const canDrop = () => draggedPerson && area.dataset.boardArea && draggedPerson.dataset.area !== area.dataset.boardArea &&
            [...form.elements.destination.options].some(option => option.value === area.dataset.boardArea);
        area.addEventListener('dragover', event => {
            if (canDrop()) event.preventDefault();
        });
        area.addEventListener('drop', event => {
            if (!canDrop()) return;
            event.preventDefault();
            select(draggedPerson);
            form.elements.destination.value = area.dataset.boardArea;
            form.requestSubmit();
        });
    }
    form.addEventListener('submit', event => {
        event.preventDefault();
        if (!person || !form.elements.destination.value) return;
        if (assignment && person.dataset.assignmentUrl) {
            const assignForm = assignment.querySelector('form');
            assignForm.action = person.dataset.assignmentUrl;
            assignForm.elements.work_area_unit_id.value = form.elements.destination.value;
            assignForm.elements.expected_assignment_version.value = person.dataset.assignmentVersion;
            const destination = form.elements.destination.selectedOptions[0].textContent.trim();
            assignment.querySelector('[data-board-assignment-preview]').textContent =
                `Assign ${person.dataset.label} to ${destination}.`;
            assignment.showModal();
            return;
        }
        document.dispatchEvent(new CustomEvent('neostaffing:preview-transfer', { detail: {
            personId: person.dataset.boardPerson,
            sourceId: person.dataset.source,
            destinationId: form.elements.destination.value,
        } }));
    });
    assignment?.querySelector('[data-board-assignment-cancel]').addEventListener('click', () => assignment.close());
})();
