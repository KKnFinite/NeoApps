(() => {
    "use strict";

    const root = document.querySelector("[data-fuel-assignments-live]");
    if (!root || !window.NeoLiveUpdates) {
        return;
    }

    const pollIntervalMs = Number(root.dataset.refreshIntervalMs || 0);
    const operationId = root.dataset.operationId || "none";
    const currentUserId = root.dataset.currentUserId;
    const revisionUrl = root.dataset.revisionUrl;
    const acknowledgeUrl = root.dataset.acknowledgeUpdateUrl;
    const initialControlValues = new WeakMap();
    let revision = Number(root.dataset.revision || 0);
    let pendingOperationId = operationId;
    let pendingRevision = revision;
    let reloading = false;
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
            // Session storage is an enhancement; live refresh remains functional without it.
        }
    };

    const currentAssignmentIds = () => new Set(
        Array.from(root.querySelectorAll("[data-fuel-assignment-id]"))
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
            // Visual alerts remain available when browser audio cannot be unlocked.
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
            // Autoplay restrictions must never suppress the visual assignment alert.
        }
    };

    const presentNewAssignments = () => {
        const currentIds = currentAssignmentIds();
        let liveReloadPending = false;
        try {
            liveReloadPending = sessionStorage.getItem(storageKey("pending")) === "1";
            sessionStorage.removeItem(storageKey("pending"));
        } catch (_error) {
            liveReloadPending = false;
        }

        const seenIds = readStoredIds();
        const newIds = liveReloadPending
            ? new Set(Array.from(currentIds).filter((id) => !seenIds.has(id)))
            : new Set();

        if (newIds.size) {
            root.querySelectorAll("[data-fuel-assignment-id]").forEach((card) => {
                if (newIds.has(String(card.dataset.fuelAssignmentId))) {
                    const marker = card.querySelector("[data-new-assignment-marker]");
                    if (marker) {
                        marker.hidden = false;
                    }
                }
            });
            playAssignmentAlert();
        }
        writeStoredIds(currentIds);
    };

    const fuelerControls = () => Array.from(root.querySelectorAll(
        ".neoscorpion-fueler-form input:not([type='hidden']), "
        + ".neoscorpion-fueler-form select, .neoscorpion-fueler-form textarea"
    ));

    fuelerControls().forEach((control) => initialControlValues.set(control, control.value));

    const hasUnsavedFuelEntry = () => fuelerControls().some(
        (control) => initialControlValues.get(control) !== control.value
    );

    const prepareReload = (nextOperationId, nextRevision) => {
        if (nextOperationId === operationId && nextRevision !== revision) {
            try {
                sessionStorage.setItem(storageKey("pending"), "1");
            } catch (_error) {
                // The reload remains correct; only browser-local alert detection is unavailable.
            }
        }
    };

    const reloadForChange = (nextOperationId, nextRevision) => {
        if (reloading) {
            return;
        }
        pendingOperationId = nextOperationId;
        pendingRevision = nextRevision;
        if (hasUnsavedFuelEntry()) {
            return;
        }
        reloading = true;
        controller?.setEnabled(false);
        prepareReload(nextOperationId, nextRevision);
        window.location.reload();
    };

    const reconcileWhenClean = () => {
        if (!hasUnsavedFuelEntry() && (
            pendingOperationId !== operationId || pendingRevision !== revision
        )) {
            reloadForChange(pendingOperationId, pendingRevision);
        }
    };
    fuelerControls().forEach((control) => {
        control.addEventListener("input", reconcileWhenClean);
        control.addEventListener("change", reconcileWhenClean);
    });

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
            reloadForChange(nextOperationId, nextRevision);
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
    presentNewAssignments();

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
