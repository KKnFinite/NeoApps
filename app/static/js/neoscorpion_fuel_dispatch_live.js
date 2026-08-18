(() => {
    "use strict";

    const root = document.querySelector("[data-fuel-dispatch-live]");
    if (!root || !window.NeoLiveUpdates) {
        return;
    }

    const pollIntervalMs = Number(root.dataset.refreshIntervalMs || 0);
    if (!Number.isFinite(pollIntervalMs) || pollIntervalMs < 5000) {
        return;
    }

    const revisionUrl = root.dataset.revisionUrl;
    const operationId = root.dataset.operationId || "none";
    const revision = Number(root.dataset.revision || 0);
    const updateBanner = root.querySelector("[data-fuel-dispatch-update-banner]");
    const refreshButton = root.querySelector("[data-fuel-dispatch-refresh-now]");
    let dirty = false;
    let reloading = false;

    const isEditableControl = (element) => element?.matches(
        "input:not([type='hidden']):not([readonly]):not([disabled]), "
        + "select:not([disabled]), textarea:not([readonly]):not([disabled])"
    );

    const markDirty = (event) => {
        if (isEditableControl(event.target)) {
            dirty = true;
            root.dataset.liveDirty = "true";
        }
    };

    const reloadCleanPage = () => {
        if (reloading) {
            return;
        }
        reloading = true;
        controller.setEnabled(false);
        window.location.reload();
    };

    const handleChangedFingerprint = () => {
        if (!dirty) {
            reloadCleanPage();
            return;
        }
        if (updateBanner) {
            updateBanner.hidden = false;
        }
    };

    const poll = async () => {
        const response = await fetch(revisionUrl, {
            cache: "no-store",
            credentials: "same-origin",
            headers: {"Accept": "application/json"},
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error("Fuel Dispatch live status is unavailable.");
        }
        const nextOperationId = payload.operation_id === null
            ? "none"
            : String(payload.operation_id);
        const nextRevision = Number(payload.revision || 0);
        if (nextOperationId !== operationId || nextRevision !== revision) {
            handleChangedFingerprint();
        }
    };

    root.addEventListener("input", markDirty);
    root.addEventListener("change", markDirty);
    refreshButton?.addEventListener("click", () => {
        if (
            dirty
            && !window.confirm("Refresh now and discard unsaved Fuel Dispatch changes?")
        ) {
            return;
        }
        reloadCleanPage();
    });

    const controller = window.NeoLiveUpdates.create({
        continuousWhileVisible: true,
        immediate: false,
        intervalMs: pollIntervalMs,
        poll,
    });
    controller.setServerStatus({auto_refresh_enabled: true});
    window.addEventListener("pagehide", () => controller.destroy(), {once: true});
})();
