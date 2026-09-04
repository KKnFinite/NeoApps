(() => {
    "use strict";

    const root = document.querySelector("[data-fuel-dispatch-live]");
    if (!root) {
        return;
    }

    initializeSpearSplash(root);
    initializeDispatchDetails(root);
    initializeDispatchSelects(root);
    const preserveDispatchScroll = initializeDispatchScroll(root);
    if (!window.NeoLiveUpdates) {
        return;
    }

    function initializeSpearSplash(scope) {
        const splash = scope.querySelector("[data-spear-splash]");
        const close = splash?.querySelector("[data-spear-splash-close]");
        const storageKey = "neoapps.neoscorpion.spear-splash.v1";
        if (!splash || !close) return;
        try {
            if (window.localStorage.getItem(storageKey)) return;
        } catch (_error) {
            // Storage is optional: still show and permit a per-visit close.
        }

        let dismissed = false;
        const dismiss = () => {
            if (dismissed) return;
            dismissed = true;
            try {
                window.localStorage.setItem(storageKey, "seen");
            } catch (_error) {
                // A blocked storage API must never prevent manual dismissal.
            }
            splash.classList.remove("is-visible");
            splash.classList.add("is-dismissing");
            window.setTimeout(() => { splash.hidden = true; }, 300);
        };
        splash.hidden = false;
        window.requestAnimationFrame(() => splash.classList.add("is-visible"));
        close.addEventListener("click", dismiss);
    }

    function initializeDispatchDetails(scope) {
        scope.addEventListener("click", (event) => {
            const toggle = event.target.closest("[data-neoscorpion-dispatch-details]");
            if (!toggle) return;
            event.preventDefault();
            const detail = document.getElementById(toggle.getAttribute("aria-controls"));
            if (!detail) return;
            const expanded = toggle.getAttribute("aria-expanded") === "true";
            toggle.setAttribute("aria-expanded", String(!expanded));
            detail.hidden = expanded;
            detail.setAttribute("aria-hidden", String(expanded));
        });
    }

    function initializeDispatchScroll(scope) {
        const storageKey = "neoapps.neoscorpion.fuel-dispatch.scroll.v1";
        const restore = () => {
            try {
                const saved = JSON.parse(window.sessionStorage.getItem(storageKey) || "null");
                if (!saved || saved.path !== window.location.pathname || !Number.isFinite(saved.y)) return;
                window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
                    window.scrollTo({top: saved.y, left: 0, behavior: "auto"});
                    window.sessionStorage.removeItem(storageKey);
                }));
            } catch (_error) {
                // Scroll restoration must never interfere with Dispatch actions.
            }
        };
        restore();
        return () => {
            try {
                window.sessionStorage.setItem(storageKey, JSON.stringify({
                    path: window.location.pathname,
                    y: window.scrollY,
                }));
            } catch (_error) {
                // Browser storage is an enhancement only.
            }
        };
    }

    function initializeDispatchSelects(scope) {
        const selects = Array.from(scope.querySelectorAll(
            ".neoscorpion-dispatch-primary-row select.neoscorpion-inline-select"
        ));
        let openCombobox = null;
        const close = (combobox, returnFocus = false) => {
            if (!combobox) return;
            combobox.classList.remove("is-open");
            combobox.trigger.setAttribute("aria-expanded", "false");
            if (returnFocus) combobox.trigger.focus();
            if (openCombobox === combobox) openCombobox = null;
        };
        const open = (combobox) => {
            if (openCombobox && openCombobox !== combobox) close(openCombobox);
            combobox.classList.add("is-open");
            combobox.trigger.setAttribute("aria-expanded", "true");
            openCombobox = combobox;
        };

        selects.forEach((select, index) => {
            if (select.dataset.dispatchComboboxReady === "true") return;
            select.dataset.dispatchComboboxReady = "true";
            const combobox = document.createElement("div");
            combobox.className = "neoscorpion-dispatch-combobox";
            const trigger = document.createElement("button");
            trigger.type = "button";
            trigger.className = "neoscorpion-dispatch-combobox-trigger";
            trigger.setAttribute("aria-haspopup", "listbox");
            trigger.setAttribute("aria-expanded", "false");
            trigger.setAttribute("aria-label", select.getAttribute("aria-label") || "Dispatch selection");
            const panel = document.createElement("div");
            const panelId = `neoscorpion-dispatch-options-${index}-${Date.now()}`;
            panel.className = "neoscorpion-dispatch-combobox-options";
            panel.id = panelId;
            panel.setAttribute("role", "listbox");
            trigger.setAttribute("aria-controls", panelId);
            select.parentNode.insertBefore(combobox, select);
            combobox.append(select, trigger, panel);
            select.classList.add("neoscorpion-dispatch-native-select");
            select.tabIndex = -1;
            select.setAttribute("aria-hidden", "true");
            combobox.trigger = trigger;

            const sync = () => {
                const selected = select.options[select.selectedIndex];
                trigger.textContent = selected?.textContent?.trim() || "Unassigned";
                trigger.classList.toggle("is-unassigned", !select.value);
                panel.querySelectorAll("[role='option']").forEach((option) => {
                    option.setAttribute("aria-selected", String(option.dataset.value === select.value));
                });
            };
            Array.from(select.options).forEach((option) => {
                const choice = document.createElement("button");
                choice.type = "button";
                choice.className = "neoscorpion-dispatch-combobox-option";
                choice.setAttribute("role", "option");
                choice.dataset.value = option.value;
                choice.textContent = option.textContent.trim();
                choice.addEventListener("click", () => {
                    select.value = option.value;
                    select.dispatchEvent(new Event("change", {bubbles: true}));
                    sync();
                    close(combobox, true);
                });
                panel.append(choice);
            });
            select.addEventListener("change", sync);
            trigger.addEventListener("click", () => {
                if (combobox.classList.contains("is-open")) close(combobox);
                else open(combobox);
            });
            trigger.addEventListener("keydown", (event) => {
                if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
                event.preventDefault();
                open(combobox);
                const selected = panel.querySelector("[aria-selected='true']") || panel.querySelector("button");
                selected?.focus();
            });
            panel.addEventListener("keydown", (event) => {
                const options = Array.from(panel.querySelectorAll("button"));
                const index = options.indexOf(event.target);
                if (event.key === "Escape") {
                    event.preventDefault();
                    close(combobox, true);
                } else if (index >= 0 && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
                    event.preventDefault();
                    const next = event.key === "ArrowDown"
                        ? Math.min(index + 1, options.length - 1)
                        : Math.max(index - 1, 0);
                    options[next]?.focus();
                }
            });
            sync();
        });
        document.addEventListener("click", (event) => {
            if (openCombobox && !openCombobox.contains(event.target)) close(openCombobox);
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && openCombobox) {
                event.preventDefault();
                close(openCombobox, true);
            }
        });
    }

    const pollIntervalMs = Number(root.dataset.refreshIntervalMs || 0);
    const revisionUrl = root.dataset.revisionUrl;
    const autosaveUrl = root.dataset.autosaveUrl;
    const spearActionUrl = root.dataset.spearActionUrl;
    const spearRecalculationMs = Math.max(
        60000,
        Number(root.dataset.spearRecalculationMs || 120000)
    );
    const spearRenderedAt = Date.now();
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
        preserveDispatchScroll();
        window.location.reload();
    };

    const handleChangedFingerprint = () => {
        if (!hasUnsavedControls()) {
            reloadPage();
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
        } else if (
            root.dataset.spearRecommendationsEnabled === "true"
            && Date.now() - spearRenderedAt >= spearRecalculationMs
            && !hasUnsavedControls()
        ) {
            reloadPage();
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
        const assignmentId = form.elements.namedItem("assignment_id");
        const expectedFueler = form.elements.namedItem(
            "expected_assigned_fueler_user_id"
        );
        const expectedTruck = form.elements.namedItem(
            "expected_assigned_truck_id"
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
        Array.from(form.elements).filter((control) => control.matches?.(
            "select[name='assigned_fueler_user_id'], select[name='assigned_truck_id'], "
            + "select[name='review_status'], "
            + "input[data-dispatch-apu-override-enabled], input[data-dispatch-apu-override-value]"
        )).forEach((control) => {
            initialControlValues.set(control, controlValue(control));
        });
    };

    const updateApuAllowanceDisplay = (form, payload, button) => {
        const effectiveLbs = payload.effective_apu_allowance_lbs;
        const effective = root.querySelector(
            `[data-dispatch-apu-effective][data-dispatch-assignment-form='${form.id}']`
        );
        const enabled = form.elements.namedItem("apu_override_enabled");
        const allowance = form.elements.namedItem("apu_override_allowance");
        const editor = form.querySelector("[data-dispatch-apu-editor]");
        if (effective) {
            if (effectiveLbs === null || effectiveLbs === undefined) {
                effective.textContent = "-";
            } else {
                const thousands = Number(effectiveLbs) / 1000;
                effective.textContent = `APU ${thousands.toFixed(1)}K`;
            }
        }
        if (enabled) enabled.value = payload.apu_override_enabled ? "1" : "0";
        if (allowance) {
            allowance.value = payload.apu_override_enabled
                ? (Number(payload.apu_override_allowance_lbs) / 1000).toFixed(1)
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
        const apuEnabled = form.elements.namedItem("apu_override_enabled");
        const apuAllowance = form.elements.namedItem("apu_override_allowance");
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

    const submitTruckCardAction = async (form, button) => {
        if (form.dataset.truckCardBusy === "true") {
            return;
        }
        const status = form.querySelector("[data-dispatch-truck-card-status]");
        form.dataset.truckCardBusy = "true";
        if (button) button.disabled = true;
        setStatus(status, "Saving...");
        try {
            // The hidden operational field is named "action". HTML form named
            // properties can therefore shadow HTMLFormElement.action and turn
            // form.action into the input element instead of the endpoint URL.
            const actionUrl = form.getAttribute("action");
            const response = await fetch(actionUrl, {
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
                throw new Error(payload.error || "Truck update failed.");
            }
            adoptFingerprint(payload);
            reloadPage();
        } catch (error) {
            form.dataset.truckCardBusy = "false";
            if (button) button.disabled = false;
            setStatus(status, `Save Failed: ${error.message || "Unable to update this truck."}`, "error");
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
        const isAsyncAssignment = event.submitter?.matches("[data-dispatch-assignment-submit]");
        const truckCardForm = event.target.closest("[data-dispatch-truck-card-form]");
        if (truckCardForm) {
            event.preventDefault();
            submitTruckCardAction(
                truckCardForm,
                event.submitter || truckCardForm.querySelector("button[type='submit']")
            );
            return;
        }
        const button = event.submitter?.matches("[data-dispatch-assignment-submit]")
            ? event.submitter
            : null;
        const form = button?.form || button?.closest("[data-dispatch-assignment-form]");
        if (!form) {
            if (!isAsyncAssignment) preserveDispatchScroll();
            return;
        }
        event.preventDefault();
        submitAssignment(form, button);
    });
    root.addEventListener("click", (event) => {
        const cancel = event.target.closest("[data-dispatch-apu-cancel]");
        if (!cancel) return;
        const editor = cancel.closest("[data-dispatch-apu-editor]");
        const form = cancel.closest("[data-dispatch-assignment-form]")
            || cancel.closest("[data-neoscorpion-dispatch-detail-row]")
                ?.querySelector("[data-dispatch-apu-override-value]")?.form;
        if (!editor || !form) return;
        const allowance = form.elements.namedItem("apu_override_allowance");
        const enabled = form.elements.namedItem("apu_override_enabled");
        if (allowance) allowance.value = allowance.dataset.originalValue || "";
        if (enabled) enabled.value = enabled.dataset.originalValue || enabled.value;
        editor.open = false;
    });
    root.querySelectorAll("[data-dispatch-apu-editor]").forEach((editor) => {
        const allowance = editor.querySelector("[data-dispatch-apu-override-value]");
        const enabled = editor.querySelector("[data-dispatch-apu-override-enabled]");
        editor.addEventListener("toggle", () => {
            if (!editor.open) return;
            if (allowance) allowance.dataset.originalValue = allowance.value;
            if (enabled) enabled.dataset.originalValue = enabled.value;
        });
    });
    syncDirtyState();
    if (
        root.dataset.spearAutomationEnabled === "true"
        && spearActionUrl
        && root.dataset.spearPlanToken
    ) {
        const delay = Math.max(1000, Number(root.dataset.spearStabilityDelayMs || 5000));
        window.setTimeout(async () => {
            if (reloading || hasUnsavedControls() || root.dataset.liveDirty === "true") return;
            root.dataset.liveDirty = "true";
            const body = new FormData();
            body.set("execution_mode", "automatic");
            body.set("plan_token", root.dataset.spearPlanToken);
            try {
                const response = await fetch(spearActionUrl, {
                    method: "POST",
                    body,
                    cache: "no-store",
                    credentials: "same-origin",
                    headers: {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok || payload.ok !== true) {
                    throw new Error(payload.error || "Automation action failed.");
                }
                reloadPage();
            } catch (error) {
                root.dataset.liveDirty = "false";
                const status = root.querySelector("[data-spear-fleet-status]");
                setStatus(status, `SPEAR: ${error.message}`, "error");
            }
        }, delay);
    }
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
