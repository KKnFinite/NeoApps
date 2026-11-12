(() => {
    "use strict";

    const root = document.querySelector("[data-subzero-ucc]");
    if (!root) return;
    root.dataset.uccInlineReady = 'true';

    let forms = Array.from(root.querySelectorAll("[data-ucc-assignment-form]"));
    const dialog = root.querySelector("[data-ucc-move-dialog]");
    let pendingForm = null;

    const weather = root.querySelector(".neosubzero-weather");

    // Presentation only: the same server-rendered physical ramp is used at
    // every width. Nothing here predicts movement or changes operational data.
    let mobileView = root.dataset.uccMobileView === "ramp" ? "ramp" : "staffing";
    let mobileRamp = root.dataset.uccSelectedRamp || "Alpha";
    const applyMobileView = ({persist = false} = {}) => {
        const ramps = Array.from(root.querySelectorAll("[data-ucc-ramp]"));
        if (ramps.length && !ramps.some((ramp) => ramp.dataset.uccRamp === mobileRamp)) {
            mobileRamp = ramps[0].dataset.uccRamp;
        }
        root.dataset.uccMobileView = mobileView;
        root.dataset.uccSelectedRamp = mobileRamp;
        ramps.forEach((ramp) => ramp.classList.toggle("is-mobile-selected", ramp.dataset.uccRamp === mobileRamp));
        root.querySelectorAll("[data-ucc-view]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.uccView === mobileView)));
        root.querySelectorAll("[data-ucc-ramp-select]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.uccRampSelect === mobileRamp)));
        const back = root.querySelector("[data-ucc-back-coordinator]");
        if (back) {
            const url = new URL(back.href, window.location.origin);
            url.searchParams.set("ramp", mobileRamp);
            back.href = url.href;
        }
        if (persist) {
            // Only view selectors enter the URL; no personnel, payload or draft.
            const url = new URL(window.location.href);
            url.searchParams.set("view", mobileView);
            url.searchParams.set("ramp", mobileRamp);
            window.history.replaceState(null, "", url);
        }
    };
    applyMobileView();

    const frostExplanationToggle = weather?.querySelector("[data-frost-explanation-toggle]");
    const applyFrostExplanations = (enabled) => {
        if (!weather || !frostExplanationToggle) return;
        weather.dataset.frostExplanations = enabled ? "on" : "off";
        frostExplanationToggle.setAttribute("aria-pressed", enabled ? "true" : "false");
        frostExplanationToggle.textContent = `WHY ${enabled ? "ON" : "OFF"}`;
        weather.querySelectorAll(".is-frost-reason").forEach((element) => {
            element.hidden = !enabled;
        });
    };
    let frostExplanationsEnabled = weather?.dataset.frostExplanations !== "off";
    applyFrostExplanations(frostExplanationsEnabled);
    frostExplanationToggle?.addEventListener("click", async () => {
        const previous = frostExplanationsEnabled;
        const requested = !previous;
        frostExplanationToggle.disabled = true;
        applyFrostExplanations(requested);
        try {
            const response = await fetch(weather.dataset.frostExplanationPreferenceUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({enabled: requested}),
            });
            const payload = await response.json();
            if (!response.ok || payload.ok !== true) throw new Error(payload.error || "Save failed");
            frostExplanationsEnabled = payload.enabled === true;
            applyFrostExplanations(frostExplanationsEnabled);
        } catch (_error) {
            frostExplanationsEnabled = previous;
            applyFrostExplanations(previous);
            frostExplanationToggle.title = "Frost explanation preference could not be saved.";
        } finally {
            frostExplanationToggle.disabled = false;
        }
    });

    // TV mode is deliberately device-local: it changes only this display's
    // presentation and must never affect another operator's UCC workspace.
    const tvEnter = root.querySelector("[data-ucc-tv-enter]");
    const tvExit = root.querySelector("[data-ucc-tv-exit]");
    const tvStorageKey = "neoapps.neosubzero.ucc.tv-mode";
    const tvDesktop = window.matchMedia("(min-width:901px)");
    const cursorIdleMs = 3000;
    let tvMode = false;
    let cursorTimer = null;

    const readTvMode = () => {
        try {
            return window.localStorage.getItem(tvStorageKey) === "1";
        } catch (_error) {
            return false;
        }
    };
    const persistTvMode = (enabled) => {
        try {
            if (enabled) window.localStorage.setItem(tvStorageKey, "1");
            else window.localStorage.removeItem(tvStorageKey);
        } catch (_error) {
            // TV mode still works for this page when device storage is unavailable.
        }
    };
    const clearCursorTimer = () => {
        if (cursorTimer) window.clearTimeout(cursorTimer);
        cursorTimer = null;
    };
    const showCursor = () => document.body.classList.remove("neosubzero-ucc-tv-cursor-hidden");
    const scheduleCursorHide = () => {
        clearCursorTimer();
        showCursor();
        if (!tvMode) return;
        cursorTimer = window.setTimeout(() => {
            if (tvMode) document.body.classList.add("neosubzero-ucc-tv-cursor-hidden");
        }, cursorIdleMs);
    };
    const applyTvMode = (enabled, {persist = true} = {}) => {
        tvMode = enabled;
        root.classList.toggle("is-tv-mode", enabled);
        document.body.classList.toggle("neosubzero-ucc-tv-mode", enabled);
        tvEnter?.toggleAttribute("hidden", enabled);
        tvExit?.toggleAttribute("hidden", !enabled);
        if (persist) persistTvMode(enabled);
        if (enabled) scheduleCursorHide();
        else {
            clearCursorTimer();
            showCursor();
        }
    };
    const requestTvFullscreen = () => {
        const fullscreenTarget = document.documentElement;
        if (typeof fullscreenTarget.requestFullscreen !== "function") return;
        fullscreenTarget.requestFullscreen().catch(() => {
            // Browser fullscreen is optional; retain the app-level wall-board.
        });
    };
    tvEnter?.addEventListener("click", () => {
        if (!tvDesktop.matches) return;
        applyTvMode(true);
        requestTvFullscreen();
    });
    tvExit?.addEventListener("click", () => {
        applyTvMode(false);
        if (document.fullscreenElement && typeof document.exitFullscreen === "function") {
            document.exitFullscreen().catch(() => {});
        }
    });
    document.addEventListener("fullscreenchange", () => {
        // Esc/browser exit from the user-initiated native fullscreen restores
        // the ordinary UCC chrome as well.
        if (tvMode && !document.fullscreenElement) applyTvMode(false);
    });
    ["pointermove", "pointerdown", "touchstart", "keydown"].forEach((eventName) => {
        document.addEventListener(eventName, () => {
            if (tvMode) scheduleCursorHide();
        }, {passive: eventName !== "keydown"});
    });
    if (tvDesktop.matches && readTvMode()) applyTvMode(true, {persist: false});
    tvDesktop.addEventListener?.("change", () => {
        if (!tvDesktop.matches && tvMode) applyTvMode(false, {persist: false});
    });

    const field = (form, name) => form.querySelector(`[name="${name}"]`);
    const normalDesktop = () => tvDesktop.matches && !tvMode && !document.body.classList.contains("operational-board-view");
    const weatherToggle = root.querySelector("[data-ucc-weather-toggle]");
    const weatherStorageKey = `neoapps.neosubzero.ucc.weather-collapsed.${weatherToggle?.dataset.uccWeatherToggle}`;
    const setWeatherCollapsed = (collapsed) => {
        root.dataset.uccWeatherCollapsed = String(collapsed);
        weatherToggle.setAttribute("aria-expanded", String(!collapsed));
        weatherToggle.textContent = collapsed ? "Expand" : "Collapse";
    };
    if (weatherToggle) {
        try { setWeatherCollapsed(localStorage.getItem(weatherStorageKey) === "1"); }
        catch (_error) { setWeatherCollapsed(false); }
        weatherToggle.closest("[data-ucc-weather-heading]").hidden = false;
        weatherToggle.addEventListener("click", () => {
            const collapsed = weatherToggle.getAttribute("aria-expanded") === "true";
            setWeatherCollapsed(collapsed);
            try { localStorage.setItem(weatherStorageKey, collapsed ? "1" : "0"); }
            catch (_error) { /* The control still works when browser storage is blocked. */ }
        });
    }

    function updateSelectedText(form, select) {
        const selected = select.selectedOptions[0];
        const text = form.querySelector('[data-ucc-selected-text]');
        const unavailable = Boolean(selected?.disabled);
        const unavailableClass = select.name === "truck_number" ? "ucc-truck-unavailable" : "ucc-person-unavailable";
        if (text) {
            text.textContent = selected?.textContent || '';
            text.classList.toggle(unavailableClass, unavailable);
        }
    }

    let crewEditor = null;
    let crewTrigger = null;
    let crewSaving = false;
    let savedCrewForm = null;
    let crewFocusId = "";
    const crewTeam = key => root.querySelector(`.ucc-team[data-ucc-editor="${CSS.escape(key)}"]`);
    const positionCrew = () => {
        if (!crewEditor) return;
        const anchor = crewTrigger.getBoundingClientRect();
        const {width, height} = crewEditor.getBoundingClientRect();
        const gap = 8;
        const left = crewTrigger.closest(".is-mirrored") ? anchor.right - width : anchor.left;
        const below = anchor.bottom + gap;
        const above = anchor.top - height - gap;
        crewEditor.style.left = `${Math.max(gap, Math.min(left, document.documentElement.clientWidth - width - gap))}px`;
        crewEditor.style.top = `${Math.max(gap, Math.min(below + height <= innerHeight - gap ? below : above, innerHeight - height - gap))}px`;
    };
    function closeCrew({restoreFocus = true, force = false} = {}) {
        if (!crewEditor || (crewSaving && !force)) return;
        const trigger = crewTrigger;
        crewEditor.hidePopover();
        crewEditor.removeAttribute("popover");
        crewEditor.removeAttribute("role");
        crewEditor.removeAttribute("aria-labelledby");
        trigger.setAttribute("aria-expanded", "false");
        crewEditor = crewTrigger = null;
        if (restoreFocus && trigger.isConnected) trigger.focus({preventScroll: true});
    }
    function openCrew(team) {
        if (!normalDesktop() || !team) return;
        const editor = team.querySelector(".ucc-team-controls");
        if (!editor || crewEditor === editor) return;
        closeCrew({restoreFocus: false, force: true});
        crewEditor = editor;
        crewTrigger = team.querySelector("[data-ucc-crew-edit]");
        editor.setAttribute("popover", "manual");
        editor.setAttribute("role", "dialog");
        editor.setAttribute("aria-labelledby", `${editor.id}-title`);
        crewTrigger.setAttribute("aria-expanded", "true");
        // Start inside the viewport so native popover autofocus cannot scroll
        // to the editor's former in-flow position before it is anchored.
        editor.style.left = editor.style.top = "8px";
        const {scrollX, scrollY} = window;
        editor.showPopover();
        if (window.scrollX !== scrollX || window.scrollY !== scrollY) {
            window.scrollTo({left: scrollX, top: scrollY, behavior: "instant"});
        }
        positionCrew();
        (editor.querySelector(crewFocusId ? `#${CSS.escape(crewFocusId)}` : 'select:enabled')
            || editor.querySelector("[data-ucc-crew-close]")).focus({preventScroll: true});
    }
    const setMoveChoices = (button, enabled) => {
        button.setAttribute("aria-pressed", String(enabled));
        button.closest(".ucc-team").querySelectorAll("option[data-ucc-assigned-elsewhere]").forEach(option => {
            option.hidden = !enabled;
        });
    };
    if (typeof root.showPopover === "function") {
        root.dataset.uccCrewEditor = "true";
        window.addEventListener("scroll", positionCrew, {passive: true});
        window.addEventListener("resize", positionCrew);
        tvDesktop.addEventListener("change", () => closeCrew({restoreFocus: false, force: true}));
        tvEnter?.addEventListener("click", () => closeCrew({restoreFocus: false, force: true}));
        document.addEventListener("neo:board-enter", () => closeCrew({restoreFocus: false, force: true}));
        root.addEventListener("keydown", event => {
            if (event.key === "Escape" && crewEditor && !dialog?.open) {
                event.preventDefault();
                closeCrew();
            }
        });
        root.addEventListener("toggle", event => {
            if (event.target.matches(".ucc-team-controls details")) positionCrew();
        }, true);
    }

    let lastStaffingFeedback;
    const staffingOutput = slot => root.querySelector(`[data-ucc-editor="${CSS.escape(slot)}"] [data-ucc-management-save]`);
    function paintStaffingFeedback() {
        if (!lastStaffingFeedback) return;
        const {slot, state, message} = lastStaffingFeedback;
        const output = staffingOutput(slot);
        if (!output) return;
        output.dataset.state = state;
        output.textContent = message || {saving: "Saving…", saved: "Saved", error: "Not saved"}[state];
    }
    function setStaffingFeedback(form, state, message = "") {
        const slot = form.closest(".ucc-team")?.dataset?.uccEditor;
        if (!slot) return false;
        if (lastStaffingFeedback && lastStaffingFeedback.slot !== slot) {
            const previous = staffingOutput(lastStaffingFeedback.slot);
            if (previous) previous.textContent = "";
        }
        lastStaffingFeedback = {slot, state, message};
        paintStaffingFeedback();
        positionCrew();
        return true;
    }

    const sourceFormFor = (personId, destinationForm) => {
        const scope = destinationForm.dataset.assignmentScope || "regular";
        const visible = forms.find((form) => form !== destinationForm && form.dataset.currentPersonId === personId
            && (form.dataset.assignmentScope || "regular") === scope);
        if (visible) return visible;
        // An empty Remote ramp is not drawn, but its canonical staffing may
        // still exist. Never mistake an undisplayed assignment for a free person.
        const index = JSON.parse(root.querySelector('[data-ucc-assignment-index]')?.textContent || '{}')[scope] || {};
        return index[personId] ? {dataset:index[personId]} : null;
    };

    const setSourceFields = (form, sourceForm) => {
        field(form, "source_assignment_id").value = sourceForm?.dataset.assignmentId || "";
        field(form, "source_expected_version").value = sourceForm?.dataset.assignmentVersion || "";
    };

    const resetMove = (form, {restoreSelection = false} = {}) => {
        if (!form) return;
        if (restoreSelection) {
            const select = field(form, "person_id");
            select.value = form.dataset.currentPersonId || "";
            if (normalDesktop()) updateSelectedText(form, select);
            form.dataset.dirty = "false";
        }
        field(form, "source_assignment_id").value = "";
        field(form, "source_expected_version").value = "";
        field(form, "move_resolution").value = "";
        delete form.dataset.moveConfirmed;
    };

    const updateDirtyState = () => {
        const dirty = Array.from(root.querySelectorAll("[data-ucc-live-board] form")).some((form) => form.dataset.dirty === "true");
        if (dirty) root.dataset.dirty = "true";
        else delete root.dataset.dirty;
    };

    const openOccupiedDialog = (form, sourceForm) => {
        pendingForm = form;
        setSourceFields(form, sourceForm);
        const occupant = dialog?.querySelector("[data-ucc-move-person]");
        if (occupant) occupant.textContent = form.dataset.currentPersonName || "This employee";
        const swap = dialog?.querySelector('[data-ucc-move-choice="swap"]');
        if (swap) {
            swap.disabled = !sourceForm;
            swap.title = sourceForm ? "Exchange both employees' slots" : "Swap requires an existing source slot";
        }
        if (typeof dialog?.showModal === "function") dialog.showModal();
    };

    const prepareMove = (form) => {
        const personId = field(form, "person_id")?.value || "";
        const currentPersonId = form.dataset.currentPersonId || "";
        if (!personId || personId === currentPersonId) {
            setSourceFields(form, null);
            return {personId, sourceForm: null, occupied: false};
        }
        const sourceForm = sourceFormFor(personId, form);
        setSourceFields(form, sourceForm);
        return {personId, sourceForm, occupied: Boolean(currentPersonId)};
    };

    function bindDropTarget(target, getForm, compact = false) {
        target.addEventListener("dragover", (event) => {
            if (!event.dataTransfer?.types.includes("text/plain")) return;
            event.preventDefault();
            target.classList.add("is-drop-target");
        });
        target.addEventListener("dragleave", () => target.classList.remove("is-drop-target"));
        target.addEventListener("drop", (event) => {
            event.preventDefault();
            target.classList.remove("is-drop-target");
            if (crewSaving) return;
            const form = getForm();
            const personId = String(event.dataTransfer?.getData("text/plain") || "");
            const select = field(form, "person_id");
            if (!personId || !Array.from(select.options).some(option => option.value === personId)) return;
            if (compact) openCrew(form.closest(".ucc-team"));
            select.value = personId;
            if (normalDesktop()) updateSelectedText(form, select);
            form.dataset.dirty = "true";
            updateDirtyState();
            const move = prepareMove(form);
            if (move.occupied && move.personId !== form.dataset.currentPersonId) {
                openOccupiedDialog(form, move.sourceForm);
                return;
            }
            form.dataset.moveConfirmed = "true";
            form.requestSubmit();
        });
    }

    const bindBoard = () => {
    forms = Array.from(root.querySelectorAll("[data-ucc-assignment-form]"));
    root.querySelectorAll("[data-ucc-live-board] form").forEach((form) => {
        if (form.dataset.uccChangeBound) return;
        form.dataset.uccChangeBound = "true";
        const changed = () => {
            form.dataset.dirty = Array.from(form.querySelectorAll('input:not([type="hidden"]), select')).some((input) => input.value !== (input.tagName === "SELECT" ? Array.from(input.options).find((option) => option.defaultSelected)?.value || input.options[0]?.value || "" : input.defaultValue)) ? "true" : "false";
            updateDirtyState();
        };
        form.addEventListener("input", changed);
        form.addEventListener("change", changed);
    });
    forms.forEach((form) => {
        if (form.dataset.uccAssignmentBound) return;
        form.dataset.uccAssignmentBound = "true";
        form.addEventListener("change", () => {
            resetMove(form);
            updateDirtyState();
        });
        form.addEventListener("submit", (event) => {
            if (form.dataset.moveConfirmed === "true") {
                return;
            }
            const move = prepareMove(form);
            if (move.occupied && move.personId) {
                event.preventDefault();
                openOccupiedDialog(form, move.sourceForm);
                return;
            }
        });
        bindDropTarget(form, () => form);
    });
    root.querySelectorAll("[data-ucc-crew-role]").forEach(target => {
        if (target.dataset.uccDropBound) return;
        target.dataset.uccDropBound = "true";
        bindDropTarget(target, () => {
            const team = target.closest(".ucc-team");
            return team.querySelector(`[data-ucc-crew-form="${target.dataset.uccCrewRole}"]`);
        }, true);
    });
    root.querySelectorAll('[data-ucc-auto-save]').forEach((form) => {
        if (form.dataset.autoSaveBound) return;
        form.dataset.autoSaveBound = 'true';
        form.addEventListener('change', () => {
            const select = form.querySelector('select');
            updateSelectedText(form, select);
            form.requestSubmit();
        });
    });

    root.querySelectorAll("[data-ucc-drag-assignee]").forEach((handle) => {
        if (handle.dataset.uccDragBound) return;
        handle.dataset.uccDragBound = "true";
        handle.addEventListener("dragstart", (event) => {
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", handle.dataset.personId || "");
            root.classList.add("is-dragging-assignee");
        });
        handle.addEventListener("dragend", () => {
            root.classList.remove("is-dragging-assignee");
            root.querySelectorAll(".is-drop-target").forEach(target => target.classList.remove("is-drop-target"));
        });
    });
    };
    bindBoard();

    dialog?.querySelectorAll("[data-ucc-move-choice]").forEach((button) => {
        button.addEventListener("click", () => {
            const choice = button.dataset.uccMoveChoice;
            const form = pendingForm;
            pendingForm = null;
            if (choice === "cancel") {
                resetMove(form, {restoreSelection: true});
                updateDirtyState();
                dialog.close();
                return;
            }
            field(form, "move_resolution").value = choice;
            form.dataset.moveConfirmed = "true";
            dialog.close();
            form.requestSubmit();
        });
    });

    dialog?.addEventListener("cancel", (event) => {
        event.preventDefault();
        const form = pendingForm;
        pendingForm = null;
        resetMove(form, {restoreSelection: true});
        updateDirtyState();
        dialog.close();
    });

    let selectedMission = "";
    const showAircraft = (missionId) => {
        selectedMission = missionId || "";
        root.querySelectorAll("[data-ucc-info-mission]").forEach((panel) => { panel.hidden = panel.dataset.uccInfoMission !== selectedMission; });
        const empty = root.querySelector("[data-ucc-info-empty]");
        if (empty) empty.hidden = Boolean(root.querySelector(`[data-ucc-info-mission="${CSS.escape(selectedMission)}"]`));
        const info = root.querySelector("[data-ucc-info]");
        if (info) info.hidden = !root.querySelector(`[data-ucc-info-mission="${CSS.escape(selectedMission)}"]`);
        root.querySelectorAll("[data-ucc-aircraft]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.uccAircraft === selectedMission)));
    };
    root.addEventListener("click", (event) => {
        const edit = event.target.closest("[data-ucc-crew-edit]");
        if (edit) {
            if (!crewSaving) openCrew(edit.closest(".ucc-team"));
            return;
        }
        if (event.target.closest("[data-ucc-crew-close]")) {
            closeCrew();
            return;
        }
        const move = event.target.closest("[data-ucc-enable-moves]");
        if (move) {
            setMoveChoices(move, move.getAttribute("aria-pressed") !== "true");
            return;
        }
        const view = event.target.closest("[data-ucc-view]");
        const ramp = event.target.closest("[data-ucc-ramp-select]");
        if (view || ramp) {
            if (view) mobileView = view.dataset.uccView;
            if (ramp) {
                mobileRamp = ramp.dataset.uccRampSelect;
                showAircraft("");
            }
            applyMobileView({persist: true});
            return;
        }
        const plane = event.target.closest("[data-ucc-aircraft]");
        if (plane) {
            showAircraft(plane.dataset.uccAircraft);
            if (normalDesktop()) root.querySelector('[data-ucc-info]:not([hidden])')?.scrollIntoView({block: "nearest"});
        }
        if (event.target.closest("[data-ucc-info-close]")) showAircraft("");
    });

    const feedback = root.querySelector("[data-ucc-feedback]");
    const currentLink = root.querySelector("[data-ucc-current-link]");
    const notify = (message, error = false, inline = false) => {
        feedback.hidden = false;
        feedback.textContent = message;
        feedback.classList.toggle("is-error", error);
        feedback.classList.toggle("is-inline", inline);
        currentLink.hidden = !error;
    };
    const editing = () => root.dataset.dirty === "true" || root.dataset.saving === "true" || Boolean(root.querySelector("input:focus, select:focus, textarea:focus")) || dialog?.open;
    let loading = null;
    let mutationEpoch = 0;
    let deferredRevision = null;
    const fetchBoard = async () => {
        const requestEpoch = mutationEpoch;
        try {
            const url = new URL(window.location.href);
            url.searchParams.set("fragment", "1");
            const response = await (window.NeoLiveRevision?.fetch || fetch)(url, {cache: "no-store", credentials: "same-origin"});
            if (!response.ok) throw new Error("Unable to refresh UCC. Your displayed state may be stale.");
            const documentNext = new DOMParser().parseFromString(await response.text(), "text/html");
            const next = documentNext.querySelector("[data-subzero-ucc]");
            if (!next) throw new Error("UCC session expired. Open current UCC to sign in.");
            if (requestEpoch !== mutationEpoch || root.dataset.saving === "true") return false;
            const deferred = editing();
            // The wallboard uses the saved staffing from this same response,
            // even when an open management draft prevents replacing its controls.
            if (tvMode) root.dispatchEvent(new CustomEvent("ucc:tv-source", {
                detail: deferred ? next.cloneNode(true) : null,
            }));
            const operationId = root.querySelector('[name="operation_id"]')?.value;
            const nextOperationId = next.querySelector('[name="operation_id"]')?.value;
            if (deferred && operationId !== nextOperationId) {
                deferredRevision = next.dataset.revision;
                notify("The current sort changed. Your draft has not been applied; open current UCC before continuing.", true);
                return false;
            }
            if (operationId !== nextOperationId) lastStaffingFeedback = null;
            const expanded = Array.from(root.querySelectorAll("[data-ucc-editor][open]")).map((el) => el.dataset.uccEditor);
            const scroll = window.scrollY;
            const sortLabel = root.querySelector('.neosubzero-ucc-head-actions > strong');
            if (sortLabel) sortLabel.textContent = next.querySelector('.neosubzero-ucc-head-actions > strong').textContent;
            if (deferred) {
                next.querySelectorAll(".ucc-team[data-ucc-editor]").forEach(team => {
                    const current = crewTeam(team.dataset.uccEditor);
                    if (!current) return;
                    const lines = team.querySelector(".ucc-crew-lines");
                    const currentLines = current.querySelector(".ucc-crew-lines");
                    if (lines && currentLines) {
                        // Keep the resting drop targets and their handlers in place.
                        Array.from(lines.children).forEach((line, index) => {
                            const currentLine = currentLines.children[index];
                            currentLine.textContent = line.textContent;
                            currentLine.className = line.className;
                            currentLine.title = line.title;
                        });
                    }
                    if (savedCrewForm?.closest(".ucc-team") === current && savedCrewForm.dataset.dirty !== "true") {
                        const fieldName = savedCrewForm.dataset.uccCrewForm;
                        const updated = team.querySelector(`[data-ucc-crew-form="${fieldName}"]`);
                        if (updated) {
                            savedCrewForm.replaceWith(updated);
                            const move = current.querySelector('[data-ucc-enable-moves][aria-pressed="true"]');
                            if (move) setMoveChoices(move, true);
                        }
                    }
                });
                root.querySelectorAll("[data-ucc-ramp]").forEach((ramp) => {
                    const newRamp = next.querySelector(`[data-ucc-ramp="${CSS.escape(ramp.dataset.uccRamp)}"]`);
                    ramp.dataset.uccRelevant = newRamp?.dataset.uccRelevant || 'false';
                    const replacement = newRamp?.querySelector(".ucc-ramp-diagram");
                    const diagram = ramp.querySelector(".ucc-ramp-diagram");
                    if (diagram && replacement) diagram.replaceWith(replacement);
                    else if (diagram) diagram.replaceChildren();
                    ramp.querySelectorAll(".ucc-queue-exception").forEach((element) => element.remove());
                    newRamp?.querySelectorAll(".ucc-queue-exception").forEach((element) => ramp.append(element));
                    const count = ramp.querySelector("header > span");
                    if (count) count.textContent = newRamp?.querySelector("header > span")?.textContent || "No current aircraft";
                });
                next.querySelectorAll("[data-ucc-ramp]").forEach((ramp) => {
                    if (!root.querySelector(`[data-ucc-ramp="${CSS.escape(ramp.dataset.uccRamp)}"]`)) root.querySelector(".ucc-ramp-board").append(ramp);
                });
                // Forecasts and unresolved locations must stay live even while
                // a staffing draft prevents replacement of the full board.
                const upcoming = root.querySelector('[data-ucc-upcoming]');
                const upcomingNext = next.querySelector('[data-ucc-upcoming]');
                if (upcoming && upcomingNext) upcoming.replaceWith(upcomingNext);
                const locations = root.querySelector('[data-ucc-location-exceptions]');
                const locationsNext = next.querySelector('[data-ucc-location-exceptions]');
                if (locations && locationsNext) locations.replaceWith(locationsNext);
                else if (locations) locations.remove();
                else if (locationsNext) root.querySelector('.ucc-ramp-board').before(locationsNext);
                root.querySelector("[data-ucc-info]")?.replaceWith(next.querySelector("[data-ucc-info]"));
            } else {
                const team = crewEditor?.closest(".ucc-team");
                const crewKey = operationId === nextOperationId ? team?.dataset.uccEditor : null;
                const movesEnabled = Boolean(team?.querySelector('[data-ucc-enable-moves][aria-pressed="true"]'));
                closeCrew({restoreFocus: false, force: true});
                root.querySelector("[data-ucc-live-board]").replaceWith(next.querySelector("[data-ucc-live-board]"));
                expanded.forEach((key) => root.querySelector(`[data-ucc-editor="${CSS.escape(key)}"]`)?.setAttribute("open", ""));
                bindBoard();
                if (crewKey) {
                    const current = crewTeam(crewKey);
                    const move = current?.querySelector("[data-ucc-enable-moves]");
                    if (movesEnabled && move) setMoveChoices(move, true);
                    openCrew(current);
                }
            }
            if (deferred) bindBoard();
            paintStaffingFeedback();
            const newWeather = next.querySelector(".neosubzero-weather");
            if (weather && newWeather) {
                const hourlyScroll = weather.querySelector(".neosubzero-weather-current-sort")?.scrollLeft || 0;
                Array.from(weather.children).filter((el) => !el.matches("header, .neosubzero-weather-ambient")).forEach((el) => el.remove());
                Array.from(newWeather.children).filter((el) => !el.matches("header, .neosubzero-weather-ambient")).forEach((el) => weather.append(el));
                const hourly = weather.querySelector(".neosubzero-weather-current-sort");
                if (hourly) hourly.scrollLeft = hourlyScroll;
                weather.querySelectorAll(".is-frost-reason").forEach((element) => { element.hidden = !frostExplanationsEnabled; });
                const stamp = weather.querySelector(".neosubzero-weather-source > span");
                if (stamp) stamp.textContent = newWeather.querySelector(".neosubzero-weather-source > span").textContent;
            }
            applyMobileView();
            showAircraft(selectedMission);
            deferredRevision = deferred ? next.dataset.revision : null;
            if (!deferred) root.dataset.revision = next.dataset.revision;
            window.scrollTo({top: scroll, behavior: "instant"});
            return !deferred;
        } finally { loading = null; }
    };
    const refreshBoard = (afterSave = false) => {
        if (loading) return afterSave ? loading.catch(() => {}).then(() => refreshBoard()) : Promise.resolve(false);
        if (root.dataset.saving === "true") return Promise.resolve(false);
        loading = fetchBoard();
        return loading;
    };
    root.addEventListener("submit", async (event) => {
        const form = event.target;
        if (event.defaultPrevented || !form.closest("[data-ucc-live-board]")) return;
        event.preventDefault();
        if (root.dataset.saving === "true" || crewSaving) return;
        crewSaving = Boolean(crewEditor?.contains(form));
        crewFocusId = crewSaving ? document.activeElement?.id || form.querySelector("select")?.id || "" : "";
        mutationEpoch += 1;
        root.dataset.saving = "true";
        const buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
        const body = new FormData(form);
        const controls = Array.from(root.querySelectorAll('[data-ucc-live-board] :is(select,[data-ucc-crew-edit],[data-ucc-crew-close]):enabled'));
        if (crewSaving) controls.push(...document.querySelectorAll('[data-ucc-tv-enter]:enabled,[data-operational-board-toggle]:enabled'));
        controls.forEach(control => { control.disabled = true; });
        if (event.submitter?.name) body.set(event.submitter.name, event.submitter.value);
        buttons.forEach((button) => { button.disabled = true; });
        notify("Saving UCC…", false, setStaffingFeedback(form, "saving"));
        const abort = new AbortController();
        const timeout = window.setTimeout(() => abort.abort(), 20000);
        let accepted = false;
        try {
            const response = await fetch(form.action, {method: "POST", body, credentials: "same-origin", headers: {"X-UCC-Request": "1"}, signal: abort.signal});
            const payload = await response.json().catch(() => null);
            if (!payload) throw new Error("No save confirmation was received. Check current UCC and your sign-in before retrying; your draft is preserved.");
            if (!response.ok || payload.ok !== true) throw new Error(payload.error || "Change not accepted. Review current UCC before retrying.");
            accepted = true;
            form.dataset.dirty = "false";
            form.querySelectorAll("input, select").forEach((input) => {
                if (input.tagName === "SELECT") Array.from(input.options).forEach((option) => { option.defaultSelected = option.selected; });
                else input.defaultValue = input.value;
            });
            updateDirtyState();
            const hasDraft = root.dataset.dirty === "true";
            notify(payload.message + (hasDraft ? " Other unsaved drafts are preserved; live display will reconcile after editing." : ""), false, setStaffingFeedback(form, "saved") && !hasDraft);
        } catch (error) {
            const message = error.name === "AbortError" ? "Request timed out; its outcome is unknown. Check current UCC before retrying. Your draft is preserved."
                : error.message || "Unable to save. Your draft is preserved; check current UCC before retrying.";
            setStaffingFeedback(form, "error", crewSaving ? message : "");
            notify(message, true);
        } finally {
            window.clearTimeout(timeout);
            delete root.dataset.saving;
            const restoreControls = () => [...buttons, ...controls].forEach(control => { control.disabled = false; });
            const reconcileFirst = accepted && normalDesktop();
            if (!reconcileFirst) restoreControls();
            if (accepted) {
                if (form.contains(document.activeElement)) document.activeElement.blur();
                if (crewSaving) savedCrewForm = form;
                try { await refreshBoard(true); } catch (error) { notify("Saved, but " + error.message, true); }
            }
            if (reconcileFirst) restoreControls();
            if (!accepted && crewFocusId && crewEditor) {
                crewEditor.querySelector(`#${CSS.escape(crewFocusId)}`)?.focus({preventScroll: true});
            }
            crewSaving = false;
            savedCrewForm = null;
            crewFocusId = "";
            positionCrew();
        }
    });

    const refreshIntervalMs = Number(root.dataset.refreshIntervalMs);
    if (
        !window.NeoLiveUpdates
        || !root.dataset.revisionUrl
        || !Number.isFinite(refreshIntervalMs)
        || refreshIntervalMs < 1000
    ) return;

    let revision = root.dataset.revision;
    const controller = window.NeoLiveUpdates.create({
        intervalMs: refreshIntervalMs,
        continuousWhileVisible: true,
        poll: async () => {
            const url = new URL(root.dataset.revisionUrl, window.location.origin);
            url.searchParams.set("revision", revision);
            const response = await (window.NeoLiveRevision?.fetch || fetch)(url, {cache: "no-store"});
            const payload = await response.json();
            controller.setServerStatus(payload.refresh || {});
            if (!response.ok || payload.ok !== true) throw new Error(payload.error || "UCC refresh failed");
            // Do not consume a revision while a draft/focused input defers it.
            // The same pending change must still refresh after editing ends.
            if (payload.revision !== root.dataset.revision) {
                // Aircraft and weather already reflect this revision. Keep the
                // staffing reconciliation pending without downloading it again.
                if (editing() && payload.revision === deferredRevision) return;
                if (await refreshBoard()) revision = root.dataset.revision;
            } else if (!editing()) revision = root.dataset.revision;
        },
    });
    controller.setServerStatus(JSON.parse(root.dataset.refreshStatus || "{}"));
})();
