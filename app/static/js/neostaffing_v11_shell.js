(() => {
    const picker = document.querySelector('[data-scope-picker]');
    if (picker) {
        const trigger = picker.querySelector('summary');
        const back = picker.querySelector('[data-scope-back]');
        const search = picker.querySelector('[data-scope-search]');
        const path = picker.querySelector('[data-scope-path]');
        const empty = picker.querySelector('[data-scope-empty]');
        const options = picker.querySelector('.v11-scope-options');
        const levels = new Map(Array.from(options.querySelectorAll('[data-scope-level]'),
            level => [level.dataset.scopeLevel, level]));
        let current = levels.get('');

        function filterChoices() {
            const choices = Array.from(current.querySelectorAll('[data-scope-choice]'));
            const query = search.value.trim().toLowerCase();
            for (const choice of choices) choice.hidden = !choice.textContent.toLowerCase().includes(query);
            empty.hidden = !query || choices.some(choice => !choice.hidden);
        }

        function showLevel(id) {
            current.hidden = true;
            current = levels.get(id);
            current.hidden = false;
            path.textContent = current.dataset.scopeName;
            back.hidden = !id;
            search.value = '';
            options.scrollTop = 0;
            filterChoices();
        }

        picker.addEventListener('toggle', () => {
            if (picker.open) showLevel('');
        });
        picker.addEventListener('click', event => {
            const choice = event.target.closest('[data-scope-open]');
            if (!choice) return;
            showLevel(choice.dataset.scopeOpen);
            back.focus();
        });
        back.addEventListener('click', () => {
            const previous = current.dataset.scopeLevel;
            showLevel(current.dataset.scopeParent);
            current.querySelector(`[data-scope-open="${previous}"]`).focus();
        });
        search.addEventListener('input', filterChoices);
        search.addEventListener('keydown', event => {
            if (event.key === 'Enter') event.preventDefault();
        });
        picker.addEventListener('keydown', event => {
            if (event.key === 'Escape' && picker.open) {
                event.preventDefault();
                picker.open = false;
                trigger.focus();
            }
        });
        document.addEventListener('click', event => {
            if (picker.open && !picker.contains(event.target)) picker.open = false;
        });
    }

    const report = document.querySelector('[data-v11-report-select]');
    report?.addEventListener('change', () => report.form.requestSubmit());
    if (report) report.form.querySelector('button').hidden = true;

    const relationship = document.querySelector('#org-person-inspector form:has(select[name="reports_to_person_id"])');
    if (relationship) {
        const manager = relationship.elements.reports_to_person_id;
        const submit = relationship.querySelector('button[type="submit"]');
        const preview = document.createElement('p');
        preview.setAttribute('role', 'status');
        submit.before(preview);
        let reviewedManager;
        const reset = () => {
            reviewedManager = null;
            preview.hidden = true;
            submit.textContent = 'Review Reports To change';
        };
        manager.addEventListener('change', reset);
        reset();
        relationship.addEventListener('submit', event => {
            if (manager.value === reviewedManager) return;
            event.preventDefault();
            reviewedManager = manager.value;
            preview.textContent = `Reports To will change to ${manager.selectedOptions[0].textContent.trim()}. Work Area assignments stay in place.`;
            preview.hidden = false;
            submit.textContent = 'Confirm Reports To change';
        });
    }
})();
