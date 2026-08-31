(() => {
    "use strict";

    const root = document.querySelector("[data-subzero-ucc]");
    if (!root) return;

    root.querySelectorAll("[data-ucc-assignment-form]").forEach((form) => {
        form.addEventListener("change", () => {
            root.dataset.dirty = "true";
        });
        form.addEventListener("submit", () => {
            root.dataset.saving = "true";
        });
    });

    const refreshIntervalMs = Number(root.dataset.refreshIntervalMs);
    if (
        !window.NeoLiveUpdates
        || !root.dataset.revisionUrl
        || !Number.isFinite(refreshIntervalMs)
        || refreshIntervalMs < 1000
    ) return;

    let revision = root.dataset.revision;
    const controller = window.NeoLiveUpdates.create({
        intervalMs: refreshIntervalMs,
        continuousWhileVisible: true,
        poll: async () => {
            const url = new URL(root.dataset.revisionUrl, window.location.origin);
            url.searchParams.set("revision", revision);
            const response = await fetch(url, {cache: "no-store"});
            const payload = await response.json();
            controller.setServerStatus(payload.refresh || {});
            revision = payload.revision || revision;
            if (
                payload.changed
                && root.dataset.dirty !== "true"
                && root.dataset.saving !== "true"
                && !root.querySelector(":focus")
            ) {
                window.location.reload();
            }
        },
    });
    controller.setServerStatus(JSON.parse(root.dataset.refreshStatus || "{}"));
})();
