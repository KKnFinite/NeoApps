/* Shared transport/status only. Pages own scheduling, mutations and rendering. */
(() => {
    "use strict";

    const poll = async ({
        stateUrl, revision, errorMessage, requestOptions,
        accept = () => true, onRevision, onStatus, onState,
    }) => {
        const pollUrl = new URL(stateUrl, window.location.href);
        pollUrl.searchParams.set("revision", revision);
        const response = await fetch(pollUrl, requestOptions);
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

    window.NeoSektorLive = { poll, renderStatus };
})();
