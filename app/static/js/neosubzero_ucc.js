(() => {
    "use strict";

    const root = document.querySelector("[data-subzero-ucc]");
    if (!root) return;

    let forms = Array.from(root.querySelectorAll("[data-ucc-assignment-form]"));
    const dialog = root.querySelector("[data-ucc-move-dialog]");
    let pendingForm = null;

    const weather = root.querySelector(".neosubzero-weather[data-weather-preference-url]");
    const weatherMotionToggle = weather?.querySelector("[data-weather-motion-toggle]");
    const deviceReducesMotion = Boolean(
        window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    );
    const applyWeatherMotion = (enabled) => {
        if (!weather || !weatherMotionToggle) return;
        weather.dataset.weatherMotion = enabled ? "on" : "off";
        weather.dataset.weatherReducedMotion = deviceReducesMotion ? "true" : "false";
        weatherMotionToggle.setAttribute("aria-pressed", enabled ? "true" : "false");
        weatherMotionToggle.textContent = `AMBIENT MOTION ${enabled ? "ON" : "OFF"}${
            enabled && deviceReducesMotion ? " · DEVICE REDUCED" : ""
        }`;
    };
    let weatherMotionEnabled = weather?.dataset.weatherMotion !== "off";
    applyWeatherMotion(weatherMotionEnabled);
    weatherMotionToggle?.addEventListener("click", async () => {
        const previous = weatherMotionEnabled;
        const requested = !previous;
        weatherMotionToggle.disabled = true;
        delete weather.dataset.weatherMotionError;
        weatherMotionToggle.removeAttribute("title");
        applyWeatherMotion(requested);
        try {
            const response = await fetch(weather.dataset.weatherPreferenceUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({enabled: requested}),
            });
            const payload = await response.json();
            if (!response.ok || payload.ok !== true) throw new Error(payload.error || "Save failed");
            weatherMotionEnabled = payload.enabled === true;
            applyWeatherMotion(weatherMotionEnabled);
        } catch (_error) {
            weatherMotionEnabled = previous;
            applyWeatherMotion(previous);
            weather.dataset.weatherMotionError = "true";
            weatherMotionToggle.title = "Weather animation preference could not be saved.";
        } finally {
            weatherMotionToggle.disabled = false;
        }
    });

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
    if (readTvMode()) applyTvMode(true, {persist: false});

    const field = (form, name) => form.querySelector(`[name="${name}"]`);

    const sourceFormFor = (personId, destinationForm) => {
        const visible = forms.find((form) => form !== destinationForm && form.dataset.currentPersonId === personId);
        if (visible) return visible;
        // An empty Remote ramp is not drawn, but its canonical staffing may
        // still exist. Never mistake an undisplayed assignment for a free person.
        const index = JSON.parse(root.querySelector('[data-ucc-assignment-index]')?.textContent || '{}');
        return index[personId] ? {dataset:index[personId]} : null;
    };

    const setSourceFields = (form, sourceForm) => {
        field(form, "source_assignment_id").value = sourceForm?.dataset.assignmentId || "";
        field(form, "source_expected_version").value = sourceForm?.dataset.assignmentVersion || "";
    };

    const resetMove = (form, {restoreSelection = false} = {}) => {
        if (!form) return;
        if (restoreSelection) {
            field(form, "person_id").value = form.dataset.currentPersonId || "";
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
        form.addEventListener("dragover", (event) => {
            if (!event.dataTransfer?.types.includes("text/plain")) return;
            event.preventDefault();
            form.classList.add("is-drop-target");
        });
        form.addEventListener("dragleave", () => form.classList.remove("is-drop-target"));
        form.addEventListener("drop", (event) => {
            event.preventDefault();
            form.classList.remove("is-drop-target");
            const personId = String(event.dataTransfer?.getData("text/plain") || "");
            const select = field(form, "person_id");
            const hasPerson = select && Array.from(select.options).some(
                (option) => option.value === personId
            );
            if (!personId || !hasPerson) return;
            select.value = personId;
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
            forms.forEach((form) => form.classList.remove("is-drop-target"));
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
        const plane = event.target.closest("[data-ucc-aircraft]");
        if (plane) showAircraft(plane.dataset.uccAircraft);
        if (event.target.closest("[data-ucc-info-close]")) showAircraft("");
    });

    const feedback = root.querySelector("[data-ucc-feedback]");
    const currentLink = root.querySelector("[data-ucc-current-link]");
    const notify = (message, error = false) => {
        feedback.hidden = false;
        feedback.textContent = message;
        feedback.classList.toggle("is-error", error);
        currentLink.hidden = !error;
    };
    const editing = () => root.dataset.dirty === "true" || root.dataset.saving === "true" || Boolean(root.querySelector("input:focus, select:focus, textarea:focus")) || dialog?.open;
    let loading = false;
    let mutationEpoch = 0;
    const refreshBoard = async () => {
        if (loading || root.dataset.saving === "true") return false;
        loading = true;
        const requestEpoch = mutationEpoch;
        try {
            const response = await fetch(window.location.href, {cache: "no-store", credentials: "same-origin"});
            if (!response.ok) throw new Error("Unable to refresh UCC. Your displayed state may be stale.");
            const documentNext = new DOMParser().parseFromString(await response.text(), "text/html");
            const next = documentNext.querySelector("[data-subzero-ucc]");
            if (!next) throw new Error("UCC session expired. Open current UCC to sign in.");
            if (requestEpoch !== mutationEpoch || root.dataset.saving === "true") return false;
            const deferred = editing();
            const operationId = root.querySelector('[name="operation_id"]')?.value;
            const nextOperationId = next.querySelector('[name="operation_id"]')?.value;
            if (deferred && operationId !== nextOperationId) {
                notify("The current sort changed. Your draft has not been applied; open current UCC before continuing.", true);
                return false;
            }
            const expanded = Array.from(root.querySelectorAll("[data-ucc-editor][open]")).map((el) => el.dataset.uccEditor);
            const scroll = window.scrollY;
            const sortLabel = root.querySelector('.neosubzero-ucc-head-actions > strong');
            if (sortLabel) sortLabel.textContent = next.querySelector('.neosubzero-ucc-head-actions > strong').textContent;
            if (deferred) {
                root.querySelectorAll("[data-ucc-ramp]").forEach((ramp) => {
                    const newRamp = next.querySelector(`[data-ucc-ramp="${CSS.escape(ramp.dataset.uccRamp)}"]`);
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
                root.querySelector("[data-ucc-info]")?.replaceWith(next.querySelector("[data-ucc-info]"));
            } else {
                root.querySelector("[data-ucc-live-board]").replaceWith(next.querySelector("[data-ucc-live-board]"));
                expanded.forEach((key) => root.querySelector(`[data-ucc-editor="${CSS.escape(key)}"]`)?.setAttribute("open", ""));
                bindBoard();
            }
            if (deferred) bindBoard();
            const newWeather = next.querySelector(".neosubzero-weather");
            if (weather && newWeather) {
                const hourlyScroll = weather.querySelector(".neosubzero-weather-current-sort")?.scrollLeft || 0;
                Array.from(weather.children).filter((el) => !el.matches("header, .neosubzero-weather-ambient")).forEach((el) => el.remove());
                Array.from(newWeather.children).filter((el) => !el.matches("header, .neosubzero-weather-ambient")).forEach((el) => weather.append(el));
                const hourly = weather.querySelector(".neosubzero-weather-current-sort");
                if (hourly) hourly.scrollLeft = hourlyScroll;
                weather.className = newWeather.className;
                weather.dataset.weatherTheme = newWeather.dataset.weatherTheme;
                weather.querySelectorAll(".is-frost-reason").forEach((element) => { element.hidden = !frostExplanationsEnabled; });
                const stamp = weather.querySelector(".neosubzero-weather-source > span");
                if (stamp) stamp.textContent = newWeather.querySelector(".neosubzero-weather-source > span").textContent;
            }
            showAircraft(selectedMission);
            if (!deferred) root.dataset.revision = next.dataset.revision;
            window.scrollTo({top: scroll, behavior: "instant"});
            return !deferred;
        } finally { loading = false; }
    };
    root.addEventListener("submit", async (event) => {
        const form = event.target;
        if (event.defaultPrevented || !form.closest("[data-ucc-live-board]")) return;
        event.preventDefault();
        if (root.dataset.saving === "true") return;
        mutationEpoch += 1;
        root.dataset.saving = "true";
        const buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
        const body = new FormData(form);
        if (event.submitter?.name) body.set(event.submitter.name, event.submitter.value);
        buttons.forEach((button) => { button.disabled = true; });
        notify("Saving UCC…");
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
            notify(payload.message + (root.dataset.dirty === "true" ? " Other unsaved drafts are preserved; live display will reconcile after editing." : ""));
        } catch (error) {
            notify(error.name === "AbortError" ? "Request timed out; its outcome is unknown. Check current UCC before retrying. Your draft is preserved." : (error.message || "Unable to save. Your draft is preserved; check current UCC before retrying."), true);
        } finally {
            window.clearTimeout(timeout);
            delete root.dataset.saving;
            buttons.forEach((button) => { button.disabled = false; });
            if (accepted) {
                if (form.contains(document.activeElement)) document.activeElement.blur();
                try { await refreshBoard(); } catch (error) { notify("Saved, but " + error.message, true); }
            }
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
            const response = await fetch(url, {cache: "no-store"});
            const payload = await response.json();
            controller.setServerStatus(payload.refresh || {});
            if (!response.ok || payload.ok !== true) throw new Error(payload.error || "UCC refresh failed");
            // Do not consume a revision while a draft/focused input defers it.
            // The same pending change must still refresh after editing ends.
            if (payload.revision !== root.dataset.revision) {
                if (await refreshBoard()) revision = root.dataset.revision;
            } else if (!editing()) revision = root.dataset.revision;
        },
    });
    controller.setServerStatus(JSON.parse(root.dataset.refreshStatus || "{}"));
})();
