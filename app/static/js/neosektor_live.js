/* Shared transport/status only. Pages own scheduling, mutations and rendering. */
(() => {
    "use strict";
    let periods = [];
    let clockOffset = 0;
    const insideSort = () => {
        const now = Date.now() + clockOffset;
        return periods.some(([start, end]) => start <= now && now < end);
    };

    const poll = async ({
        stateUrl, revision, errorMessage, requestOptions, manual = false, pace = false,
        accept = () => true, onRevision, onStatus, onState,
    }) => {
        if (!manual && !insideSort()) return;
        const pollUrl = new URL(stateUrl, window.location.href);
        pollUrl.searchParams.set("revision", revision);
        const response = pace && !manual && window.NeoLiveRevision?.pacedState
            ? await window.NeoLiveRevision.pacedState(pollUrl, requestOptions, insideSort)
            : await (window.NeoLiveRevision?.fetch || fetch)(pollUrl, requestOptions);
        if (!response.ok) {
            throw new Error(errorMessage);
        }
        const payload = await response.json();
        if (!payload.ok) {
            throw new Error(errorMessage);
        }
        // Validate first, then reject stale snapshots before ANY local changes.
        if (!accept()) {
            return;
        }
        onRevision(payload.revision);
        const refresh = payload.refresh || payload.state?.refresh || {};
        onStatus(refresh);
        if (refresh.auto_refresh_enabled === false || payload.changed === false) {
            return;
        }
        if (!payload.state) {
            throw new Error(errorMessage);
        }
        onState(payload.state);
    };

    const renderStatus = (root, status, paused, separator = " · ") => {
        periods = status.poll_windows || [];
        clockOffset = Number.isFinite(status.poll_clock_ms) ? status.poll_clock_ms - Date.now() : 0;
        const isActive = status.auto_refresh_enabled !== false;
        root.dataset.refreshActive = isActive ? "true" : "false";
        if (paused) {
            paused.hidden = isActive;
            paused.replaceChildren(
                document.createTextNode(status.message || "Live updates off - outside Ops window"),
            );
            if (status.operation_label && status.window_label) {
                const detail = document.createElement("span");
                detail.textContent = `${status.operation_label}${separator}${status.window_label}`;
                paused.appendChild(detail);
            }
        }
    };

    window.NeoSektorLive = { poll, renderStatus, insideSort };
})();
