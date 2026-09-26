(() => {
    const root = document.querySelector("[data-neoermac-upcoming-live]");
    if (!root) {
        return;
    }
    const boardHost = root.querySelector("[data-upcoming-pulls-board-host]");
    if (!boardHost) {
        return;
    }

    let currentRevision = root.dataset.upcomingRevision || "";
    let controller = null;
    const initialStatus = JSON.parse(root.dataset.refreshStatus || "{}");

    const applyRefreshStatus = (status) => {
        const refresh = status || {};
        const enabled = refresh.auto_refresh_enabled !== false;
        const banner = root.querySelector("[data-operation-refresh-banner]");
        if (banner) {
            if (!enabled && refresh.message) {
                banner.replaceChildren(document.createTextNode(refresh.message));
                if (refresh.operation_label && refresh.window_label) {
                    const detail = document.createElement("span");
                    detail.textContent = `${refresh.operation_label} \u00b7 ${refresh.window_label}`;
                    banner.appendChild(detail);
                }
            }
            banner.hidden = enabled;
        }
        root.dataset.refreshActive = enabled ? "true" : "false";
        controller.setServerStatus(refresh);
    };

    let selectedSide = "west";
    let epoch = 0;
    const inputs = () => Array.from(boardHost.querySelectorAll("[data-upcoming-actual]"));
    const protectedInput = () => inputs().some(input =>
        input === document.activeElement || input.value !== "" || input.dataset.saving === "true");
    const applySide = () => {
        root.querySelectorAll("[data-upcoming-side-button]").forEach(button => {
            button.setAttribute("aria-pressed", String(button.dataset.upcomingSideButton === selectedSide));
        });
        boardHost.querySelectorAll("[data-upcoming-side]").forEach(panel => {
            panel.hidden = panel.dataset.upcomingSide !== selectedSide;
        });
    };
    root.addEventListener("click", event => {
        const button = event.target.closest("[data-upcoming-side-button]");
        if (!button) return;
        selectedSide = button.dataset.upcomingSideButton;
        applySide();
    });

    const poll = async () => {
        if (protectedInput()) return;
        const requestEpoch = ++epoch;
        const pollUrl = new URL(root.dataset.stateUrl, window.location.origin);
        pollUrl.searchParams.set("revision", currentRevision);
        const response = await fetch(pollUrl, {
            cache: "no-store",
            credentials: "same-origin",
            headers: {"Accept": "application/json"},
        });
        const payload = await response.json().catch(() => ({}));
        if (payload.reload_required) {
            throw new Error(payload.error || "Reload Upcoming Pulls to resume live updates.");
        }
        if (!response.ok || !payload.ok) {
            throw new Error(payload.error || "Upcoming Pulls live refresh failed.");
        }

        // A poll started before focus or a save must not erase the edit or
        // acknowledge a revision whose HTML was never installed.
        if (requestEpoch !== epoch || protectedInput()) return;
        applyRefreshStatus(payload.refresh);
        if (payload.changed === false) {
            currentRevision = payload.revision || currentRevision;
            return;
        }

        const parsed = new DOMParser().parseFromString(payload.board_html || "", "text/html");
        const nextBoard = parsed.querySelector("[data-upcoming-pulls-board]");
        if (!nextBoard) {
            throw new Error("Upcoming Pulls board response was incomplete.");
        }
        boardHost.replaceChildren(nextBoard);
        currentRevision = payload.revision || currentRevision;
        applySide();
    };

    const feedback = (input, message) => {
        input.closest("form").querySelector("[data-upcoming-save-status]").textContent = message;
    };
    const refreshAfterEdit = () => poll().catch(() => {
        const status = root.querySelector("[data-live-update-status]");
        if (status) status.textContent = "Refresh failed. Retrying…";
    });
    const save = async input => {
        if (input.dataset.saving === "true") return;
        if (!input.value) {
            feedback(input, "");
            refreshAfterEdit();
            return;
        }
        const value = /^\d{4}$/.test(input.value)
            ? `${input.value.slice(0, 2)}:${input.value.slice(2)}` : input.value;
        if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(value)) {
            feedback(input, "Use HHMM (0000–2359)");
            return;
        }
        input.value = value;
        const form = input.closest("form");
        input.dataset.saving = "true";
        input.readOnly = true;
        ++epoch;
        feedback(input, "Saving…");
        try {
            const response = await fetch(root.dataset.saveUrl, {
                method: "POST", credentials: "same-origin",
                headers: {"Accept": "application/json"}, body: new FormData(form),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) throw new Error(payload.error || "Save failed. Try again.");
            currentRevision = "refresh-after-save";
            if (input.value === value) {
                input.value = "";
                input.blur();
                feedback(input, "Saved");
            } else {
                feedback(input, "Unsaved change");
            }
        } catch (error) {
            feedback(input, error.message || "Save failed. Try again.");
        } finally {
            input.dataset.saving = "false";
            input.readOnly = false;
            ++epoch;
        }
        await refreshAfterEdit();
    };
    boardHost.addEventListener("input", event => {
        if (event.target.matches("[data-upcoming-actual]")) feedback(event.target, event.target.value ? "Unsaved" : "");
    });
    boardHost.addEventListener("change", event => {
        if (event.target.matches("[data-upcoming-actual]")) save(event.target);
    });
    boardHost.addEventListener("submit", event => {
        if (!event.target.matches("[data-upcoming-pull-form]")) return;
        event.preventDefault();
        save(event.target.querySelector("[data-upcoming-actual]"));
    });
    boardHost.addEventListener("keydown", event => {
        const input = event.target;
        if (!input.matches("[data-upcoming-actual]")) return;
        if (event.key === "Enter") {
            event.preventDefault();
            save(input);
        } else if (event.key === "Escape" && input.dataset.saving !== "true") {
            input.value = "";
            feedback(input, "");
            input.blur();
            refreshAfterEdit();
        }
    });
    boardHost.addEventListener("focusout", event => {
        if (event.target.dataset.saving === "true") return;
        // Wait for focus to reach its next input before deciding to refresh.
        setTimeout(refreshAfterEdit, 0);
    });
    applySide();

    controller = window.NeoLiveUpdates.create({
        intervalMs: Number(root.dataset.refreshIntervalMs),
        statusElement: root.querySelector("[data-live-update-status]"),
        poll,
        continuousWhileVisible: true,
    });
    controller.setServerStatus(initialStatus);
})();
