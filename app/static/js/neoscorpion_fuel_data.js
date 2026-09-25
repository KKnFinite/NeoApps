(() => {
    "use strict";

    // A single pending revision survives dirty editing, without another poller.
    const createRefreshGate = ({isDirty, refresh}) => {
        let pending = null;
        let busy = false;
        return {
            async changed(revision) {
                pending = {revision};
                return this.resume();
            },
            async resume() {
                if (pending === null || busy || isDirty()) return;
                const request = pending;
                busy = true;
                try {
                    if (await refresh(request.revision) !== false && pending === request) pending = null;
                } finally { busy = false; }
                if (pending !== null && pending !== request) return this.resume();
            },
            reset() { pending = null; },
        };
    };
    const acceptSaveResponse = (response, payload, render) => {
        if (response.status === 409) {
            render(payload, "DATA CHANGED · REVIEW CURRENT VALUES");
            return;
        }
        if (!response.ok || !payload.ok) throw new Error(payload.error || "Save failed.");
        render(payload, "Saved");
    };
    if (typeof module !== "undefined" && module.exports) {
        module.exports = {createRefreshGate, acceptSaveResponse};
    }
    if (typeof document === "undefined") return;

    const baselines = new WeakMap();
    const dialog = document.querySelector("[data-fuel-data-dialog]");
    const content = dialog?.querySelector("[data-fuel-data-content]");
    let panelUrl = null;
    let panelRevision = null;
    let generation = 0;
    const controls = (scope) => Array.from(scope.querySelectorAll(
        "input[name], select[name], textarea[name]"
    )).filter(control => !["csrf_token", "expected", "assignment_id", "apu_override_present"].includes(control.name));
    const hasDirty = (scope) => Boolean(scope && (
        scope.querySelector('[data-fuel-data-busy="true"]')
        || controls(scope).some(control => baselines.has(control) && control.value !== baselines.get(control))
    ));
    const initialize = (scope) => {
        scope.querySelectorAll("[data-fuel-data-card]").forEach(card => {
            window.NeoScorpionFuelPlanning?.initialize(card);
        });
        controls(scope).forEach(control => baselines.set(control, control.value));
    };
    const status = (scope, message) => {
        const output = scope.querySelector("[data-fuel-data-status]");
        if (output) output.textContent = message;
        else if (scope === content) content.textContent = message;
    };
    const headers = () => ({
        "Accept": "application/json", "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "",
    });
    const renderPanel = (payload, message = "") => {
        content.innerHTML = payload.html || "";
        if (!payload.html) content.textContent = `${message ? message + " · " : ""}Assignment is no longer active.`;
        panelRevision = `${payload.operation_id}:${payload.revision}`;
        initialize(content);
        status(content, message);
    };
    const refreshGate = createRefreshGate({
        isDirty: () => !dialog?.open || hasDirty(content),
        async refresh(revision) {
            if (revision === panelRevision) return;
            const requestGeneration = generation;
            const response = await fetch(panelUrl, {cache: "no-store", credentials: "same-origin", headers: headers()});
            const payload = await response.json().catch(() => ({ok: false, error: "Unable to load Fueler Data. Reload and try again."}));
            if (requestGeneration !== generation || !dialog.open) return;
            // Typing may have started while the request was in flight.
            if (hasDirty(content)) return false;
            if (response.status === 404) {
                renderPanel({html: "", revision});
                return;
            }
            if (!response.ok) throw new Error(payload.error || "Refresh failed.");
            renderPanel(payload);
        },
    });
    window.NeoScorpionFuelData = {
        isOpen: () => Boolean(dialog?.open),
        hasDirty,
        revisionChanged: (revision, operationId) => refreshGate.changed(`${operationId}:${revision}`).catch(error => status(content, error.message)),
    };
    initialize(document);

    document.addEventListener("click", async (event) => {
        const open = event.target.closest("[data-fuel-data-open]");
        if (open) {
            panelUrl = open.dataset.fuelDataOpen;
            panelRevision = null;
            generation += 1;
            refreshGate.reset();
            content.textContent = "Loading Fueler Data…";
            dialog.showModal();
            window.NeoScorpionFuelData.revisionChanged(-1);
            return;
        }
        if (event.target.closest("[data-fuel-data-close]")) dialog.close();
        const cancel = event.target.closest("[data-apu-editor-cancel]");
        if (cancel) {
            const form = cancel.closest("form");
            controls(form).forEach(control => { control.value = baselines.get(control); });
            form.querySelector("details").open = false;
        }
        // APU buttons update hidden fields as part of the existing planner.
        if (event.target.closest("[data-fuel-data-card]")) refreshGate.resume().catch(() => {});
    });
    dialog?.addEventListener("close", () => {
        generation += 1;
        refreshGate.reset();
        content.innerHTML = "";
        document.dispatchEvent(new CustomEvent("neoscorpion:fuel-data-closed"));
    });
    ["input", "change"].forEach(name => document.addEventListener(name, () => {
        refreshGate.resume().catch(error => status(content, error.message));
    }));

    document.addEventListener("submit", async (event) => {
        const form = event.target;
        const card = form.closest("[data-fuel-data-card]");
        if (!card) return;
        event.preventDefault();
        if (card.dataset.fuelDataBusy === "true") return;
        if (form.matches(".neoscorpion-fueler-off-form") && hasDirty(card)) {
            status(card, "Save Fuel Entry before MARK OFF.");
            return;
        }
        const apuEditor = card.querySelector("[data-apu-editor-form]");
        if (form.matches("[data-fuel-planning-form]") && apuEditor && hasDirty(apuEditor)) {
            status(card, "Save or cancel the APU edit first.");
            return;
        }
        if (form.matches("[data-apu-editor-form]")) {
            if (hasDirty(card.querySelector("[data-fuel-planning-form]"))) {
                status(card, "Save Fuel Entry before editing APU.");
                return;
            }
            form.elements.apu_override_enabled.value = event.submitter?.matches("[data-apu-reset]") ? "0" : "1";
            if (form.elements.apu_override_enabled.value === "0") form.elements.apu_override_allowance.value = "";
        }
        const body = new FormData();
        body.set("expected", card.dataset.editBaseline);
        controls(form).forEach(control => {
            if (control.value !== baselines.get(control)) body.set(control.name, control.value);
        });
        if (body.has("apu_override_enabled") || body.has("apu_override_allowance")) body.set("apu_override_present", "1");
        card.dataset.fuelDataBusy = "true";
        const enabled = Array.from(card.querySelectorAll("input, select, textarea, button")).filter(control => !control.disabled);
        enabled.forEach(control => { control.disabled = true; });
        status(card, "Saving…");
        try {
            const response = await fetch(form.getAttribute("action"), {
                method: "POST", body, cache: "no-store", credentials: "same-origin", headers: headers(),
            });
            const payload = await response.json().catch(() => ({ok: false, error: "Unable to load Fueler Data. Reload and try again."}));
            acceptSaveResponse(response, payload, (state, message) => {
                if (dialog?.open && content.contains(card)) {
                    generation += 1; // Discard any older GET still in flight.
                    renderPanel(state, message);
                    refreshGate.reset();
                } else if (card.isConnected) {
                    const holder = document.createElement("div");
                    holder.innerHTML = state.html || "";
                    initialize(holder);
                    status(holder, message);
                    if (!state.html) holder.textContent = "Assignment is no longer active.";
                    card.replaceWith(...holder.childNodes);
                }
                document.dispatchEvent(new CustomEvent("neoscorpion:fuel-data-saved", {detail: state}));
            });
        } catch (error) { status(card, error.message || "Save failed."); }
        finally {
            card.dataset.fuelDataBusy = "false";
            enabled.forEach(control => { control.disabled = false; });
        }
    });
})();
