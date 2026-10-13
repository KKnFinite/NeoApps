/* Local clock entry only. Date, timezone and overnight policy belong to callers. */
(() => {
    "use strict";
    const normalize = (value, optional = true) => {
        let raw = String(value ?? "").trim();
        if (!raw) return optional ? "" : null;
        if (/^[0-9]{3,4}$/.test(raw)) {
            raw = raw.padStart(4, "0");
            raw = `${raw.slice(0, 2)}:${raw.slice(2)}`;
        }
        const match = /^([0-9]{2}):([0-9]{2})$/.exec(raw);
        return match && Number(match[1]) < 24 && Number(match[2]) < 60 ? raw : null;
    };
    const normalizeInput = (input, optional = true) => {
        const value = normalize(input.value, optional);
        if (value === null) {
            input.setCustomValidity?.("Use a valid time (HH:MM, 00:00–23:59).");
            input.setAttribute?.("aria-invalid", "true");
            return null;
        }
        input.value = value;
        input.setCustomValidity?.("");
        input.removeAttribute?.("aria-invalid");
        return value;
    };
    window.NeoAppsClockTime = Object.freeze({normalize, normalizeInput});

    document.addEventListener("input", (event) => {
        if (!event.target.matches?.("[data-clock-time]")) return;
        event.target.setCustomValidity("");
        event.target.removeAttribute("aria-invalid");
    });
    document.addEventListener("focusout", (event) => {
        if (event.target.matches?.("[data-clock-time]")) normalizeInput(event.target);
    });
    document.addEventListener("change", (event) => {
        if (event.target.matches?.("[data-clock-time]")) normalizeInput(event.target);
    }, true);
    document.addEventListener("submit", (event) => {
        const fields = event.target.querySelectorAll?.("[data-clock-time]:not([disabled])") || [];
        for (const input of fields) {
            if (normalizeInput(input) !== null) continue;
            event.preventDefault();
            event.stopImmediatePropagation();
            input.reportValidity?.();
            input.focus?.();
            break;
        }
    }, true);
})();
