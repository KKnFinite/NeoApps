/* Preserve failed POST drafts without refreshing/rebasing their signed baseline. */
(() => {
    'use strict';
    const forms = Array.from(document.querySelectorAll('[data-admin-form]'));
    const source = document.querySelector('[data-admin-form-draft]');
    if (source) {
        try {
            const draft = JSON.parse(source.textContent);
            const form = forms.find(item => item.dataset.adminForm === draft.scope);
            if (form) {
                Array.from(form.elements).forEach(control => {
                    if (!control.name || control.name === 'csrf_token' || control.type === 'submit') return;
                    if (control.type === 'checkbox') control.checked = draft.values[control.name] === control.value;
                    else if (Object.hasOwn(draft.values, control.name)) control.value = draft.values[control.name];
                });
                // SPEAR's draggable order must agree with its restored hidden input.
                const list = form.querySelector('[data-spear-priority-list]');
                (draft.values.priority_order || '').split(',').forEach(key => {
                    const item = Array.from(list?.children || []).find(el => el.dataset.priorityKey === key);
                    if (item) list.appendChild(item);
                });
            }
        } catch (_) { /* The server-rendered unsaved-entries disclosure remains. */ }
    }
    window.addEventListener('pageshow', () => {
        // The shared shell restores the old submit scroll after navigation.
        // Failed admin submissions must expose feedback, not the old Save
        // position below it. Leave successful-page scroll behavior unchanged.
        if (source) window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
            const error = document.querySelector('[data-admin-form-error]');
            error?.focus();
            error?.scrollIntoView({block:'start'});
        }));
    });
})();
