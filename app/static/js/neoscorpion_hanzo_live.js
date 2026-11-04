(() => {
    "use strict";

    const root = document.querySelector("[data-hanzo-live]");
    if (!root || !window.NeoLiveUpdates) {
        return;
    }

    const intervalMs = Number(root.dataset.refreshIntervalMs || 0);
    const revisionUrl = root.dataset.revisionUrl;
    const panelUrl = root.dataset.panelUrl;
    let operationId = root.dataset.operationId || "none";
    let revision = Number(root.dataset.revision || 0);
    let reloading = false;
    let refreshing = false;
    let panelDigest = '';

    const reload = () => {
        if (reloading) {
            return;
        }
        reloading = true;
        controller?.setEnabled(false);
        window.location.reload();
    };

    const poll = async () => {
        const response = await (window.NeoLiveRevision?.revision || fetch)(revisionUrl, {
            cache: "no-store",
            credentials: "same-origin",
            headers: {"Accept": "application/json"},
        }, intervalMs, operationId);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.error || "Hanzo live status is unavailable.");
        }
        const nextOperationId = payload.operation_id === null
            ? "none"
            : String(payload.operation_id);
        const nextRevision = Number(payload.revision || 0);
        if (nextOperationId !== operationId) {
            reload();
        } else if (nextRevision !== revision && !refreshing) {
            if (!panelUrl) { reload(); return; }
            refreshing = true;
            try {
                const response = await (window.NeoLiveRevision?.fetch || fetch)(panelUrl, {
                    cache: "no-store", credentials: "same-origin",
                    headers: {Accept: 'application/json', 'X-Neo-Panel-Digest': panelDigest},
                });
                const panel = await response.json().catch(() => ({}));
                if (!response.ok || panel.ok !== true) throw new Error(panel.error || 'Hanzo refresh unavailable.');
                if (String(panel.operation_id ?? 'none') !== nextOperationId
                        || Number(panel.revision || 0) < Math.max(nextRevision, revision)) return;
                if (!panel.unchanged) {
                    const template = document.createElement('template');
                    template.innerHTML = String(panel.html || '').trim();
                    const fresh = template.content.querySelector('[data-hanzo-panel]');
                    const old = root.querySelector('[data-hanzo-panel]');
                    if (!fresh || !old) { reload(); return; }
                    const saved = [window.scrollX, window.scrollY];
                    old.replaceWith(fresh);
                    window.scrollTo(...saved);
                }
                panelDigest = panel.panel_digest || '';
                revision = Number(panel.revision || 0);
                root.dataset.revision = String(revision);
            } finally { refreshing = false; }
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
