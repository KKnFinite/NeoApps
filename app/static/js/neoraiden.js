(() => {
    "use strict";
    const root = document.querySelector("[data-raiden]");
    if (!root) return;
    const get = name => root.querySelector("[data-" + name + "]");
    const chooser = get("interval");
    const storageKey = "neoapps.raiden.refresh.seconds";
    try {
        const stored = localStorage.getItem(storageKey);
        if (Array.from(chooser.options).some(option => option.value === stored)) chooser.value = stored;
    } catch (_) { /* Storage is optional on shared displays. */ }
    let due = Date.now() + Number(chooser.value) * 1000;
    let revision = root.dataset.revision;
    let pending = false;
    let stopped = false;
    let retriesAfter = 0;
    function stamp() {
        const now = new Date();
        get("success").dateTime = now.toISOString();
        get("success").textContent = now.toLocaleTimeString();
    }
    function warn(message) {
        get("stale").textContent = message;
        get("stale").hidden = !message;
    }
    async function refresh() {
        if (pending || stopped || document.hidden || Date.now() < retriesAfter) return;
        pending = true;
        get("refresh").disabled = true;
        get("countdown").textContent = "Updating…";
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
            const response = await fetch(root.dataset.state, {
                credentials: "same-origin", cache: "no-store", signal: controller.signal,
                headers: revision ? { "If-None-Match": '"' + revision + '"' } : {}
            });
            if (response.redirected || [401,403,404].includes(response.status)) {
                get("board").replaceChildren();
                root.dispatchEvent(new Event("raiden:updated"));
                warn("This Sort is no longer available. Reopen NeoRaiden to check access.");
                stopped = true;
                return;
            }
            if (response.status === 429) {
                const raw = response.headers.get("Retry-After");
                const delay = raw && /^\d+$/.test(raw) ? Number(raw) * 1000 : Date.parse(raw) - Date.now();
                retriesAfter = Date.now() + (Number.isFinite(delay) ? Math.max(0,delay) : 30000);
            }
            if (response.status !== 304) {
                if (!response.ok) throw Error();
                const data = await response.json();
                if (typeof data.html !== "string") throw Error();
                root.dispatchEvent(new Event("raiden:before-update"));
                get("board").innerHTML = data.html;
                root.dispatchEvent(new Event("raiden:updated"));
            }
            revision = (response.headers.get("ETag") || "").replaceAll('"',"");
            stamp();
            warn("");
        } catch (_) {
            warn("Refresh unavailable. Showing the last successful update.");
        } finally {
            clearTimeout(timeout);
            pending = false;
            due = Math.max(retriesAfter, Date.now() + Number(chooser.value) * 1000);
            tick();
        }
    }
    function tick() {
        if (pending || document.hidden) return;
        get("refresh").disabled = stopped || Date.now() < retriesAfter;
        const seconds = Math.max(0, Math.ceil((due - Date.now()) / 1000));
        get("countdown").textContent = stopped ? "Refresh stopped" : "Next refresh in " + seconds + "s";
        if (!stopped && !seconds) refresh();
    }
    chooser.addEventListener("change", () => {
        try { localStorage.setItem(storageKey, chooser.value); } catch (_) {}
        due = Math.max(retriesAfter, Date.now() + Number(chooser.value) * 1000);
        tick();
    });
    get("sort-choice").addEventListener("change", event => location.assign(event.target.value));
    get("refresh").addEventListener("click", refresh);
    document.addEventListener("visibilitychange", tick);
    window.addEventListener("pageshow", event => { if (event.persisted) refresh(); });
    stamp();
    tick();
    setInterval(tick, 1000);
})();
