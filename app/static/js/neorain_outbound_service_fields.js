/* Rain-owned operator fields. No Google write or milestone mutation. */
(() => {
    "use strict";

    const root = document.querySelector('[data-neorain-board-page="outbound"]');
    const url = root?.dataset.serviceUrl;
    if (!root || !url) return;

    const fields = new Set(["meal", "jump", "js_in"]);
    const allViews = (missionId) => Array.from(
        root.querySelectorAll("[data-neorain-outbound-row][data-neorain-mission-id]")
    ).filter((view) => view.dataset.neorainMissionId === String(missionId));

    const messageFor = (control) => control.closest(
        ".neorain-service-cell, .neorain-mobile-service-field"
    )?.querySelector("[data-neorain-service-message]");

    const showMessage = (control, message = "") => {
        const output = messageFor(control);
        if (!output) return;
        output.textContent = message;
        output.hidden = !message;
    };

    const currentValue = (control) => control.dataset.neorainServiceField === "meal"
        ? String(control.checked)
        : control.value.trim();

    const markDirty = (control) => {
        control.dataset.neorainServiceDirty = String(
            currentValue(control) !== (control.dataset.neorainServiceSavedValue || "")
        );
    };

    const parsedValue = (control) => {
        const field = control.dataset.neorainServiceField;
        if (field === "meal") {
            return { wire: control.checked, display: String(control.checked) };
        }
        const text = control.value.trim();
        if (field === "jump") {
            if (text !== "" && !/^[0-9]$/.test(text)) {
                throw new Error("Jump must be a single digit (0–9).");
            }
            return { wire: text, display: text };
        }
        if (field === "js_in") {
            const value = /^[0-9]{4}$/.test(text)
                ? text.slice(0, 2) + ":" + text.slice(2)
                : text;
            if (value && !/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(value)) {
                throw new Error("Use military HH:MM (00:00–23:59).");
            }
            return { wire: value, display: value };
        }
        throw new Error("Unsupported Outbound field.");
    };

    const applyState = (missionId, service, originatingControl = null) => {
        if (!service) return;
        const values = {
            meal: Boolean(service.meal),
            jump: service.jump == null ? "" : String(service.jump),
            js_in: service.js_in || "",
        };
        allViews(missionId).forEach((view) => {
            view.dataset.neorainServiceVersion = String(service.service_version ?? 0);
            view.querySelectorAll("[data-neorain-service-field]").forEach((control) => {
                const field = control.dataset.neorainServiceField;
                if (!(field in values)) return;
                if (control !== originatingControl && (
                    control.dataset.neorainServiceDirty === "true"
                    || control.dataset.neorainServiceSaving === "true"
                )) return;
                if (field === "meal") control.checked = values.meal;
                else control.value = values[field];
                control.dataset.neorainServiceSavedValue = String(values[field]);
                control.dataset.neorainServiceDirty = "false";
            });
            view.querySelectorAll("[data-neorain-service-display]").forEach((display) => {
                const field = display.dataset.neorainServiceDisplay;
                const value = values[field];
                display.textContent = field === "meal"
                    ? (value ? "YES" : "—") : (value === "" ? "—" : value);
            });
        });
    };

    const revertValue = (control) => {
        const saved = control.dataset.neorainServiceSavedValue || "";
        if (control.dataset.neorainServiceField === "meal") {
            control.checked = saved === "true";
        } else {
            control.value = saved;
        }
        markDirty(control);
    };

    const save = async (control) => {
        const field = control.dataset.neorainServiceField;
        const view = control.closest("[data-neorain-outbound-row][data-neorain-mission-id]");
        if (!view || !fields.has(field) || control.dataset.neorainServiceSaving === "true") return;

        let desired;
        try {
            desired = parsedValue(control);
        } catch (error) {
            showMessage(control, error.message);
            markDirty(control);
            return;
        }
        if (desired.display === (control.dataset.neorainServiceSavedValue || "")) {
            if (field !== "meal") control.value = desired.display;
            markDirty(control);
            showMessage(control);
            return;
        }

        const missionId = view.dataset.neorainMissionId;
        control.dataset.neorainServiceSaving = "true";
        control.disabled = true;
        showMessage(control);
        try {
            const response = await fetch(url, {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    mission_id: Number(missionId),
                    field,
                    value: desired.wire,
                    expected_version: Number(view.dataset.neorainServiceVersion || 0),
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) {
                if (response.status === 409 && payload.service) {
                    applyState(missionId, payload.service, control);
                    showMessage(control, payload.error || "Details changed. Review latest values.");
                } else {
                    if (field === "meal") revertValue(control);
                    showMessage(control, payload.error || "Unable to save this field.");
                }
                return;
            }
            applyState(missionId, payload.service, control);
            showMessage(control);
            if (payload.revision) {
                root.dispatchEvent(new CustomEvent("neorain:revision", {
                    detail: { revision: payload.revision },
                }));
            }
        } catch (_) {
            if (field === "meal") revertValue(control);
            showMessage(control, "Unable to save. Try again.");
        } finally {
            control.dataset.neorainServiceSaving = "false";
            control.disabled = false;
            markDirty(control);
        }
    };

    root.addEventListener("input", (event) => {
        const control = event.target.closest?.("[data-neorain-service-field]");
        if (!control || !root.contains(control)) return;
        markDirty(control);
        showMessage(control);
    });

    root.addEventListener("change", (event) => {
        const control = event.target.closest?.("[data-neorain-service-field]");
        if (!control || !root.contains(control)) return;
        void save(control);
    });

    root.addEventListener("keydown", (event) => {
        const control = event.target.closest?.("[data-neorain-service-field]");
        if (!control || control.type === "checkbox") return;
        if (event.key === "Escape") {
            revertValue(control);
            showMessage(control);
            control.blur();
        } else if (event.key === "Enter") {
            event.preventDefault();
            control.blur();
        }
    });
})();
