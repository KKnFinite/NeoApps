(() => {
    "use strict";

    const root = document.querySelector("[data-fuel-dispatch-live]");
    if (!root) {
        return;
    }

    initializeSpearSplash(root);
    initializeDispatchDetails(root);
    initializeDispatchSelects(root);
    initializeDispatchApuEditors(root);
    const dispatchScroll = initializeDispatchScroll(root);
    const preserveDispatchScroll = () => dispatchScroll.preserve();
    window.addEventListener("pagehide", preserveDispatchScroll);
    let assignmentAlertTimer = null;
    function updateAssignmentAlerts(scope) {
        if (assignmentAlertTimer !== null) window.clearTimeout(assignmentAlertTimer);
        assignmentAlertTimer = null;
        const panel = scope.querySelector(".neoscorpion-panel[data-assignment-alert-threshold-minutes]");
        const threshold = Number(panel?.dataset.assignmentAlertThresholdMinutes);
        const thresholdMs = (Number.isFinite(threshold) && threshold >= 0 ? threshold : 30) * 60000;
        const now = Date.now();
        let nextChangeMs = Infinity;
        scope.querySelectorAll(".neoscorpion-dispatch-primary-row.is-ready-to-assign").forEach((row) => {
            // The server supplies the mission's canonical UTC departure, not its display label.
            const etdMs = row.dataset.etdUtc ? Date.parse(row.dataset.etdUtc) : NaN;
            const remainingMs = etdMs - now;
            const red = Number.isFinite(etdMs) && remainingMs <= thresholdMs;
            row.classList.toggle("is-assignment-alert-red", red);
            if (Number.isFinite(etdMs) && !red) {
                nextChangeMs = Math.min(nextChangeMs, remainingMs - thresholdMs);
            }
        });
        if (Number.isFinite(nextChangeMs)) {
            assignmentAlertTimer = window.setTimeout(
                () => updateAssignmentAlerts(root),
                Math.max(1, Math.min(nextChangeMs, 60000))
            );
        }
    }
    updateAssignmentAlerts(root);
    document.addEventListener("visibilitychange", () => updateAssignmentAlerts(root));
    window.addEventListener("pagehide", () => {
        if (assignmentAlertTimer !== null) window.clearTimeout(assignmentAlertTimer);
    }, {once: true});
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
        const storageKey = "neoapps.neoscorpion.fuel-dispatch.scroll.v3";
        const tableWrap = () => scope.querySelector(".neoscorpion-table-wrap--sticky");

        // A cancelled uplift restores an earlier cycle key. Resolve only a
        // missing key to this mission's active row; existing history stays exact.
        const activeRowForKey = (key) => {
            const missionId = String(key || "").split("-")[0];
            if (!/^\d+$/.test(missionId)) return null;
            return Array.from(scope.querySelectorAll(
                ".neoscorpion-dispatch-primary-row[data-dispatch-row-key]:not([data-cycle-history])"
            )).find(row => row.dataset.dispatchRowKey.startsWith(`${missionId}-`)) || null;
        };

        const restoreDetailRows = (detailIds) => {
            if (!Array.isArray(detailIds)) return;
            detailIds.forEach((detailId) => {
                let detail = document.getElementById(detailId);
                let toggle = scope.querySelector(
                    `[data-neoscorpion-dispatch-details][aria-controls="${detailId}"]`
                );
                if (!detail || !toggle) {
                    const row = activeRowForKey(detailId.replace("neoscorpion-dispatch-detail-", ""));
                    toggle = row?.querySelector("[data-neoscorpion-dispatch-details]");
                    detail = toggle ? document.getElementById(toggle.getAttribute("aria-controls")) : null;
                }
                if (!detail || !toggle) return;
                detail.hidden = false;
                detail.setAttribute("aria-hidden", "false");
                toggle.setAttribute("aria-expanded", "true");
            });
        };

        const snapshot = () => {
            const wrap = tableWrap();
            const rows = Array.from(scope.querySelectorAll(
                ".neoscorpion-dispatch-table--compact tr[data-dispatch-row-key]"
            ));
            const wrapRect = wrap?.getBoundingClientRect() || null;
            const anchor = rows.find((row) => {
                const rect = row.getBoundingClientRect();
                if (!wrapRect) return rect.bottom > 0;
                return rect.bottom > wrapRect.top && rect.top < wrapRect.bottom;
            }) || rows[0] || null;
            const anchorRect = anchor?.getBoundingClientRect() || null;
            const openDetails = Array.from(scope.querySelectorAll(
                "[data-neoscorpion-dispatch-details][aria-expanded='true']"
            )).map((toggle) => toggle.getAttribute("aria-controls")).filter(Boolean);
            return {
                path: window.location.pathname,
                y: window.scrollY,
                x: window.scrollX,
                rowKey: anchor?.dataset.dispatchRowKey || null,
                windowMissionOffset: anchorRect?.top ?? null,
                tableMissionOffset: (
                    anchorRect && wrapRect
                        ? anchorRect.top - wrapRect.top
                        : null
                ),
                tableScrollTop: wrap?.scrollTop || 0,
                tableScrollLeft: wrap?.scrollLeft || 0,
                openDetails,
            };
        };

        const applyRestore = (saved) => {
            if (!saved) return;
            restoreDetailRows(saved.openDetails);
            if (Number.isFinite(saved.y) || Number.isFinite(saved.x)) {
                window.scrollTo({
                    top: Number.isFinite(saved.y) ? saved.y : window.scrollY,
                    left: Number.isFinite(saved.x) ? saved.x : window.scrollX,
                    behavior: "auto",
                });
            }
            const wrap = tableWrap();
            const anchor = saved.rowKey
                ? scope.querySelector(
                    `.neoscorpion-dispatch-table--compact tr[data-dispatch-row-key="${saved.rowKey}"]`
                ) || activeRowForKey(saved.rowKey)
                : null;
            if (wrap) {
                if (Number.isFinite(saved.tableScrollTop)) wrap.scrollTop = saved.tableScrollTop;
                if (Number.isFinite(saved.tableScrollLeft)) wrap.scrollLeft = saved.tableScrollLeft;
                if (anchor && Number.isFinite(saved.tableMissionOffset)) {
                    const wrapTop = wrap.getBoundingClientRect().top;
                    const anchorTop = anchor.getBoundingClientRect().top;
                    wrap.scrollTop += anchorTop - wrapTop - saved.tableMissionOffset;
                }
            } else if (anchor && Number.isFinite(saved.windowMissionOffset)) {
                const anchorTop = anchor.getBoundingClientRect().top;
                window.scrollTo({
                    top: Math.max(0, window.scrollY + anchorTop - saved.windowMissionOffset),
                    left: Number.isFinite(saved.x) ? saved.x : 0,
                    behavior: "auto",
                });
            }
        };

        const restoreSession = () => {
            try {
                const saved = JSON.parse(window.sessionStorage.getItem(storageKey) || "null");
                if (!saved || saved.path !== window.location.pathname) return;
                // Browser restoration is manual on Dispatch. Restore immediately
                // before paint, then once more on the next frame for final layout.
                applyRestore(saved);
                window.requestAnimationFrame(() => {
                    applyRestore(saved);
                    window.sessionStorage.removeItem(storageKey);
                });
            } catch (_error) {
                // Scroll restoration must never interfere with Dispatch actions.
            }
        };

        restoreSession();
        return {
            snapshot,
            restoreSnapshot(saved) {
                applyRestore(saved);
                window.requestAnimationFrame(() => applyRestore(saved));
            },
            preserve() {
                try {
                    window.sessionStorage.setItem(storageKey, JSON.stringify(snapshot()));
                } catch (_error) {
                    // Browser storage is an enhancement only.
                }
            },
        };
    }

    function initializeDispatchSelects(scope) {
        const selects = Array.from(scope.querySelectorAll(
            ".neoscorpion-dispatch-primary-row select.neoscorpion-inline-select"
        ));
        const close = (combobox, returnFocus = false) => {
            if (!combobox) return;
            combobox.classList.remove("is-open");
            combobox.trigger.setAttribute("aria-expanded", "false");
            if (returnFocus) combobox.trigger.focus();
            if (scope._dispatchOpenCombobox === combobox) scope._dispatchOpenCombobox = null;
        };
        const open = (combobox) => {
            const current = scope._dispatchOpenCombobox || null;
            if (current && current !== combobox) close(current);
            combobox.classList.add("is-open");
            combobox.trigger.setAttribute("aria-expanded", "true");
            scope._dispatchOpenCombobox = combobox;
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
        if (scope.dataset.dispatchComboboxGlobalReady !== "true") {
            scope.dataset.dispatchComboboxGlobalReady = "true";
            document.addEventListener("click", (event) => {
                const current = scope._dispatchOpenCombobox || null;
                if (current && !current.contains(event.target)) close(current);
            });
            document.addEventListener("keydown", (event) => {
                const current = scope._dispatchOpenCombobox || null;
                if (event.key === "Escape" && current) {
                    event.preventDefault();
                    close(current, true);
                }
            });
        }
    }


    function initializeDispatchApuEditors(scope) {
        scope.querySelectorAll("[data-dispatch-apu-editor]").forEach((editor) => {
            if (editor.dataset.dispatchApuReady === "true") return;
            editor.dataset.dispatchApuReady = "true";
            const allowance = editor.querySelector("[data-dispatch-apu-override-value]");
            const enabled = editor.querySelector("[data-dispatch-apu-override-enabled]");
            editor.addEventListener("toggle", () => {
                if (!editor.open) return;
                if (allowance) allowance.dataset.originalValue = allowance.value;
                if (enabled) enabled.dataset.originalValue = enabled.value;
            });
        });
    }

    const pollIntervalMs = Number(root.dataset.refreshIntervalMs || 0);
    const revisionUrl = root.dataset.revisionUrl;
    const panelUrl = root.dataset.panelUrl;
    const autosaveUrl = root.dataset.autosaveUrl;
    const spearActionUrl = root.dataset.spearActionUrl;
    const spearRecalculationMs = Math.max(
        60000,
        Number(root.dataset.spearRecalculationMs || 120000)
    );
    let spearRenderedAt = Date.now();
    let operationId = root.dataset.operationId || "none";
    let revision = Number(root.dataset.revision || 0);
    const initialControlValues = new WeakMap();
    let reloading = false;
    let lifecycleSaving = false;
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
        + "select[name='assigned_truck_id']:not([disabled]), [data-cycle-start] input:not([type=hidden])"
    ));

    const resetProtectedBaselines = () => {
        protectedControls().forEach((control) => {
            initialControlValues.set(control, controlValue(control));
        });
    };
    resetProtectedBaselines();

    const hasUnsavedAutosave = () => Array.from(
        root.querySelectorAll("[data-dispatch-autosave]")
    ).some((input) => (
        input.dataset.autosaveSaving === "true"
        || input.value.trim() !== (input.dataset.savedValue || "")
    ));

    const hasUnsavedControls = () => (
        lifecycleSaving ||
        protectedControls().some(
            (control) => initialControlValues.get(control) !== controlValue(control)
        )
        || hasUnsavedAutosave()
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

    let pendingFuelDataRefresh = false;

    const reloadPage = async () => {
        if (window.NeoScorpionFuelData?.isOpen()) {
            pendingFuelDataRefresh = true;
            return false;
        }
        if (reloading || hasUnsavedControls() || !panelUrl) return false;
        reloading = true;
        const saved = dispatchScroll.snapshot();
        try {
            const response = await fetch(panelUrl, {
                cache: "no-store",
                credentials: "same-origin",
                headers: {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok !== true || !payload.html) {
                throw new Error(payload.error || "Fuel Dispatch refresh failed.");
            }
            // Input may have become dirty while the network request was in
            // flight. Never replace live operator typing with fetched markup.
            if (hasUnsavedControls()) {
                pendingFuelDataRefresh = true;
                return false;
            }
            const holder = document.createElement("template");
            holder.innerHTML = payload.html.trim();
            const nextPanel = holder.content.firstElementChild;
            const currentPanel = root.querySelector(":scope > .neoscorpion-panel");
            if (!nextPanel?.matches(".neoscorpion-panel") || !currentPanel) {
                throw new Error("Fuel Dispatch refresh returned invalid content.");
            }
            currentPanel.replaceWith(nextPanel);
            updateAssignmentAlerts(root);
            adoptFingerprint(payload);
            root.dataset.spearPlanToken = payload.spear_plan_token || "";
            root.dataset.spearAutomationEnabled = payload.spear_automation_enabled ? "true" : "false";
            spearRenderedAt = Date.now();
            initializeDispatchSelects(root);
            initializeDispatchApuEditors(root);
            resetProtectedBaselines();
            syncDirtyState();
            dispatchScroll.restoreSnapshot(saved);
            pendingFuelDataRefresh = false;
            scheduleSpearAutomation();
            return true;
        } catch (error) {
            const status = root.querySelector("[data-spear-fleet-status]");
            setStatus(status, `LIVE REFRESH: ${error.message || "Unable to refresh Dispatch."}`, "error");
            return false;
        } finally {
            reloading = false;
        }
    };

    document.addEventListener("neoscorpion:fuel-data-closed", () => {
        if (pendingFuelDataRefresh && !hasUnsavedControls()) reloadPage();
    });

    const handleChangedFingerprint = () => {
        if (!hasUnsavedControls()) return reloadPage();
        return Promise.resolve(false);
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
            window.NeoScorpionFuelData?.revisionChanged(nextRevision, nextOperationId);
            await handleChangedFingerprint();
        } else if (
            root.dataset.spearRecommendationsEnabled === "true"
            && Date.now() - spearRenderedAt >= spearRecalculationMs
            && !hasUnsavedControls()
        ) {
            await reloadPage();
        }
    };

    const autosaveField = async (input) => {
        if (!autosaveUrl || input.dataset.autosaveSaving === "true") {
            return false;
        }
        const submittedValue = input.value.trim();
        const savedValue = input.dataset.savedValue || "";
        if (submittedValue === savedValue) {
            input.dataset.autosaveFailed = "false";
            syncDirtyState();
            return true;
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
            return true;
        } catch (error) {
            input.dataset.autosaveFailed = "true";
            setStatus(
                status,
                `Save Failed: ${error.message || "Unable to save this field."}`,
                "error"
            );
            return false;
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
        const editingApu = Boolean(button.closest("[data-dispatch-apu-editor]"));
        const apuEnabled = form.elements.namedItem("apu_override_enabled");
        const apuAllowance = form.elements.namedItem("apu_override_allowance");
        if (resetApu) {
            if (apuEnabled) apuEnabled.value = "0";
            if (apuAllowance) apuAllowance.value = "";
        } else if (apuEnabled && editingApu) {
            apuEnabled.value = "1";
        }
        const expectedFuelerBefore = String(
            form.elements.namedItem("expected_assigned_fueler_user_id")?.value || ""
        );
        const expectedTruckBefore = String(
            form.elements.namedItem("expected_assigned_truck_id")?.value || ""
        );
        const requestedFuelerBefore = String(
            form.elements.namedItem("assigned_fueler_user_id")?.value || ""
        );
        const requestedTruckBefore = String(
            form.elements.namedItem("assigned_truck_id")?.value || ""
        );
        const resourceChangeRequested = (
            expectedFuelerBefore !== requestedFuelerBefore
            || expectedTruckBefore !== requestedTruckBefore
        );

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
            if (payload.changed && resourceChangeRequested) {
                await reloadPage();
                return;
            }
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

    const submitLifecycleAction = async (form, button) => {
        if (lifecycleSaving) return;
        const status = form.querySelector("[data-lifecycle-status]");
        if (!window.confirm(form.dataset.confirm)) return;
        lifecycleSaving = true;
        if (button) button.disabled = true;
        setStatus(status, "Saving...");
        syncDirtyState();
        try {
            const response = await fetch(form.getAttribute("action"), {
                method: "POST", body: new FormData(form), cache: "no-store", credentials: "same-origin",
                headers: {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok !== true) throw new Error(payload.error || "Action failed.");
            lifecycleSaving = false;
            setStatus(status, payload.changed ? "Saved" : "No change");
            // The existing panel refresh preserves row/details/scroll and defers
            // while another field or the shared Fueler Data modal is in use.
            pendingFuelDataRefresh = true;
            await reloadPage();
        } catch (error) {
            setStatus(status, `Save Failed: ${error.message || "Unable to save."}`, "error");
        } finally {
            lifecycleSaving = false;
            if (button) button.disabled = false;
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
            await reloadPage();
        } catch (error) {
            form.dataset.truckCardBusy = "false";
            if (button) button.disabled = false;
            setStatus(status, `Save Failed: ${error.message || "Unable to update this truck."}`, "error");
        }
    };

    const focusNextAutosaveField = (input) => {
        const fieldName = input.dataset.autosaveField || "";
        if (!fieldName) return false;
        const fields = Array.from(root.querySelectorAll(
            `[data-dispatch-autosave][data-autosave-field="${fieldName}"]`
        )).filter((field) => !field.disabled);
        const index = fields.indexOf(input);
        if (index < 0 || index + 1 >= fields.length) return false;
        const next = fields[index + 1];
        next.focus({preventScroll: true});
        if (typeof next.select === "function") next.select();
        next.scrollIntoView({block: "nearest", inline: "nearest"});
        return true;
    };

    root.addEventListener("keydown", async (event) => {
        const input = event.target.closest?.("[data-dispatch-autosave]");
        if (!input || event.key !== "Enter" || event.shiftKey || event.isComposing) return;
        event.preventDefault();
        const saved = await autosaveField(input);
        if (saved) focusNextAutosaveField(input);
    });

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
        const lifecycleForm = event.target.closest("[data-dispatch-lifecycle-form]");
        if (lifecycleForm) {
            event.preventDefault();
            submitLifecycleAction(lifecycleForm, event.submitter || lifecycleForm.querySelector("button[type='submit']"));
            return;
        }
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
    syncDirtyState();
    let spearAutomationTimer = null;
    function scheduleSpearAutomation() {
        if (spearAutomationTimer !== null) {
            window.clearTimeout(spearAutomationTimer);
            spearAutomationTimer = null;
        }
        if (
            root.dataset.spearAutomationEnabled !== "true"
            || !spearActionUrl
            || !root.dataset.spearPlanToken
        ) return;
        const delay = Math.max(1000, Number(root.dataset.spearStabilityDelayMs || 5000));
        spearAutomationTimer = window.setTimeout(async () => {
            spearAutomationTimer = null;
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
                root.dataset.liveDirty = "false";
                await reloadPage();
            } catch (error) {
                root.dataset.liveDirty = "false";
                const status = root.querySelector("[data-spear-fleet-status]");
                setStatus(status, `SPEAR: ${error.message}`, "error");
            }
        }, delay);
    }
    scheduleSpearAutomation();
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
