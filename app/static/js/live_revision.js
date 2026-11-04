/* Ephemeral, same-session revision hints only. Never transports HTML or actions. */
(() => {
    "use strict";
    const scope = document.querySelector('meta[name="live-poll-scope"]')?.content;
    const entries = new Map();
    const fields = ['ok', 'current_operation', 'operation_id', 'revision', 'tail_revision', 'tail_notes_revision', 'can_edit'];
    const primitive = value => value === null || typeof value === 'boolean'
        || (typeof value === 'number' && Number.isFinite(value))
        || (typeof value === 'string' && value.length <= 160);
    let channel = null;
    let freshResume = true;
    try { if (scope && window.BroadcastChannel) channel = new BroadcastChannel(`neo-revision-v1:${scope}`); } catch (_) { /* independent polling */ }
    const entryFor = key => {
        if (!entries.has(key)) {
            if (entries.size >= 16) entries.delete(entries.keys().next().value);
            entries.set(key, {at: 0, until: 0, value: null});
        }
        return entries.get(key);
    };
    const send = message => { try { channel?.postMessage(message); } catch (_) { /* local fallback */ } };
    if (channel) channel.onmessage = ({data}) => {
        if (!data || typeof data.key !== 'string' || data.key.length > 1000) return;
        const entry = entryFor(data.key);
        if (data.type === 'request') {
            if (entry.until > Date.now()) send({type: 'cooldown', key: data.key, until: entry.until});
            if (entry.value && Date.now() - entry.at < 60000) send({type: 'revision', key: data.key, at: entry.at, value: entry.value});
        } else if (data.type === 'cooldown' && Number.isFinite(data.until)) {
            entry.until = Math.max(entry.until, data.until);
        } else if (data.type === 'revision' && Number.isFinite(data.at)
                && data.at >= entry.at && data.at <= Date.now() + 1000 && Date.now() - data.at < 60000
                && data.value && Object.keys(data.value).every(key => fields.includes(key) && primitive(data.value[key]))
                && (!data.key.includes('/fuel-dispatch/revision') || typeof data.value.can_edit === 'boolean')
                && (!entry.value || entry.value.operation_id !== data.value.operation_id
                    || Number(data.value.revision) >= Number(entry.value.revision))) {
            entry.at = data.at;
            entry.value = data.value;
        }
    };
    const throttled = until => Object.assign(new Error('Live refresh cooling down; retrying automatically.'), {retryAt: until, status: 429});
    const cooldownKey = target => `cooldown:${target.origin}${target.pathname}`;
    const verifyScope = response => {
        if (response.redirected) {
            response.body?.cancel().catch(() => {});
            throw Object.assign(new Error('Sign-in changed. Preserve your drafts and reopen this screen.'), {sessionChanged: true});
        }
        const current = response.headers.get('X-Neo-Live-Scope');
        if (scope && current && current !== scope) throw Object.assign(
            new Error('Sign-in context changed. Preserve your drafts and reopen this screen.'), {sessionChanged: true});
        return current;
    };
    const retryAfter = response => {
        const raw = response.headers.get('Retry-After');
        const seconds = raw === null || raw.trim() === '' ? NaN : Number(raw);
        const date = Number.isFinite(seconds) ? Date.now() + Math.max(0, seconds) * 1000 : Date.parse(raw);
        return Number.isFinite(date) ? Math.max(Date.now() + 1000, date) : Date.now() + 5000;
    };
    const fetchLive = async (url, options = {}) => {
        if (String(options.method || 'GET').toUpperCase() !== 'GET') return fetch(url, options);
        const key = cooldownKey(new URL(url, window.location.href));
        const entry = entryFor(key);
        if (entry.until > Date.now()) throw throttled(entry.until);
        const response = await fetch(url, options);
        if (response.status === 429) {
            entry.until = retryAfter(response);
            send({type: 'cooldown', key, until: entry.until});
            response.body?.cancel().catch(() => {});
            throw throttled(entry.until);
        }
        return response;
    };
    const fetchRevision = async (url, options = {}, intervalMs = 5000, operationId = "none") => {
        const target = new URL(url, window.location.href);
        if (!channel || target.origin !== window.location.origin) {
            const abort = new AbortController();
            const timer = window.setTimeout(() => abort.abort(), 10000);
            try {
                const response = await fetchLive(url, {...options, signal: abort.signal});
                if (target.origin === window.location.origin) verifyScope(response);
                await response.clone().json().catch(() => null);
                return response;
            } finally { window.clearTimeout(timer); }
        }
        const key = `${target.href}#operation=${encodeURIComponent(operationId)}`;
        const entry = entryFor(key);
        const maxAge = Math.min(freshResume ? 4900 : 60000, Math.max(0, Number(intervalMs) - 100));
        freshResume = false;
        send({type: 'request', key});
        const read = async () => {
            const started = Date.now();
            const until = Math.max(entry.until, entryFor(cooldownKey(target)).until);
            if (until > Date.now()) throw throttled(until);
            if (entry.value && Date.now() >= entry.at && Date.now() - entry.at < maxAge) {
                return new Response(JSON.stringify(entry.value), {headers: {'Content-Type': 'application/json'}});
            }
            const abort = new AbortController();
            const timer = window.setTimeout(() => abort.abort(), 10000);
            try {
                const response = await fetchLive(url, {...options, signal: abort.signal});
                const currentScope = verifyScope(response);
                // A login redirect/403/error is never a shared successful fingerprint.
                if (response.ok && !response.redirected && currentScope === scope) {
                    const value = await response.clone().json().catch(() => null);
                    if (value && Object.prototype.hasOwnProperty.call(value, 'revision') && value.ok !== false) {
                        const safe = Object.fromEntries(fields.filter(k => Object.prototype.hasOwnProperty.call(value, k)).map(k => [k, value[k]]));
                        if (Object.values(safe).every(primitive)) {
                            entry.value = safe;
                            entry.at = started;
                            send({type: 'revision', key, at: entry.at, value: entry.value});
                        }
                    }
                } else { entry.value = null; }
                return response;
            } finally { window.clearTimeout(timer); }
        };
        if (navigator.locks?.request) {
            const abort = new AbortController();
            const timer = window.setTimeout(() => abort.abort(), 2000);
            let entered = false;
            try {
                return await navigator.locks.request(`neo-revision:${scope}:${key}`, {signal: abort.signal}, async () => {
                    entered = true;
                    window.clearTimeout(timer);
                    // Broadcast delivery is queued separately from Web Lock release.
                    await new Promise(resolve => window.setTimeout(resolve, 25));
                    return read();
                });
            } catch (error) {
                if (entered) throw error;
                return read(); // Never wait indefinitely for an unresponsive tab.
            } finally { window.clearTimeout(timer); }
        }
        // No lock API: correctness takes precedence over request deduplication.
        return read();
    };
    window.addEventListener('pagehide', () => { channel?.close(); channel = null; entries.clear(); });
    window.NeoLiveRevision = Object.freeze({fetch: fetchLive, revision: fetchRevision, resume: () => { freshResume = true; }});
})();
