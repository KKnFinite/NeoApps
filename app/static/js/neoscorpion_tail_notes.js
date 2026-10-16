(() => {
    'use strict';
    const root = document.querySelector('[data-fuel-dispatch-live]');
    if (!root) return;
    root.addEventListener('click', event => {
        const clear = event.target.closest('[data-tail-note-clear]');
        if (!clear) return;
        const form = clear.closest('form');
        form.elements.notes.value = '';
        form.requestSubmit();
    });
    root.addEventListener('submit', async event => {
        const form = event.target.closest('[data-tail-note-form]');
        if (!form) return;
        event.preventDefault();
        if (form.dataset.saving === 'true') return;
        const input = form.elements.notes;
        const draft = input.value;
        const status = form.querySelector('[data-tail-note-status]');
        form.dataset.saving = 'true';
        form.querySelectorAll('button').forEach(button => { button.disabled = true; });
        status.textContent = 'SAVING…';
        try {
            const response = await fetch(form.action, {method: 'POST', body: new FormData(form),
                credentials: 'same-origin', headers: {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}});
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok !== true) throw new Error(payload.error || 'Unable to save Tail Notes.');
            root.querySelectorAll('[data-tail-note-form]').forEach(other => {
                if (other.elements.tail_number.value !== payload.note.tail) return;
                const field = other.elements.notes;
                // Other mission cards for this aircraft share the note, but keep any independent draft.
                if (other !== form && field.value !== field.defaultValue) return;
                if (other !== form || field.value === draft) field.value = payload.note.notes;
                field.defaultValue = payload.note.notes;
                other.elements.expected_version.value = String(payload.note.version);
                const label = other.closest('[data-tail-notes]').querySelector('[data-tail-note-editor]');
                label.textContent = `${payload.note.editor} · ${payload.note.updated_at}`;
            });
            status.textContent = input.value === input.defaultValue ? 'SAVED' : 'SAVED · new draft unsaved';
            status.classList.remove('is-error');
        } catch (error) {
            status.textContent = error.message || 'Unable to save Tail Notes.';
            status.classList.add('is-error');
        } finally {
            form.dataset.saving = 'false';
            form.querySelectorAll('button').forEach(button => { button.disabled = false; });
            input.dispatchEvent(new Event('change', {bubbles: true}));
        }
    });
})();
