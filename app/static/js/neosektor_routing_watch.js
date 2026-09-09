/* Driver-only invalidation hints; all routing remains in the canonical fetch. */
window.NeoSektorRoutingWatch = {
    create({versionUrl, initial, fetchState}) {
        let snapshot = initial;
        let generation = 0;
        let enabled = false, stopped = false, checking = false;
        let timer = null, fullRequest = null;
        const active = () => enabled && !stopped && !document.hidden;
        const clear = () => { window.clearTimeout(timer); timer = null; };
        const schedule = (delay = 1500) => {
            if (active() && timer === null && !checking) timer = window.setTimeout(check, delay);
        };
        const observe = next => {
            if (next) { snapshot = next; generation += 1; }
        };
        // Normal reconciliation, visibility wake and version-triggered fetches
        // share one in-flight request; full responses cannot arrive out of order.
        const refresh = () => {
            if (!fullRequest) fullRequest = Promise.resolve().then(fetchState).finally(() => { fullRequest = null; });
            return fullRequest;
        };
        async function check() {
            clear();
            if (!active() || checking || !snapshot) return;
            checking = true;
            let delay = 1500;
            const atRequest = generation;
            const requested = snapshot;
            const superseded = () => generation !== atRequest && (
                snapshot.sort_date !== requested.sort_date || snapshot.sort_name !== requested.sort_name
                || snapshot.version !== requested.version);
            try {
                const url = new URL(versionUrl, window.location.href);
                url.searchParams.set('sort_date', snapshot.sort_date);
                url.searchParams.set('sort_name', snapshot.sort_name);
                const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin'});
                const payload = await response.json();
                if (!response.ok || !payload.ok || typeof payload.version !== 'string') throw new Error('Version check failed');
                if (!active() || superseded() || payload.version === snapshot.version) return;
                // A safety-net request may predate this observed commit. Wait for
                // it; only fetch again if it did not deliver a fresh snapshot.
                if (fullRequest) await fullRequest;
                if (!active() || superseded()) return;
                await refresh();
                // Non-routing/no-op changes can leave the canonical revision
                // unchanged. Acknowledge once, but never acknowledge a failure.
                if (!superseded()) snapshot = {...snapshot, version: payload.version};
            } catch (_) {
                delay = 5000; // No fallback/full fetch merely because a hint failed.
            } finally {
                checking = false;
                schedule(delay);
            }
        }
        const setEnabled = value => {
            enabled = Boolean(value);
            if (!active()) clear();
            else schedule();
        };
        document.addEventListener('visibilitychange', () => {
            clear();
            if (active()) void check();
        });
        window.addEventListener('pagehide', () => { stopped = true; clear(); });
        window.addEventListener('pageshow', () => { stopped = false; schedule(); });
        return {observe, refresh, setEnabled};
    },
};
