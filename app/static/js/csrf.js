/* Shared, session-bound renewal. Never replay an operational mutation. */
(() => {
    'use strict';
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (!meta || window.NeoAppsCsrf) return;
    const unsafe = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
    const originalFetch = window.fetch.bind(window);
    const pendingForms = new WeakSet();
    const ttl = Number(meta.dataset.ttl) || 7200;
    const margin = Math.min(300, ttl * 0.2) * 1000;
    let refreshAt = Number(meta.content.split('.')[0]) * 1000 + ttl * 1000 - margin;
    let renewing = null, timer = null, retryAt = 0, lastError = null, blocked = false;
    let retryDelay = 60000;
    const token = () => meta.content || '';
    const sameOrigin = value => {
        try { return new URL(value, location.href).origin === location.origin; }
        catch (_error) { return false; }
    };
    const methodFor = (form, submitter) => String(
        submitter?.getAttribute('formmethod') || form.getAttribute('method') || 'GET'
    ).toUpperCase();
    const actionFor = (form, submitter) => submitter?.getAttribute('formaction')
        || form.getAttribute('action') || location.href;
    const syncForm = (form, submitter = null) => {
        if (!unsafe.has(methodFor(form, submitter)) || !sameOrigin(actionFor(form, submitter))) return;
        let fields = Array.from(form.elements || form.querySelectorAll('input[name="csrf_token"]'))
            .filter(field => field.name === 'csrf_token');
        if (!fields.length) {
            const field = document.createElement('input');
            field.type = 'hidden'; field.name = 'csrf_token'; form.prepend(field); fields = [field];
        }
        fields.forEach(field => { field.value = token(); });
    };
    const feedback = error => {
        if (!document.body) return;
        let output = document.querySelector('[data-csrf-feedback]');
        if (!output) {
            output = document.createElement('div'); output.dataset.csrfFeedback = '';
            output.className = 'flash flash-error'; output.setAttribute('role', 'alert');
            document.body.prepend(output);
        }
        output.textContent = error.message || String(error);
    };
    const schedule = () => {
        clearTimeout(timer);
        if (!token() || meta.dataset.authenticated !== 'true' || document.hidden || blocked) return;
        timer = setTimeout(() => { void renewIfDue(); }, Math.max(1000, Math.max(refreshAt, retryAt) - Date.now()));
    };
    const ensureFresh = async () => {
        if (!token()) return '';
        if (renewing) return renewing;
        if (Date.now() < refreshAt) return token();
        if (blocked || Date.now() < retryAt) throw lastError;
        renewing = Promise.resolve().then(async () => {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 10000);
            try {
                const response = await originalFetch(meta.dataset.renewUrl, {
                    method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
                    headers: {'Accept': 'application/json', 'X-CSRF-Renewal': '1', 'X-CSRF-Token': token()},
                    signal: controller.signal,
                });
                const payload = await response.json();
                if (!response.ok || typeof payload.csrf_token !== 'string' || !payload.csrf_token
                    || !(Number(payload.expires_in) > 0)) {
                    blocked = response.status === 401 || response.status === 403;
                    throw Object.assign(new Error(payload.error || 'Your form session could not be renewed. Your entry is kept; sign in again if necessary.'), {csrfRenewal: true});
                }
                meta.content = payload.csrf_token;
                window.neoAppsCsrfToken = token();
                const seconds = Number(payload.expires_in);
                refreshAt = Date.now() + (seconds - Math.min(300, seconds * 0.2)) * 1000;
                retryAt = 0; lastError = null; retryDelay = 60000;
                Array.from(document.forms).forEach(form => syncForm(form));
                document.querySelector('[data-csrf-feedback]')?.remove();
                return token();
            } catch (error) {
                lastError = new Error(error.csrfRenewal ? error.message
                    : 'Could not renew your form session. Nothing was submitted; your entry is kept. Check your connection or sign in again.');
                retryAt = Date.now() + retryDelay;
                retryDelay = Math.min(300000, retryDelay * 2);
                throw lastError;
            } finally {
                clearTimeout(timeout); renewing = null; schedule();
            }
        });
        return renewing;
    };
    async function renewIfDue() {
        if (document.hidden || !token() || meta.dataset.authenticated !== 'true') return;
        try { await ensureFresh(); }
        catch (error) { feedback(error); }
        schedule();
    }
    window.neoAppsCsrfToken = token();
    window.NeoAppsCsrf = Object.freeze({ensureFresh, token});
    window.fetch = async (input, init = {}) => {
        const method = String(init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
        const url = input instanceof Request ? input.url : input;
        if (!token() || !unsafe.has(method) || !sameOrigin(url)) return originalFetch(input, init);
        // Resolve before sending anything; concurrent autosaves share renewal.
        try { await ensureFresh(); }
        catch (error) { feedback(error); throw error; }
        const signal = init.signal || (input instanceof Request ? input.signal : null);
        if (signal?.aborted) throw new DOMException('The request was aborted.', 'AbortError');
        const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
        headers.set('X-CSRF-Token', token());
        const response = await originalFetch(input, {...init, headers});
        if (response.status === 400 && response.headers.get('X-NeoApps-CSRF-Rejected') === '1') {
            refreshAt = 0;
            feedback(new Error('This save was rejected before execution. Your entry is kept; try saving again to renew the form session.'));
            // Deliberate retry belongs to the operator; never replay the request.
        }
        return response;
    };
    document.addEventListener('submit', event => {
        const form = event.target instanceof HTMLFormElement ? event.target : null;
        if (!form || !token() || !unsafe.has(methodFor(form, event.submitter))
            || !sameOrigin(actionFor(form, event.submitter))) return;
        if (window.NeoInteraction?.isPending(form)) {
            event.preventDefault(); event.stopImmediatePropagation(); return;
        }
        syncForm(form, event.submitter);
        if (!pendingForms.has(form) && !renewing && Date.now() < refreshAt) return;
        event.preventDefault(); event.stopImmediatePropagation();
        if (pendingForms.has(form)) return;
        pendingForms.add(form);
        const submitter = event.submitter;
        const action = actionFor(form, submitter), method = methodFor(form, submitter);
        const identity = () => JSON.stringify(Array.from(form.elements || form.querySelectorAll('input[type="hidden"]:not([name="csrf_token"])'))
            .filter(field => field.type === 'hidden' && field.name !== 'csrf_token')
            .map(field => [field.name, field.value]));
        const originalIdentity = identity();
        const previousBusy = form.getAttribute('aria-busy');
        const restoreBusy = () => previousBusy === null ? form.removeAttribute('aria-busy') : form.setAttribute('aria-busy', previousBusy);
        form.setAttribute('aria-busy', 'true');
        void ensureFresh().then(() => {
            if (!form.isConnected || actionFor(form, submitter) !== action || methodFor(form, submitter) !== method
                || identity() !== originalIdentity) {
                throw new Error('The form changed while renewing. Nothing was submitted; review your entry before saving.');
            }
            syncForm(form, submitter);
            pendingForms.delete(form); restoreBusy();
            if (submitter && (submitter.form !== form || submitter.disabled)) {
                throw new Error('The selected action changed while renewing. Nothing was submitted; review it before saving.');
            }
            HTMLFormElement.prototype.requestSubmit.call(form, submitter || undefined);
        }).catch(error => {
            pendingForms.delete(form); restoreBusy(); feedback(error);
        });
    }, true);
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) clearTimeout(timer); else void renewIfDue();
    });
    window.addEventListener('focus', () => { void renewIfDue(); });
    window.addEventListener('pageshow', () => { void renewIfDue(); });
    window.addEventListener('online', () => { retryAt = 0; void renewIfDue(); });
    schedule();
})();
