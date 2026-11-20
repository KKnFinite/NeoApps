(() => {
    const scope = document.querySelector('.v11-scope');
    if (scope) {
        scope.querySelector('[data-scope-submit]').hidden = true;
        scope.elements.unit_id.addEventListener('change', () => scope.requestSubmit());
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
