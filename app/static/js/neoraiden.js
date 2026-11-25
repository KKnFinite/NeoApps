(() => {
    "use strict";
    const root = document.querySelector("[data-raiden]");
    if (!root) return;
    const get = name => root.querySelector("[data-" + name + "]");
    let intervalMs = Number(root.dataset.intervalMs);
    let due = Date.now() + intervalMs;
    let revision = root.dataset.revision;
    let pending = false;
    let stopped = false;
    let retriesAfter = 0;
    let timer;
    function stamp() {
        const now = new Date();
        get("success").dateTime = now.toISOString();
        get("success").textContent = now.toLocaleTimeString();
    }
    function warn(message) {
        get("stale").textContent = message;
        get("stale").hidden = !message;
    }
    let windows = JSON.parse(root.dataset.windows || "[]");
    const insideSort = () => windows.some(([start, end]) => start <= Date.now() && Date.now() < end);
    async function refresh() {
        if (!intervalMs || !insideSort()) return;
        if (pending || stopped || document.hidden || Date.now() < retriesAfter) return;
        pending = true;
        clearTimeout(timer);
        get("refresh-status").textContent = "Updating…";
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
            const response = await fetch(root.dataset.state, {
                credentials: "same-origin", cache: "no-store", signal: controller.signal,
                headers: { "X-Raiden-Automatic": "1", ...(revision && { "If-None-Match": '"' + revision + '"' }) }
            });
            const periods = response.headers.get("X-Raiden-Windows");
            if (periods) windows = JSON.parse(periods);
            const interval = response.headers.get("X-Raiden-Interval-Ms");
            if (interval !== null) intervalMs = Number(interval);
            if (response.status === 204) return;
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
            due = Math.max(retriesAfter, Date.now() + intervalMs);
            tick();
        }
    }
    function tick() {
        clearTimeout(timer);
        if (pending || document.hidden) return;
        const now = Date.now();
        const active = insideSort();
        get("refresh-status").textContent = stopped || !intervalMs ? "Live updates off" : active ? "" : "Outside Sort hours";
        if (stopped || !intervalMs) return;
        if (active && now >= due && now >= retriesAfter) {
            refresh();
            return;
        }
        // Wake for a due update, a physical window boundary, or a manual retry.
        const wakes = windows.flat().filter(time => time > now);
        if (active) wakes.push(Math.max(due, retriesAfter));
        if (wakes.length) timer = setTimeout(tick, Math.min(Math.min(...wakes) - now, 2147483647));
    }
    get("sort-choice").addEventListener("change", event => location.assign(event.target.value));
    document.addEventListener("visibilitychange", tick);
    window.addEventListener("pageshow", event => { if (event.persisted) tick(); });
    stamp();
    tick();
})();
