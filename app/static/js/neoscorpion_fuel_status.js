(() => {
    "use strict";
    const stageColors = {pending: "gray", ready: "amber", assigned: "orange", fueling: "yellow", off: "teal", "fob-ready": "teal", complete: "green", fob: "green", review: "red"};
    const etdStages = new Set(["pending", "ready", "assigned", "off", "fob-ready"]);
    const colorFor = (stage, etd, threshold, finish, now) => {
        if (etdStages.has(stage) && Number.isFinite(etd) && etd <= now + threshold * 60000) return "red";
        if (stage === "fueling" && Number.isFinite(etd) && Number.isFinite(finish) && Math.max(finish, now) > etd - 20 * 60000) return "red";
        return stageColors[stage] || "gray";
    };
    const estimateText = (finish, clock, now, difference = null, source = "BASELINE") => {
        if (!Number.isFinite(finish) || !clock) return "TIMING UNKNOWN";
        const count = Math.ceil(Math.abs(finish - now) / 60000);
        const delta = Number.isFinite(difference) ? ` ${difference > 0 ? "+" : ""}${difference}m PROF` : "";
        return `EST OFF ${clock} (${finish <= now ? "overdue" : "in"} ${count}m)${delta}${source === "BASELINE" ? " · BASELINE" : ""}`;
    };
    const travelText = (arrival, origin, now) => {
        if (!Number.isFinite(arrival) || !origin) return "TRUCK LOCATION UNKNOWN";
        if (arrival <= now) return "ARRIVAL UNCONFIRMED";
        return `COMING FROM ${origin} · ~${Math.ceil((arrival - now) / 60000)} MIN`;
    };
    const update = () => {
        const now = Date.now();
        document.querySelectorAll("[data-fuel-status]").forEach(element => {
            const color = colorFor(element.dataset.fuelStatus, Date.parse(element.dataset.fuelStatusEtd),
                Number(element.dataset.fuelStatusThreshold), Date.parse(element.dataset.fuelStatusFinish), now);
            if (element.dataset.fuelStatusColor !== color) element.dataset.fuelStatusColor = color;
            const estimate = element.querySelector("[data-fueler-off-estimate]");
            if (element.dataset.fuelStatus === "fueling" && estimate) {
                const text = estimateText(Date.parse(estimate.dataset.fuelerOffEstimate), estimate.dataset.fuelerOffClock, now,
                    estimate.dataset.profDifference === "" ? null : Number(estimate.dataset.profDifference), estimate.dataset.offSource);
                const label = estimate.querySelector("[data-fueler-off-estimate-text]");
                if (!label) return;
                if (label.textContent !== text) label.textContent = text;
                if (estimate.title !== text) estimate.title = text;
                // Keep the SMALL font's height and the existing column width.
                // Condense just this readout horizontally rather than truncating
                // its clock/countdown or changing any table/pill geometry.
                const scale = String(Math.min(1, estimate.clientWidth / Math.max(1, label.scrollWidth)));
                if (label.style.getPropertyValue("--off-estimate-scale") !== scale) {
                    label.style.setProperty("--off-estimate-scale", scale);
                }
            }
        });
        document.querySelectorAll("[data-truck-travel-until]").forEach(element => {
            const text = travelText(Date.parse(element.dataset.truckTravelUntil), element.dataset.truckTravelOrigin, now);
            const label = element.querySelector("[data-fueler-off-estimate-text]") || element;
            if (label.textContent !== text) label.textContent = text;
            if (element.title !== text) element.title = text;
            if (label !== element) {
                const scale = String(Math.min(1, element.clientWidth / Math.max(1, label.scrollWidth)));
                if (label.style.getPropertyValue("--off-estimate-scale") !== scale) label.style.setProperty("--off-estimate-scale", scale);
            }
        });
    };
    if (typeof module !== "undefined" && module.exports) module.exports = {colorFor, estimateText, travelText};
    if (typeof document === "undefined") return;
    // New live fragments and Fueler Data dialogs use the same clock treatment.
    let queued = false;
    new MutationObserver(() => {
        if (queued) return;
        queued = true;
        queueMicrotask(() => { queued = false; update(); });
    }).observe(document.documentElement, {childList: true, subtree: true});
    document.addEventListener("visibilitychange", update);
    window.addEventListener("resize", update);
    document.fonts?.ready.then(update);
    window.setInterval(update, 1000);
    update();
})();
