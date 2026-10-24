/* Shared Delay Info entry: MotherBrain authorization remains server-side. */
(() => {
    'use strict';
    const init = () => {
        const root = document.querySelector('[data-neorain-inbound-live], [data-neorain-outbound-live]');
        if (!root || root.dataset.neorainDelayEntryReady) return;
        root.dataset.neorainDelayEntryReady = 'true';
        const selector = '.neorain-delay-line-form, .neorain-delay-delete-form';
        const busy = new Set();
        const value = (form, name) => form.elements.namedItem(name)?.value || '';
        const message = (scope, text, error = false) => {
            let output = scope.querySelector(':scope > [data-neorain-delay-feedback]');
            if (!output) {
                output = document.createElement('output');
                output.className = 'neorain-delay-feedback';
                output.dataset.neorainDelayFeedback = '';
                output.setAttribute('aria-live', 'polite');
                scope.append(output);
            }
            output.textContent = text;
            output.dataset.error = String(error);
        };
        root.addEventListener('input', event => {
            const form = event.target.closest(selector);
            if (form) form.dataset.neorainDelayDirty = 'true';
        });
        root.addEventListener('submit', async event => {
            const form = event.target.closest(selector);
            if (!form || !root.contains(form)) return;
            event.preventDefault();
            const missionId = value(form, 'mission_id');
            if (busy.has(missionId) || !form.reportValidity()) return;
            const scope = form.parentElement.closest('[data-neorain-inbound-edit-protected], [data-neorain-outbound-edit-protected]');
            if (!scope) return;
            const data = new FormData(form);
            const action = value(form, 'action');
            const entry = form.closest('.neorain-delay-existing-row');
            const controls = Array.from((entry || form).querySelectorAll('input:not([type="hidden"]), button'));
            const disabled = controls.map(control => control.disabled);
            busy.add(missionId);
            form.dataset.neorainDelaySaving = 'true';
            form.dataset.neorainDelayDirty = 'true';
            controls.forEach(control => { control.disabled = true; });
            message(scope, 'SAVING…');
            const controller = new AbortController();
            const timeout = window.setTimeout(() => controller.abort(), 20000);
            try {
                // The hidden input named "action" shadows HTMLFormElement.action.
                const response = await fetch(form.getAttribute('action'), {
                    method: 'POST', body: data, credentials: 'same-origin',
                    headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'},
                    signal: controller.signal,
                });
                const result = await response.json().catch(() => null);
                if (!response.ok || !result?.ok) {
                    throw new Error(result?.error || 'Save was not confirmed. Check your session and current delays before retrying. Your entry is kept.');
                }
                // Reconcile only the affected entry, never another unsaved delay.
                const scopes = new Set(Array.from(root.querySelectorAll('.neorain-delay-line-form'))
                    .filter(candidate => value(candidate, 'mission_id') === missionId)
                    .map(candidate => candidate.closest('[data-neorain-inbound-edit-protected], [data-neorain-outbound-edit-protected]')));
                for (const target of scopes) {
                    if (!target) continue;
                    const old = Array.from(target.querySelectorAll('.neorain-delay-existing-row'))
                        .find(row => row.querySelector('[name="delay_id"]')?.value === String(result.delay_id));
                    if (old && old !== entry && old.querySelector('[data-neorain-delay-dirty="true"]')) continue;
                    if (action === 'delete') {
                        old?.remove();
                    } else {
                        const template = document.createElement('template');
                        template.innerHTML = target.closest('[data-neorain-mobile-delay]') ? result.mobile_html : result.html;
                        if (old) old.replaceWith(template.content);
                        else target.querySelector('.neorain-delay-existing-list').append(template.content);
                    }
                }
                if (action === 'add') {
                    for (const name of ['minutes', 'code', 'notes']) form.elements.namedItem(name).value = '';
                }
                form.removeAttribute('data-neorain-delay-dirty');
                for (const toggle of root.querySelectorAll(`[aria-controls="delay-info-${missionId}"]`)) {
                    toggle.textContent = `DELAY INFO (${result.count})`;
                }
                const mobile = root.querySelector(`#mobile-flight-${missionId} [data-neorain-mobile-delay] > summary`);
                if (mobile) mobile.textContent = `DELAY INFO (${result.count})`;
                message(scope, 'DELAY INFO SAVED.');
            } catch (error) {
                // No automatic retry: a lost response can follow a committed ADD.
                message(scope, error.name === 'AbortError'
                    ? 'Save timed out. Your entry is kept. Check current delays in a new tab before retrying.'
                    : (error.message || 'Unable to save. Your entry is kept.'), true);
            } finally {
                window.clearTimeout(timeout);
                busy.delete(missionId);
                form.removeAttribute('data-neorain-delay-saving');
                controls.forEach((control, index) => { control.disabled = disabled[index]; });
            }
        });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true});
    else init();
})();
