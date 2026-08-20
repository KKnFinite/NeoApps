(() => {
    "use strict";

    const root = document.querySelector("[data-hanzo-live]");
    if (!root || !window.NeoLiveUpdates) {
        return;
    }

    const intervalMs = Number(root.dataset.refreshIntervalMs || 0);
    const revisionUrl = root.dataset.revisionUrl;
    let operationId = root.dataset.operationId || "none";
    let revision = Number(root.dataset.revision || 0);
    let reloading = false;

    const reload = () => {
        if (reloading) {
            return;
        }
        reloading = true;
        controller?.setEnabled(false);
        window.location.reload();
    };

    const poll = async () => {
        const response = await fetch(revisionUrl, {
            cache: "no-store",
            credentials: "same-origin",
            headers: {"Accept": "application/json"},
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.error || "Hanzo live status is unavailable.");
        }
        const nextOperationId = payload.operation_id === null
            ? "none"
            : String(payload.operation_id);
        const nextRevision = Number(payload.revision || 0);
        if (nextOperationId !== operationId || nextRevision !== revision) {
            reload();
        }
    };

    let controller = null;
    if (Number.isFinite(intervalMs) && intervalMs >= 5000) {
        controller = window.NeoLiveUpdates.create({
            continuousWhileVisible: true,
            immediate: true,
            intervalMs,
            poll,
        });
        controller.setServerStatus({auto_refresh_enabled: true});
        window.addEventListener("pagehide", () => controller.destroy(), {once: true});
    }
})();
