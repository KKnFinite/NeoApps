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
        const outputs = scope.querySelectorAll("[data-fuel-data-status], [data-fuel-action-feedback]");
        outputs.forEach(output => { output.textContent = message; });
        const output = outputs[0];
        if (!output && scope === content) content.textContent = message;
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
        initialize,
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
        if (form.matches("[data-source-verification-form]")) return; // Explicit Dispatcher verification, not a Fueler data save.
        event.preventDefault();
        if (card.dataset.fuelDataBusy === "true") return;
        if (form.matches("[data-new-visit-form], [data-fueler-submit-form]") && hasDirty(card)) {
            status(card, form.matches("[data-fueler-submit-form]") ? "Save Fuel Entry before COMPLETE ASSIGNMENT." : "Save existing fuel entry before starting another visit.");
            return;
        }
        const physicalAction = form.matches(".neoscorpion-fueler-off-form, [data-truck-on-form]");
        const unsaved = physicalAction ? controls(card).filter(control => baselines.has(control)
            && control.value !== baselines.get(control)).map(control => ({name: control.name, value: control.value,
                scope: control.closest("[data-apu-editor-form]") ? "[data-apu-editor-form]" : "[data-fuel-planning-form]"})) : [];
        const restoreUnsaved = scope => unsaved.forEach(item => {
            const input = scope.querySelector(`${item.scope} [name="${item.name}"]`);
            if (input) { input.value = item.value; input.dispatchEvent(new Event("input", {bubbles: true})); }
        });
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
        if (form.matches("[data-fueler-submit-form]") && hasDirty(card.querySelector("[data-fuel-planning-form]"))) {
            status(card, "Save Fuel Entry before COMPLETE ASSIGNMENT.");
            return;
        }
        const body = new FormData();
        body.set("expected", card.dataset.editBaseline);
        if (form.matches(".neoscorpion-fueler-off-form") && unsaved.length) body.set("completion_deferred", "1");
        controls(form).forEach(control => {
            if (control.value !== baselines.get(control)) body.set(control.name, control.value);
        });
        if (form.matches("[data-fuel-planning-form]")) {
            // Save measurements only. Physical ON/OFF are separate actions.
            form.querySelectorAll('[data-fuel-reading="remaining"]').forEach(control => {
                body.set(control.name, control.value);
            });
        }
        if (form.matches("[data-truck-on-form]")) {
            ["expected_cycle", "expected_tail", "expected_truck_id", "expected_work_ref"].forEach(name => {
                body.set(name, form.elements[name].value);
            });
            if (form.elements.resume_off_at) body.set('resume_off_at',form.elements.resume_off_at.value);
        }
        if (form.matches('[data-new-visit-form]') && form.elements.separate_visit_confirmed?.checked) body.set('separate_visit_confirmed','1');
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
            if (response.ok && form.matches("[data-fueler-submit-form]") && !payload.submission_receipt) {
                throw new Error("Submission not confirmed. Review this visit and try again; do not assume it was submitted.");
            }
            acceptSaveResponse(response, payload, (state, message) => {
                if (response.ok && form.matches("[data-fueler-submit-form]")) message = state.next_visit_activated
                    ? "PREVIOUS TRUCK SUBMITTED · NEXT TRUCK ACTIVATED · FRESH ON REQUIRED"
                    : "SUBMITTED · READY FOR DISPATCHER COMPLETE";
                if (response.ok && physicalAction) message = `${form.matches(".neoscorpion-fueler-off-form") ? "TRUCK OFF" : "TRUCK ON"} recorded${unsaved.length ? " · Unsaved Fuel Entry kept" : ""}`;
                if (dialog?.open && content.contains(card)) {
                    generation += 1; // Discard any older GET still in flight.
                    renderPanel(state, message);
                    if (response.ok) restoreUnsaved(content);
                    refreshGate.reset();
                } else if (card.isConnected) {
                    const holder = document.createElement("div");
                    holder.innerHTML = state.html || "";
                    initialize(holder);
                    if (response.ok) restoreUnsaved(holder);
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
