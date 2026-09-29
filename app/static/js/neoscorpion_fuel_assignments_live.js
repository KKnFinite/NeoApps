(() => {
    "use strict";

    const root = document.querySelector("[data-fuel-assignments-live]");
    if (!root || !window.NeoLiveUpdates) {
        return;
    }

    const pollIntervalMs = Number(root.dataset.refreshIntervalMs || 0);
    const currentUserId = root.dataset.currentUserId;
    const revisionUrl = root.dataset.revisionUrl;
    const panelUrl = root.dataset.panelUrl;
    const acknowledgeUrl = root.dataset.acknowledgeUpdateUrl;
    let operationId = root.dataset.operationId || "none";
    let revision = Number(root.dataset.revision || 0);
    let pendingOperationId = operationId;
    let pendingRevision = revision;
    let refreshing = false;
    let audioContext = null;
    let controller = null;

    const storageKey = (suffix, scopedOperationId = operationId) => (
        `neoscorpion:fuel-assignments:${suffix}:${currentUserId}:${scopedOperationId}`
    );

    const readStoredIds = () => {
        try {
            const stored = JSON.parse(sessionStorage.getItem(storageKey("seen")) || "[]");
            return new Set(Array.isArray(stored) ? stored.map(String) : []);
        } catch (_error) {
            return new Set();
        }
    };

    const writeStoredIds = (ids) => {
        try {
            sessionStorage.setItem(storageKey("seen"), JSON.stringify(Array.from(ids)));
        } catch (_error) {
            // Session storage only supports the assignment alert experience.
        }
    };

    const currentAssignmentIds = (scope = root) => new Set(
        Array.from(scope.querySelectorAll("[data-fuel-assignment-id]"))
            .map((card) => String(card.dataset.fuelAssignmentId))
            .filter(Boolean)
    );

    const getAudioContext = () => {
        if (audioContext) {
            return audioContext;
        }
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) {
            return null;
        }
        audioContext = new AudioContext();
        return audioContext;
    };

    const primeAudio = () => {
        try {
            const context = getAudioContext();
            if (context?.state === "suspended") {
                context.resume().catch(() => {});
            }
        } catch (_error) {
            // Visual updates remain available when browser audio cannot be unlocked.
        }
    };

    const playAssignmentAlert = () => {
        try {
            const context = getAudioContext();
            if (!context) {
                return;
            }
            const start = context.currentTime;
            const gain = context.createGain();
            const tone = context.createOscillator();
            tone.type = "triangle";
            tone.frequency.setValueAtTime(920, start);
            tone.frequency.exponentialRampToValueAtTime(480, start + 0.16);
            gain.gain.setValueAtTime(0.0001, start);
            gain.gain.exponentialRampToValueAtTime(0.18, start + 0.012);
            gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.19);
            tone.connect(gain);
            gain.connect(context.destination);
            tone.start(start);
            tone.stop(start + 0.2);
        } catch (_error) {
            // Autoplay restrictions must never suppress the visual assignment update.
        }
    };

    const rememberAssignments = (alertForNew = false) => {
        const currentIds = currentAssignmentIds();
        const seenIds = readStoredIds();
        if (
            alertForNew
            && Array.from(currentIds).some((id) => !seenIds.has(id))
        ) {
            playAssignmentAlert();
        }
        writeStoredIds(currentIds);
    };

    const fuelerControls = (scope = root) => Array.from(scope.querySelectorAll(
        ".neoscorpion-fueler-form input:not([type='hidden']), "
        + ".neoscorpion-fueler-form select, .neoscorpion-fueler-form textarea"
    ));

    const markFallbackBaselines = (scope = root) => {
        fuelerControls(scope).forEach((control) => {
            control.dataset.fuelerLiveBaseline = control.value;
        });
    };

    const hasUnsavedFuelEntry = () => window.NeoScorpionFuelData
        ? window.NeoScorpionFuelData.hasDirty(root)
        : fuelerControls().some(
            (control) => control.dataset.fuelerLiveBaseline !== control.value
        );

    const capturePanelState = () => {
        const state = {
            scrollX: window.scrollX,
            scrollY: window.scrollY,
            openDetails: {},
            focusAssignmentId: null,
            focusName: null,
        };
        root.querySelectorAll("[data-fuel-assignment-id]").forEach((card) => {
            const id = String(card.dataset.fuelAssignmentId || "");
            if (!id) return;
            state.openDetails[id] = Array.from(card.querySelectorAll("details"))
                .map((details, index) => details.open ? index : -1)
                .filter((index) => index >= 0);
        });
        const active = document.activeElement;
        const activeCard = active?.closest?.("[data-fuel-assignment-id]");
        if (activeCard && active?.name) {
            state.focusAssignmentId = String(activeCard.dataset.fuelAssignmentId || "");
            state.focusName = active.name;
        }
        return state;
    };

    const restorePanelState = (state) => {
        Object.entries(state.openDetails).forEach(([id, indexes]) => {
            const card = Array.from(root.querySelectorAll("[data-fuel-assignment-id]"))
                .find((candidate) => String(candidate.dataset.fuelAssignmentId) === id);
            if (!card) return;
            const details = Array.from(card.querySelectorAll("details"));
            indexes.forEach((index) => {
                if (details[index]) details[index].open = true;
            });
        });
        if (state.focusAssignmentId && state.focusName) {
            const card = Array.from(root.querySelectorAll("[data-fuel-assignment-id]"))
                .find((candidate) => (
                    String(candidate.dataset.fuelAssignmentId) === state.focusAssignmentId
                ));
            const control = card
                ? Array.from(card.querySelectorAll("[name]"))
                    .find((candidate) => candidate.name === state.focusName)
                : null;
            control?.focus({preventScroll: true});
        }
        window.requestAnimationFrame(() => {
            window.scrollTo(state.scrollX, state.scrollY);
        });
    };

    const adoptFingerprint = (payload) => {
        operationId = payload.operation_id === null
            ? "none"
            : String(payload.operation_id);
        revision = Number(payload.revision || 0);
        pendingOperationId = operationId;
        pendingRevision = revision;
        root.dataset.operationId = operationId === "none" ? "" : operationId;
        root.dataset.revision = String(revision);
    };

    const applyPanel = (payload) => {
        const currentPanel = root.querySelector("[data-fuel-assignments-panel]");
        const template = document.createElement("template");
        template.innerHTML = String(payload.html || "").trim();
        const nextPanel = template.content.querySelector("[data-fuel-assignments-panel]");
        if (!currentPanel || !nextPanel) {
            throw new Error("Fuel Assignments live panel is unavailable.");
        }

        const priorOperationId = operationId;
        const state = capturePanelState();
        currentPanel.replaceWith(nextPanel);
        window.NeoScorpionFuelData?.initialize?.(nextPanel);
        markFallbackBaselines(nextPanel);
        adoptFingerprint(payload);
        rememberAssignments(priorOperationId === operationId);
        restorePanelState(state);
    };

    const refreshForChange = async (nextOperationId, nextRevision) => {
        pendingOperationId = nextOperationId;
        pendingRevision = nextRevision;
        if (refreshing || hasUnsavedFuelEntry()) {
            return false;
        }

        refreshing = true;
        try {
            const response = await fetch(panelUrl, {
                cache: "no-store",
                credentials: "same-origin",
                headers: {"Accept": "application/json"},
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok !== true) {
                throw new Error(payload.error || "Fuel Assignments live panel is unavailable.");
            }
            if (hasUnsavedFuelEntry()) {
                pendingOperationId = payload.operation_id === null
                    ? "none"
                    : String(payload.operation_id);
                pendingRevision = Number(payload.revision || 0);
                return false;
            }
            applyPanel(payload);
            return true;
        } finally {
            refreshing = false;
        }
    };

    const reconcileWhenClean = () => {
        if (
            !hasUnsavedFuelEntry()
            && (pendingOperationId !== operationId || pendingRevision !== revision)
        ) {
            refreshForChange(pendingOperationId, pendingRevision).catch(() => {});
        }
    };

    const poll = async () => {
        const response = await fetch(revisionUrl, {
            cache: "no-store",
            credentials: "same-origin",
            headers: {"Accept": "application/json"},
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.ok !== true) {
            throw new Error("Fuel Assignments live status is unavailable.");
        }

        const nextOperationId = payload.operation_id === null
            ? "none"
            : String(payload.operation_id);
        const nextRevision = Number(payload.revision || 0);
        if (nextOperationId !== operationId || nextRevision !== revision) {
            await refreshForChange(nextOperationId, nextRevision);
        }
    };

    const acknowledgeUpdate = async (button) => {
        if (!acknowledgeUrl || button.dataset.acknowledging === "true") {
            return;
        }
        const notice = button.closest("[data-assignment-update-notice]");
        const status = notice?.querySelector("[data-acknowledge-status]");
        const body = new FormData();
        body.set("assignment_id", button.dataset.assignmentId || "");
        body.set("update_version", button.dataset.updateVersion || "");
        button.dataset.acknowledging = "true";
        button.disabled = true;
        if (status) {
            status.textContent = "Saving...";
        }
        try {
            const response = await fetch(acknowledgeUrl, {
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
                throw new Error(payload.error || "Acknowledgment failed.");
            }
            notice?.remove();
        } catch (error) {
            if (status) {
                status.textContent = error.message || "Acknowledgment failed.";
            }
            button.disabled = false;
        } finally {
            button.dataset.acknowledging = "false";
        }
    };

    ["pointerdown", "keydown", "touchstart"].forEach((eventName) => {
        document.addEventListener(eventName, primeAudio, {once: true, passive: true});
    });

    root.addEventListener("click", (event) => {
        const button = event.target.closest("[data-acknowledge-assignment-update]");
        if (button) {
            acknowledgeUpdate(button);
        }
    });
    root.addEventListener("input", reconcileWhenClean);
    root.addEventListener("change", reconcileWhenClean);

    document.addEventListener("neoscorpion:fuel-data-saved", (event) => {
        const payload = event.detail || {};
        if (payload.operation_id === undefined || payload.revision === undefined) {
            return;
        }
        if (payload.closed) {
            const nextOperationId = payload.operation_id === null
                ? "none"
                : String(payload.operation_id);
            refreshForChange(nextOperationId, Number(payload.revision || 0)).catch(() => {});
            return;
        }
        adoptFingerprint(payload);
        rememberAssignments(false);
    });

    markFallbackBaselines(root);
    rememberAssignments(false);

    if (Number.isFinite(pollIntervalMs) && pollIntervalMs >= 5000 && panelUrl) {
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
