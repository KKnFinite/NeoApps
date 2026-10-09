(() => {
    "use strict";
    const stageColors = {pending: "gray", ready: "amber", assigned: "orange", fueling: "yellow", off: "teal", "fob-ready": "teal", complete: "green", fob: "green", review: "red"};
    const etdStages = new Set(["pending", "ready", "assigned", "off", "fob-ready"]);
    const colorFor = (stage, etd, threshold, finish, now) => {
        if (etdStages.has(stage) && Number.isFinite(etd) && etd <= now + threshold * 60000) return "red";
        if (stage === "fueling" && Number.isFinite(etd) && Number.isFinite(finish) && Math.max(finish, now) > etd - 20 * 60000) return "red";
        return stageColors[stage] || "gray";
    };
    const update = () => {
        const now = Date.now();
        document.querySelectorAll("[data-fuel-status]").forEach(element => {
            const color = colorFor(element.dataset.fuelStatus, Date.parse(element.dataset.fuelStatusEtd),
                Number(element.dataset.fuelStatusThreshold), Date.parse(element.dataset.fuelStatusFinish), now);
            if (element.dataset.fuelStatusColor !== color) element.dataset.fuelStatusColor = color;
        });
    };
    if (typeof module !== "undefined" && module.exports) module.exports = {colorFor};
    if (typeof document === "undefined") return;
    // New live fragments and Fueler Data dialogs use the same clock treatment.
    let queued = false;
    new MutationObserver(() => {
        if (queued) return;
        queued = true;
        queueMicrotask(() => { queued = false; update(); });
    }).observe(document.documentElement, {childList: true, subtree: true});
    document.addEventListener("visibilitychange", update);
    window.setInterval(update, 1000);
    update();
})();
