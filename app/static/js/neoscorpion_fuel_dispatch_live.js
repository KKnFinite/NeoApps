(() => {
    "use strict";

    const root = document.querySelector("[data-fuel-dispatch-live]");
    if (!root || !window.NeoLiveUpdates) {
        return;
    }

    const pollIntervalMs = Number(root.dataset.refreshIntervalMs || 0);
    const revisionUrl = root.dataset.revisionUrl;
    const autosaveUrl = root.dataset.autosaveUrl;
    let operationId = root.dataset.operationId || "none";
    let revision = Number(root.dataset.revision || 0);
    const initialControlValues = new WeakMap();
    let reloading = false;
    let controller = null;

    const isEditableControl = (element) => element?.matches(
        "input:not([type='hidden']):not([readonly]):not([disabled]), "
        + "select:not([disabled]), textarea:not([readonly]):not([disabled])"
    );

    const controlValue = (control) => {
        if (control.type === "checkbox" || control.type === "radio") {
            return control.checked ? "1" : "0";
        }
        return control.value;
    };

    const protectedControls = () => Array.from(root.querySelectorAll(
        "select[name='assigned_fueler_user_id']:not([disabled]), "
        + "select[name='assigned_truck_id']:not([disabled])"
    ));

    protectedControls().forEach((control) => {
        initialControlValues.set(control, controlValue(control));
    });

    const hasUnsavedControls = () => (
        protectedControls().some(
            (control) => initialControlValues.get(control) !== controlValue(control)
        )
    );

    const syncDirtyState = () => {
        if (hasUnsavedControls()) {
            root.dataset.liveDirty = "true";
        } else {
            root.dataset.liveDirty = "false";
        }
    };

    const setStatus = (element, message, state = "") => {
        if (!element) {
            return;
        }
        element.textContent = message;
        element.dataset.state = state;
    };

    const adoptFingerprint = (payload) => {
        operationId = payload.operation_id === null
            ? "none"
            : String(payload.operation_id);
        revision = Number(payload.revision || 0);
        root.dataset.operationId = operationId;
        root.dataset.revision = String(revision);
    };

    const reloadPage = () => {
        if (reloading) {
            return;
        }
        reloading = true;
        controller?.setEnabled(false);
        window.location.reload();
    };

    const handleChangedFingerprint = () => {
        if (!hasUnsavedControls()) {
            reloadPage();
            return;
        }
        root.querySelectorAll("[data-dispatch-assignment-form]").forEach((form) => {
            const waiting = form.querySelector("[data-assignment-live-waiting]");
            if (waiting && protectedControls().some(
                (control) => control.closest("[data-dispatch-assignment-form]") === form
                    && initialControlValues.get(control) !== controlValue(control)
            )) waiting.hidden = false;
        });
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

    const autosaveField = async (input) => {
        if (!autosaveUrl || input.dataset.autosaveSaving === "true") {
            return;
        }
        const submittedValue = input.value.trim();
        const savedValue = input.dataset.savedValue || "";
        if (submittedValue === savedValue) {
            input.dataset.autosaveFailed = "false";
            syncDirtyState();
            return;
        }
        const status = input.parentElement?.querySelector("[data-autosave-status]");
        const missionId = input.dataset.missionId || "";
        input.dataset.autosaveSaving = "true";
        setStatus(status, "Saving...");
        const body = new FormData();
        body.set("mission_id", missionId);
        body.set("field_name", input.dataset.autosaveField || "");
        body.set("value", submittedValue);
        body.set("expected_value", savedValue);
        try {
            const response = await fetch(autosaveUrl, {
                method: "POST",
                body,
                cache: "no-store",
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok !== true) {
                throw new Error(payload.error || "Save failed.");
            }
            input.dataset.savedValue = payload.display_value || "";
            input.dataset.autosaveFailed = "false";
            if (input.value.trim() === submittedValue) {
                input.value = payload.display_value || "";
            }
            adoptFingerprint(payload);
            setStatus(status, payload.changed ? "Saved" : "No change");
        } catch (error) {
            input.dataset.autosaveFailed = "true";
            setStatus(
                status,
                `Save Failed: ${error.message || "Unable to save this field."}`,
                "error"
            );
        } finally {
            input.dataset.autosaveSaving = "false";
            syncDirtyState();
        }
    };

    const updateAssignmentBaseline = (form, payload) => {
        const assignmentId = form.querySelector("input[name='assignment_id']");
        const expectedFueler = form.querySelector(
            "input[name='expected_assigned_fueler_user_id']"
        );
        const expectedTruck = form.querySelector(
            "input[name='expected_assigned_truck_id']"
        );
        if (assignmentId) {
            assignmentId.value = String(payload.assignment_id || "");
        }
        if (expectedFueler) {
            expectedFueler.value = String(payload.assigned_fueler_user_id || "");
        }
        if (expectedTruck) {
            expectedTruck.value = String(payload.assigned_truck_id || "");
        }
        form.querySelectorAll(
            "select[name='assigned_fueler_user_id'], select[name='assigned_truck_id'], "
            + "select[name='review_status'], "
            + "input[data-dispatch-apu-override-enabled], input[data-dispatch-apu-override-value]"
        ).forEach((control) => {
            initialControlValues.set(control, controlValue(control));
        });
    };

    const updateApuAllowanceDisplay = (form, payload, button) => {
        const effectiveLbs = payload.effective_apu_allowance_lbs;
        const effective = form.querySelector("[data-dispatch-apu-effective]");
        const enabled = form.querySelector("[data-dispatch-apu-override-enabled]");
        const allowance = form.querySelector("[data-dispatch-apu-override-value]");
        const editor = form.querySelector("[data-dispatch-apu-editor]");
        if (effective) {
            if (effectiveLbs === null || effectiveLbs === undefined) {
                effective.textContent = "-";
            } else {
                const thousands = Number(effectiveLbs) / 1000;
                effective.textContent = `APU ${Number.isInteger(thousands * 10) ? thousands.toFixed(1) : thousands.toFixed(2)}K`;
            }
        }
        if (enabled) enabled.value = payload.apu_override_enabled ? "1" : "0";
        if (allowance) {
            allowance.value = payload.apu_override_enabled
                ? String(Number(payload.apu_override_allowance_lbs) / 1000)
                : "";
        }
        if (editor) editor.open = false;
        button?.closest("[data-dispatch-apu-editor]")?.querySelector("summary")?.focus();
    };

    const submitAssignment = async (form, button) => {
        if (form.dataset.assignmentSaving === "true") {
            return;
        }
        const status = form.querySelector("[data-assignment-save-status]");
        const resetApu = button.matches("[data-dispatch-apu-reset]");
        const apuEnabled = form.querySelector("[data-dispatch-apu-override-enabled]");
        const apuAllowance = form.querySelector("[data-dispatch-apu-override-value]");
        if (resetApu) {
            if (apuEnabled) apuEnabled.value = "0";
            if (apuAllowance) apuAllowance.value = "";
        } else if (apuEnabled) {
            apuEnabled.value = "1";
        }
        form.dataset.assignmentSaving = "true";
        button.disabled = true;
        setStatus(status, "Saving...");
        try {
            const response = await fetch(form.action, {
                method: "POST",
                body: new FormData(form),
                cache: "no-store",
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok !== true) {
                throw new Error(payload.error || "Assignment save failed.");
            }
            adoptFingerprint(payload);
            updateAssignmentBaseline(form, payload);
            updateApuAllowanceDisplay(form, payload, button);
            button.textContent = payload.button_label || "UPDATE ASSIGNMENT";
            setStatus(status, payload.changed ? "Saved" : "No change");
        } catch (error) {
            setStatus(
                status,
                `Save Failed: ${error.message || "Unable to update this assignment."}`,
                "error"
            );
        } finally {
            form.dataset.assignmentSaving = "false";
            button.disabled = false;
            syncDirtyState();
        }
    };

    root.addEventListener("input", (event) => {
        if (isEditableControl(event.target) && !event.target.matches("[data-dispatch-autosave]")) {
            syncDirtyState();
        }
    });
    root.addEventListener("change", (event) => {
        if (event.target.matches("[data-dispatch-autosave]")) {
            autosaveField(event.target);
        } else if (isEditableControl(event.target)) {
            syncDirtyState();
        }
    });
    root.addEventListener("focusout", (event) => {
        if (event.target.matches("[data-dispatch-autosave]")) {
            autosaveField(event.target);
        }
    });
    root.addEventListener("submit", (event) => {
        const button = event.submitter?.matches("[data-dispatch-assignment-submit]")
            ? event.submitter
            : null;
        const form = button?.closest("[data-dispatch-assignment-form]");
        if (!form) {
            return;
        }
        event.preventDefault();
        submitAssignment(form, button);
    });
    root.addEventListener("click", (event) => {
        const cancel = event.target.closest("[data-dispatch-apu-cancel]");
        if (!cancel) return;
        const editor = cancel.closest("[data-dispatch-apu-editor]");
        const form = cancel.closest("[data-dispatch-assignment-form]");
        if (!editor || !form) return;
        const allowance = form.querySelector("[data-dispatch-apu-override-value]");
        const enabled = form.querySelector("[data-dispatch-apu-override-enabled]");
        if (allowance) allowance.value = allowance.dataset.originalValue || "";
        if (enabled) enabled.value = enabled.dataset.originalValue || enabled.value;
        editor.open = false;
    });
    root.querySelectorAll("[data-dispatch-apu-editor]").forEach((editor) => {
        const form = editor.closest("[data-dispatch-assignment-form]");
        const allowance = form?.querySelector("[data-dispatch-apu-override-value]");
        const enabled = form?.querySelector("[data-dispatch-apu-override-enabled]");
        editor.addEventListener("toggle", () => {
            if (!editor.open) return;
            if (allowance) allowance.dataset.originalValue = allowance.value;
            if (enabled) enabled.dataset.originalValue = enabled.value;
        });
    });
    syncDirtyState();
    if (Number.isFinite(pollIntervalMs) && pollIntervalMs >= 5000) {
        controller = window.NeoLiveUpdates.create({
            continuousWhileVisible: true,
            immediate: false,
            intervalMs: pollIntervalMs,
            poll,
        });
        controller.setServerStatus({auto_refresh_enabled: true});
        window.addEventListener("pagehide", () => controller.destroy(), {once: true});
    }
})();
