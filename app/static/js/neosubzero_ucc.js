(() => {
    "use strict";

    const root = document.querySelector("[data-subzero-ucc]");
    if (!root) return;

    const forms = Array.from(root.querySelectorAll("[data-ucc-assignment-form]"));
    const dialog = root.querySelector("[data-ucc-move-dialog]");
    let pendingForm = null;

    const weather = root.querySelector(".neosubzero-weather[data-weather-user-id]");
    const weatherMotionToggle = weather?.querySelector("[data-weather-motion-toggle]");
    const weatherMotionKey = weather?.dataset.weatherUserId
        ? `neosubzero.weather-motion.${weather.dataset.weatherUserId}`
        : "";
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
    let weatherMotionEnabled = true;
    if (weatherMotionKey) {
        try {
            weatherMotionEnabled = window.localStorage.getItem(weatherMotionKey) !== "0";
        } catch (_error) {
            weatherMotionEnabled = true;
        }
    }
    applyWeatherMotion(weatherMotionEnabled);
    weatherMotionToggle?.addEventListener("click", () => {
        weatherMotionEnabled = !weatherMotionEnabled;
        applyWeatherMotion(weatherMotionEnabled);
        try {
            window.localStorage.setItem(weatherMotionKey, weatherMotionEnabled ? "1" : "0");
        } catch (_error) {
            // The visual preference remains active for this page when storage is unavailable.
        }
    });

    const field = (form, name) => form.querySelector(`[name="${name}"]`);

    const sourceFormFor = (personId, destinationForm) => forms.find(
        (form) => form !== destinationForm && form.dataset.currentPersonId === personId
    );

    const setSourceFields = (form, sourceForm) => {
        field(form, "source_assignment_id").value = sourceForm?.dataset.assignmentId || "";
        field(form, "source_expected_version").value = sourceForm?.dataset.assignmentVersion || "";
    };

    const resetMove = (form, {restoreSelection = false} = {}) => {
        if (!form) return;
        if (restoreSelection) {
            field(form, "person_id").value = form.dataset.currentPersonId || "";
        }
        field(form, "source_assignment_id").value = "";
        field(form, "source_expected_version").value = "";
        field(form, "move_resolution").value = "";
        delete form.dataset.moveConfirmed;
    };

    const updateDirtyState = () => {
        const dirty = forms.some((form) => (
            field(form, "person_id")?.value || ""
        ) !== (form.dataset.currentPersonId || ""));
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

    forms.forEach((form) => {
        form.addEventListener("change", () => {
            resetMove(form);
            updateDirtyState();
        });
        form.addEventListener("submit", (event) => {
            if (form.dataset.moveConfirmed === "true") {
                root.dataset.saving = "true";
                return;
            }
            const move = prepareMove(form);
            if (move.occupied && move.personId) {
                event.preventDefault();
                openOccupiedDialog(form, move.sourceForm);
                return;
            }
            root.dataset.saving = "true";
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

    root.querySelectorAll("[data-ucc-truck-form]").forEach((form) => {
        form.addEventListener("input", () => { root.dataset.dirty = "true"; });
        form.addEventListener("submit", () => { root.dataset.saving = "true"; });
    });

    root.querySelectorAll("[data-ucc-drag-assignee]").forEach((handle) => {
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
            revision = payload.revision || revision;
            if (
                payload.changed
                && root.dataset.dirty !== "true"
                && root.dataset.saving !== "true"
                && !root.querySelector(":focus")
                && !dialog?.open
            ) {
                window.location.reload();
            }
        },
    });
    controller.setServerStatus(JSON.parse(root.dataset.refreshStatus || "{}"));
})();
