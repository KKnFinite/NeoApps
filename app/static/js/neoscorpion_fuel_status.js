(() => {
    "use strict";
    const stageColors = {pending: "gray", ready: "amber", assigned: "orange", fueling: "yellow", off: "teal", "fob-ready": "teal", complete: "green", fob: "green", review: "red"};
    const etdStages = new Set(["pending", "ready", "assigned", "off", "fob-ready"]);
    const colorFor = (stage, etd, threshold, finish, now) => {
        if (etdStages.has(stage) && Number.isFinite(etd) && etd <= now + threshold * 60000) return "red";
        if (stage === "fueling" && Number.isFinite(etd) && Number.isFinite(finish) && Math.max(finish, now) > etd - 20 * 60000) return "red";
        return stageColors[stage] || "gray";
    };
    const estimateDetails = (finish, clock, now, difference = null, source = "BASELINE") => {
        if (!Number.isFinite(finish) || !clock) return "TIMING UNKNOWN";
        const count = Math.ceil(Math.abs(finish - now) / 60000);
        const delta = Number.isFinite(difference) ? ` ${difference > 0 ? "+" : ""}${difference}m PROF` : "";
        return `EST OFF ${clock} (${finish <= now ? "overdue" : "in"} ${count}m)${delta}${source === "BASELINE" ? " · BASELINE" : ""}`;
    };
    const estimateText = (finish, clock, now, difference = null) => {
        if (!Number.isFinite(finish) || !clock) return "TIMING UNKNOWN";
        return Number.isFinite(difference) && difference > 0 ? `OVER +${difference} MIN` : `EST OFF ${clock}`;
    };
    const travelText = (arrival, origin, now) => {
        if (!Number.isFinite(arrival) || !origin) return "TRUCK LOCATION UNKNOWN";
        if (arrival <= now) return "TRUCK WAIT";
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
                const details = estimateDetails(Date.parse(estimate.dataset.fuelerOffEstimate), estimate.dataset.fuelerOffClock, now,
                    estimate.dataset.profDifference === "" ? null : Number(estimate.dataset.profDifference), estimate.dataset.offSource);
                if (estimate.title !== details) estimate.title = details;
            }
        });
        document.querySelectorAll("[data-truck-travel-until]").forEach(element => {
            const text = travelText(Date.parse(element.dataset.truckTravelUntil), element.dataset.truckTravelOrigin, now);
            const label = element.querySelector("[data-fueler-off-estimate-text]") || element;
            if (label.textContent !== text) label.textContent = text;
            const title = text === "TRUCK WAIT" ? "Truck ON pending; estimated travel has elapsed." : text;
            if (element.title !== title) element.title = title;
        });
    };
    if (typeof module !== "undefined" && module.exports) module.exports = {colorFor, estimateText, estimateDetails, travelText};
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
