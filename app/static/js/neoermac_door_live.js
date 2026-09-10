(() => {
    const config = JSON.parse(document.getElementById("neoermac-door-config").textContent);
    const root = document.querySelector("[data-door-view]");
    if (!root) {
        return;
    }

    const stateUrl = root.dataset.stateUrl;
    const initialRefreshStatus = config.refresh;
    const refreshPaused = root.querySelector("[data-neoermac-refresh-paused]");
    const canEditRequests = config.canEdit;
    const selectedDoor = root.querySelector('[name="door"]')?.value || "";
    const doorViewUrl = config.doorViewUrl;
    const pullKeys = ["pure", "mix"];
    const pullAlertClasses = ["is-pull-due-soon", "is-pull-due-now", "is-pull-late"];
    const initialDoorTabAlerts = config.doorTabAlerts;
    const alertClockApi = window.NeoErmacDoorAlertClock;
    let currentDoorTabAlerts = initialDoorTabAlerts;
    let alertClock = null;
    let refreshController = null;

    const renderDoorTabAlerts = (nowMs = Date.now()) => {
        document.querySelectorAll("[data-door-tab]").forEach((tab) => {
            const door = tab.dataset.doorTab || "";
            const isActive = tab.classList.contains("is-active");
            const alert = isActive ? {} : (currentDoorTabAlerts?.[door] || {});
            const state = isActive
                ? ""
                : (
                    alertClockApi
                        ? alertClockApi.doorTabAlertState(alert, nowMs)
                        : (alert.state || "")
                );

            tab.classList.remove("is-pull-due-now", "is-pull-late");
            if (state === "late") {
                tab.classList.add("is-pull-late");
            } else if (state === "due_now") {
                tab.classList.add("is-pull-due-now");
            }
            tab.dataset.doorTabAlertState = state;
            tab.setAttribute(
                "aria-label",
                state === "late"
                    ? `${door}, late pull`
                    : (state === "due_now" ? `${door}, pull now` : door),
            );
        });
    };

    const applyDoorTabAlerts = (doorTabAlerts) => {
        currentDoorTabAlerts = doorTabAlerts || {};
        renderDoorTabAlerts();
        alertClock?.refresh();
    };

    window.neoErmacApplyDoorTabAlerts = applyDoorTabAlerts;

    const setEpochDataset = (row, property, value) => {
        if (value === null || value === undefined || value === "") {
            delete row.dataset[property];
            return;
        }
        row.dataset[property] = String(value);
    };

    const applyPullAlertTiming = (row, alert) => {
        const timing = alert || {};
        row.dataset.pullAccounted = timing.accounted === true ? "true" : "false";
        row.dataset.pullAlertKey = timing.key || "";
        setEpochDataset(row, "pullDueSoonEpochMs", timing.due_soon_epoch_ms);
        setEpochDataset(row, "pullDueNowEpochMs", timing.due_now_epoch_ms);
        setEpochDataset(row, "pullLateEpochMs", timing.late_epoch_ms);
        setEpochDataset(row, "pullWindowStartEpochMs", timing.window_start_epoch_ms);
        setEpochDataset(row, "pullWindowEndEpochMs", timing.window_end_epoch_ms);
    };

    const pullTimingFromRow = (row) => ({
        accounted: row.dataset.pullAccounted === "true",
        due_soon_epoch_ms: row.dataset.pullDueSoonEpochMs,
        due_now_epoch_ms: row.dataset.pullDueNowEpochMs,
        late_epoch_ms: row.dataset.pullLateEpochMs,
        window_start_epoch_ms: row.dataset.pullWindowStartEpochMs,
        window_end_epoch_ms: row.dataset.pullWindowEndEpochMs,
    });

    const renderPullAlert = (row, alert) => {
        const presentation = alert || {};
        row.classList.remove(...pullAlertClasses);
        if (presentation.css_class) {
            row.classList.add(presentation.css_class);
        }
        row.dataset.pullAlertState = presentation.state || "";

        let badge = row.querySelector("[data-pull-alert-badge]");
        if (presentation.label) {
            if (!badge) {
                badge = document.createElement("span");
                badge.className = "neoermac-pull-alert-badge";
                badge.dataset.pullAlertBadge = "";
                row.querySelector(".neoermac-door-planned")?.appendChild(badge);
            }
            badge.textContent = presentation.label;
        } else if (badge) {
            badge.remove();
        }
    };

    const applyPullAlerts = (card, pullAlerts) => {
        if (!card) {
            return;
        }
        card.querySelectorAll(".neoermac-door-pull-row[data-pull-key]").forEach((row) => {
            const pullKey = row.dataset.pullKey || "";
            const alert = pullAlerts?.[pullKey] || {};
            applyPullAlertTiming(row, alert);
            renderPullAlert(
                row,
                alertClockApi?.pullAlertState(alert, Date.now()) || alert,
            );
        });
        alertClock?.refresh();
    };

    window.neoErmacApplyPullAlerts = applyPullAlerts;

    const collectAlertTimings = () => {
        const timings = Array.from(
            root.querySelectorAll(".neoermac-door-pull-row[data-pull-key]")
        ).map(pullTimingFromRow);
        Object.values(currentDoorTabAlerts || {}).forEach((doorAlert) => {
            if (Array.isArray(doorAlert?.pulls)) {
                timings.push(...doorAlert.pulls);
            }
        });
        return timings;
    };

    const renderClockAlerts = (nowMs) => {
        root.querySelectorAll(".neoermac-door-pull-row[data-pull-key]").forEach((row) => {
            renderPullAlert(
                row,
                alertClockApi?.pullAlertState(pullTimingFromRow(row), nowMs) || {},
            );
        });
        renderDoorTabAlerts(nowMs);
    };

    const findPullControl = (card, selector, pullKey) => Array.from(
        card.querySelectorAll(selector)
    ).find((control) => control.dataset.pullKey === pullKey);

    const controlIsProtected = (control) => Boolean(
        control
        && (
            document.activeElement === control
            || control.dataset.localDirty === "true"
            || control.closest("[data-door-destination-card]")?.classList.contains("is-pull-saving")
        )
    );

    const cardHasProtectedInput = (card) => Array.from(
        card.querySelectorAll("[data-hhmm-input], [data-no-pull-toggle]")
    ).some(controlIsProtected);

    const updatePullFormCompleteState = () => {
        const form = root.querySelector("[data-door-pull-form]");
        if (!form) {
            return;
        }
        const cards = Array.from(form.querySelectorAll("[data-door-destination-card]"));
        const allCompleteCollapsed = cards.length > 0 && cards.every((card) => (
            card.classList.contains("is-pulls-complete")
            && card.classList.contains("is-pulls-collapsed")
        ));
        form.classList.toggle("is-all-pulls-complete", allCompleteCollapsed);
    };

    const applyDoorCardState = (card, cardData, options = {}) => {
        if (!card || !cardData) {
            return;
        }
        const protectLocal = options.protectLocal !== false;
        const operationInput = card.querySelector('[data-pull-operation]');
        const missionInput = card.querySelector('[data-pull-mission]');
        const identityChanged = missionInput && (
            missionInput.value !== String(cardData.mission_id || '')
            || operationInput?.value !== String(cardData.operation_id || '')
        );
        // Never rebind an edit in progress to a different mission after a poll.
        if (identityChanged && protectLocal && cardHasProtectedInput(card)) return;
        if (operationInput) operationInput.value = String(cardData.operation_id || '');
        if (missionInput) missionInput.value = String(cardData.mission_id || '');
        card.dataset.tailPresence = cardData.tail_presence?.state || "";

        const tail = card.querySelector("[data-door-tail]");
        if (tail) {
            tail.textContent = cardData.tail || "-";
        }
        const parking = card.querySelector("[data-door-parking]");
        if (parking) {
            parking.textContent = cardData.parking || "-";
        }
        const status = card.querySelector("[data-door-status]");
        if (status) {
            status.textContent = cardData.status || "Scheduled";
        }
        const windowLabel = card.querySelector("[data-door-window]");
        if (windowLabel) {
            windowLabel.textContent = cardData.window_minutes !== null
                && cardData.window_minutes !== undefined
                ? `WINDOW ${cardData.window_minutes} MIN`
                : "WINDOW TBD";
        }

        pullKeys.forEach((pullKey) => {
            const planned = card.querySelector(`[data-door-planned="${pullKey}"]`);
            if (planned) {
                planned.textContent = cardData.planned?.[pullKey] || "-";
            }
            const base = card.querySelector(`[data-door-base-planned="${pullKey}"]`);
            const showBase = Boolean(
                cardData.window_minutes !== null
                && cardData.window_minutes !== undefined
                && cardData.window_minutes !== 0
                && cardData.base_planned?.[pullKey]
                && cardData.base_planned[pullKey] !== cardData.planned?.[pullKey]
            );
            if (base) {
                base.hidden = !showBase;
                base.textContent = showBase
                    ? `BASE ${cardData.base_planned[pullKey]} +${cardData.window_minutes} MIN`
                    : "";
            }

            const input = findPullControl(card, "[data-hhmm-input]", pullKey);
            const toggle = findPullControl(card, "[data-no-pull-toggle]", pullKey);
            const protectedValue = protectLocal
                && (controlIsProtected(input) || controlIsProtected(toggle));
            const noPull = Boolean(cardData.no_pull?.[pullKey]);
            if (!protectedValue) {
                const original = card.querySelector(`[data-pull-original="${pullKey}"]`);
                if (original) original.value = cardData.original?.[pullKey] || '';
                if (toggle) {
                    toggle.checked = noPull;
                }
                if (input) {
                    input.value = noPull ? "" : (cardData.actual?.[pullKey] || "");
                    input.disabled = noPull;
                }
            }

            const staticActual = card.querySelector(`[data-door-static-actual="${pullKey}"]`);
            if (staticActual) {
                staticActual.textContent = noPull
                    ? `NO ${pullKey === "pure" ? "Pure" : "Mix Pull"}`
                    : (cardData.actual?.[pullKey] || "-");
            }
        });

        const summary = card.querySelector("[data-pull-complete-summary]");
        const title = card.querySelector("[data-pull-complete-title]");
        const pullLine = card.querySelector("[data-pull-complete-lines]");
        if (title) {
            title.textContent = cardData.pulls_complete
                ? (cardData.complete_title || `${cardData.destination || "-"} ${cardData.parking || "-"} COMPLETE`)
                : "";
        }
        if (pullLine) {
            pullLine.textContent = cardData.pull_summary || "";
        }
        if (cardData.pulls_complete) {
            card.classList.add("is-pulls-complete");
            if (!cardHasProtectedInput(card)) {
                card.classList.add("is-pulls-collapsed");
            }
            if (summary) {
                summary.hidden = false;
            }
            const editToggle = card.querySelector("[data-pull-edit-toggle]");
            if (editToggle && card.classList.contains("is-pulls-collapsed")) {
                editToggle.setAttribute("aria-expanded", "false");
                editToggle.textContent = "EDIT PULLS";
            }
        } else {
            card.classList.remove("is-pulls-complete", "is-pulls-collapsed");
            if (summary) {
                summary.hidden = true;
            }
        }

        applyPullAlerts(card, cardData.pull_alerts || {});
        updatePullFormCompleteState();
    };

    window.neoErmacApplyDoorCardState = applyDoorCardState;

    const renderEvents = (events) => {
        const panel = root.querySelector("[data-uld-events]");
        if (!panel) {
            return;
        }

        let body = "<h2>ON THE WAY</h2>";
        if (events && events.length) {
            body += `<ul class="neoermac-door-simple-list neoermac-uld-event-list">${events
                .map((event) => {
                    const door = event.door || "";
                    const href = `${doorViewUrl}?door=${encodeURIComponent(door)}`;
                    return `<li>
                        <a class="neoermac-uld-door-link" href="${href}" data-uld-door-link="${escapeHtml(door)}">${escapeHtml(door)}</a>
                        <span aria-hidden="true">&middot;</span>
                        <span>${escapeHtml(event.label || "")}</span>
                    </li>`;
                })
                .join("")}</ul>`;
        } else {
            body += '<p class="neoermac-door-empty">No relevant on-the-way events.</p>';
        }
        panel.innerHTML = body;
    };

    const renderRequests = (requests) => {
        const panel = root.querySelector("[data-uld-request-list]");
        if (!panel) {
            return;
        }
        if (panel.querySelector("[data-uld-request-edit][open]")) {
            return;
        }

        let body = "<h3>ACTIVE REQUESTS</h3>";
        if (requests && requests.length) {
            body += `<div class="neoermac-uld-request-rows">${requests
                .map((requestRow) => {
                    const counts = requestRow.counts || {};
                    const a2Count = Number.parseInt(counts.A2 ?? 0, 10) || 0;
                    const a1Count = Number.parseInt(counts.A1 ?? 0, 10) || 0;
                    const ampCount = Number.parseInt(counts.AMP ?? 0, 10) || 0;
                    const requestId = Number.parseInt(requestRow.id ?? 0, 10) || 0;
                    const requestLabel = requestRow.setup_needed ? "SETUP" : "RESPOT";
                    const requestDoor = requestRow.door || "";
                    const requestDoorHref = `${doorViewUrl}?door=${encodeURIComponent(requestDoor)}`;
                    const actions = canEditRequests && requestId ? `
                        <details class="neoermac-uld-request-edit" data-uld-request-edit>
                            <summary class="neoermac-uld-request-edit-button">EDIT</summary>
                            <form method="post" class="neoermac-uld-request-edit-form">
                                <input type="hidden" name="active_door" value="${escapeHtml(selectedDoor)}">
                                <input type="hidden" name="request_door" value="${escapeHtml(requestDoor)}">
                                <input type="hidden" name="action" value="edit_uld_request">
                                <input type="hidden" name="request_id" value="${requestId}">
                                <label>
                                    <span>A2</span>
                                    <input class="neoermac-ios-safe-input" type="number" name="uld_a2_count" min="0" step="1" inputmode="numeric" value="${a2Count}">
                                </label>
                                <label>
                                    <span>A1</span>
                                    <input class="neoermac-ios-safe-input" type="number" name="uld_a1_count" min="0" step="1" inputmode="numeric" value="${a1Count}">
                                </label>
                                <label>
                                    <span>AMP</span>
                                    <input class="neoermac-ios-safe-input" type="number" name="uld_amp_count" min="0" step="1" inputmode="numeric" value="${ampCount}">
                                </label>
                                <button class="neoermac-uld-request-save" type="submit">SAVE</button>
                            </form>
                        </details>
                        <form method="post" class="neoermac-uld-request-cancel-form">
                            <input type="hidden" name="active_door" value="${escapeHtml(selectedDoor)}">
                            <input type="hidden" name="request_door" value="${escapeHtml(requestDoor)}">
                            <input type="hidden" name="action" value="delete_uld_request">
                            <input type="hidden" name="request_id" value="${requestId}">
                            <button class="neoermac-uld-request-cancel" type="submit" aria-label="Cancel ${requestRow.setup_needed ? "setup" : "respot"} ULD request">&times;</button>
                        </form>
                    ` : "";
                    return `
                        <div class="neoermac-uld-request-row${requestRow.setup_needed ? " is-setup-needed" : ""}" data-uld-request-row>
                            <a class="neoermac-uld-door-link" href="${requestDoorHref}" data-uld-door-link="${escapeHtml(requestDoor)}">${escapeHtml(requestDoor)}</a>
                            <span>A2 <strong>${a2Count}</strong></span>
                            <span>A1 <strong>${a1Count}</strong></span>
                            <span>AMP <strong>${ampCount}</strong></span>
                            <span class="neoermac-uld-request-priority">${requestLabel}</span>
                            <time>${escapeHtml(requestRow.updated_at_label || formatTime(requestRow.updated_at))}</time>
                            ${actions}
                        </div>
                    `;
                })
                .join("")}</div>`;
        } else {
            body += '<p class="neoermac-door-empty">No relevant active ULD requests.</p>';
        }
        panel.innerHTML = body;
    };

    const applyState = (state) => {
        const workspace = state.uld_workspace || state;
        renderRequests(workspace.requests || []);
        renderEvents(workspace.on_the_way_events || []);
        const complete = renderDestinations(state.destinations || [], state.pull_content_html);
        applyDoorTabAlerts(state.door_tab_alerts || {});
        return complete;
    };

    const setRefreshStatus = (refresh) => {
        const status = refresh || {};
        const isActive = status.auto_refresh_enabled !== false;
        root.dataset.refreshActive = isActive ? "true" : "false";

        if (refreshPaused) {
            refreshPaused.hidden = isActive;
            refreshPaused.replaceChildren(
                document.createTextNode(status.message || "Live updates off - outside Sort window")
            );
            if (status.operation_label && status.window_label) {
                const detail = document.createElement("span");
                detail.textContent = `${status.operation_label} - ${status.window_label}`;
                refreshPaused.appendChild(detail);
            }
        }

        if (!refreshController) {
            refreshController = window.NeoLiveUpdates.create({
                intervalMs: config.refresh.live_screen_refresh_interval_ms,
                statusElement: document.querySelector("[data-live-update-status]"),
                poll: refreshState,
                continuousWhileVisible: true,
            });
        }
        refreshController.setServerStatus(status);
    };

    const renderDestinations = (destinations, html) => {
        const list = root.querySelector("[data-door-destination-list]");
        if (!list) return false;
        const rows = new Map(destinations.map(row => [String(row.destination).toUpperCase(), row]));
        let complete = true;
        if (typeof html === 'string') {
            const template = document.createElement('template');
            template.innerHTML = html;
            const incoming = Array.from(template.content.querySelectorAll('[data-door-destination-card]'));
            complete = window.NeoErmacLiveIntegrity.reconcileMembership(
                list, incoming, card => card.dataset.doorDestination,
                cardHasProtectedInput,
                (card) => applyDoorCardState(card, rows.get(card.dataset.doorDestination), {protectLocal: true}),
            );
            const form = root.querySelector('[data-door-pull-form]');
            form.hidden = list.children.length === 0;
            const empty = root.querySelector('[data-door-no-destinations]');
            if (empty) empty.hidden = list.children.length !== 0;
            // Scope visibility follows canonical membership without replacing the
            // form, its CSRF token, focused controls, or the user's scope choice.
            const newScope = template.content.querySelector('[data-pull-scope]');
            const oldScope = form.querySelector('[data-pull-scope]');
            if (newScope && !oldScope) form.insertBefore(newScope, list);
            if (!newScope && oldScope && complete) oldScope.remove();
            Array.from(list.children).forEach((card, index) => {
                card.dataset.destinationIndex = String(index);
                card.querySelectorAll('[name]').forEach(control => {
                    control.name = control.name.replace(/_\d+$/, '_' + index);
                });
                card.querySelectorAll('[data-no-pull-input]').forEach(input => { input.dataset.noPullInput = input.dataset.pullKey + '-' + index; });
                card.querySelectorAll('[data-no-pull-toggle]').forEach(input => { input.dataset.noPullToggle = input.dataset.pullKey + '-' + index; });
            });
            form.querySelector('[name="destination_count"]').value = String(list.children.length);
            window.neoErmacBindPullControls?.();
        } else {
            for (const card of list.children) {
                const row = rows.get(card.dataset.doorDestination);
                if (row) applyDoorCardState(card, row, {protectLocal: true});
                if (!row || cardHasProtectedInput(card)) complete = false;
            }
        }
        updatePullFormCompleteState();
        return complete;
    };

    window.neoErmacReconcileDoorDestinations = renderDestinations;
    const responseOrder = window.NeoErmacLiveIntegrity.createOrder();
    let currentRevision = root.dataset.doorViewRevision || "";
    window.neoErmacBeginPullSave = () => {
        currentRevision = '';
        return responseOrder.beginSave();
    };
    window.neoErmacEndPullSave = ticket => responseOrder.endSave(ticket);

    const refreshState = async () => {
        const ticket = responseOrder.beginPoll();
        const pollUrl = new URL(stateUrl, window.location.origin);
        if (currentRevision) pollUrl.searchParams.set("revision", currentRevision);
        const response = await fetch(pollUrl, {cache: "no-store", credentials: "same-origin"});
        if (!response.ok) throw new Error("Door View refresh failed.");
        const payload = await response.json();
        if (!payload.ok) throw new Error("Door View refresh failed.");
        if (!responseOrder.accepts(ticket)) return;
        setRefreshStatus(payload.refresh || payload.state?.refresh);
        if (payload.changed === false) return;
        if (!payload.state) throw new Error("Door View refresh failed.");
        // Do not accept a revision for a snapshot that was only partly applied.
        if (applyState(payload.state)) currentRevision = payload.revision || currentRevision;
    };

    const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
    }[character]));

    const formatTime = (value) => {
        if (!value) {
            return "-";
        }
        const match = String(value).match(/T(\d{2}:\d{2})/);
        return match ? match[1] : String(value).slice(0, 5);
    };

    applyDoorTabAlerts(initialDoorTabAlerts);
    alertClock = alertClockApi?.create({
        getTimings: collectAlertTimings,
        render: renderClockAlerts,
    }) || null;
    setRefreshStatus(initialRefreshStatus);
})();

(() => {
    if (!JSON.parse(document.getElementById("neoermac-door-config").textContent).canEdit) return;
    const root = document.querySelector("[data-door-view]");
    const pullForm = document.querySelector("[data-door-pull-form]");
    const saveUrl = root?.dataset.pullSaveUrl;
    const pullKeys = ["pure", "mix"];
    const pullLabels = {
        pure: "PURE",
        mix: "MIX",
    };
    const pendingSaves = new WeakMap();
    const saveSequences = new WeakMap();
    const fieldWrites = new WeakMap();
    const hhmmPattern = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
    const getScopeControl = () => root.querySelector("[data-pull-scope]");
    const scopeInput = pullForm?.querySelector("[data-pull-scope-value]");
    const operationId = root?.dataset.operationId || "";
    const userId = root?.dataset.userId || "";
    const scopeStorageKey = operationId && userId
        ? `neoermac.pull-scope.${userId}.${operationId}`
        : "";

    const setPullScope = (applyToBoth, { persist = true } = {}) => {
        const enabled = applyToBoth === true;
        if (scopeInput) {
            scopeInput.value = enabled ? "1" : "0";
        }
        getScopeControl()?.querySelectorAll("[data-pull-scope-option]").forEach((option) => {
            const active = (option.dataset.pullScopeOption === "1") === enabled;
            option.setAttribute("aria-pressed", active ? "true" : "false");
        });
        if (!persist || !scopeStorageKey) {
            return;
        }
        try {
            window.localStorage.setItem(scopeStorageKey, enabled ? "1" : "0");
        } catch (_storageError) {
            // Browser storage is optional; THIS DOOR remains the safe fallback.
        }
    };

    if (root) {
        let storedScope = "0";
        try {
            storedScope = window.localStorage.getItem(scopeStorageKey) || "0";
        } catch (_storageError) {
            storedScope = "0";
        }
        setPullScope(storedScope === "1", { persist: false });
        root.addEventListener("click", (event) => {
            const option = event.target.closest("[data-pull-scope-option]");
            if (option) {
                setPullScope(option.dataset.pullScopeOption === "1");
            }
        });
    }

    const findActualInput = (card, pullKey) => Array.from(
        card.querySelectorAll("[data-hhmm-input]")
    ).find((input) => input.dataset.pullKey === pullKey);

    const findNoPullToggle = (card, pullKey) => Array.from(
        card.querySelectorAll("[data-no-pull-toggle]")
    ).find((toggle) => toggle.dataset.pullKey === pullKey);

    const setCardError = (card, message) => {
        const error = card.querySelector("[data-pull-autosave-error]");
        if (!error) {
            return;
        }
        error.textContent = message || "";
        error.hidden = !message;
        card.classList.toggle("has-pull-save-error", Boolean(message));
        if (message) {
            card.classList.remove("is-pulls-collapsed");
        }
    };

    const setCardStatus = (card, message, state = "") => {
        const status = card.querySelector("[data-pull-autosave-status]");
        if (!status) {
            return;
        }
        status.textContent = message || "";
        status.dataset.state = state || "";
        status.hidden = !message;
    };

    const updatePullFormCompleteState = () => {
        if (!pullForm) {
            return;
        }
        const cards = Array.from(pullForm.querySelectorAll("[data-door-destination-card]"));
        const allCompleteCollapsed = cards.length > 0 && cards.every((card) => (
            card.classList.contains("is-pulls-complete")
            && card.classList.contains("is-pulls-collapsed")
        ));
        pullForm.classList.toggle("is-all-pulls-complete", allCompleteCollapsed);
    };

    const summaryText = (cardData) => pullKeys
        .map((key) => {
            const value = cardData.no_pull?.[key] ? "NONE" : (cardData.actual?.[key] || "-");
            return `${pullLabels[key]} ${value}`;
        })
        .join(" \u00B7 ");

    const applyCardState = (card, cardData, savedPullKey, submitted, applySnapshot = true) => {
        if (!cardData) {
            return;
        }
        const savedInput = findActualInput(card, savedPullKey);
        const savedToggle = findNoPullToggle(card, savedPullKey);
        const original = card.querySelector(`[data-pull-original="${savedPullKey}"]`);
        // Only acknowledge the version actually sent, never a newer edit or a
        // different mission. An accepted save advances its own field baseline
        // even while the input remains focused; polling cannot advance dirty fields.
        if (original?.value !== submitted.original) return;
        const sameIdentity = String(cardData.mission_id || '') === submitted.mission_id
            && String(cardData.operation_id || '') === submitted.operation_id;
        if (original && sameIdentity) original.value = cardData.original?.[savedPullKey] || '';
        const unchangedSinceSend = Boolean(savedToggle?.checked) === submitted.no_pull
            && (savedToggle?.checked ? '' : (savedInput?.value || '').trim()) === submitted.value;
        if (savedInput && unchangedSinceSend) {
            delete savedInput.dataset.localDirty;
        }
        if (savedToggle && unchangedSinceSend) {
            delete savedToggle.dataset.localDirty;
        }

        if (!applySnapshot) {
            setCardError(card, "");
            setCardStatus(card, "SAVED", "saved");
            return;
        }
        window.neoErmacApplyDoorCardState?.(
            card,
            {
                ...cardData,
                pull_summary: cardData.pull_summary || summaryText(cardData),
            },
            { protectLocal: true },
        );

        setCardError(card, "");
        setCardStatus(card, "SAVED", "saved");
        updatePullFormCompleteState();
    };

    const savePull = async (card, pullKey) => {
        if (!saveUrl || !card || !pullKey) return;
        const fields = fieldWrites.get(card) || {};
        const fieldWrite = fields[pullKey] || (fields[pullKey] = {running: false, queued: false});
        fieldWrites.set(card, fields);
        if (fieldWrite.running) { fieldWrite.queued = true; return; }
        const input = findActualInput(card, pullKey);
        const toggle = findNoPullToggle(card, pullKey);
        if (!input || !toggle) {
            return;
        }

        const value = input.value.trim();
        if (!toggle.checked && value && !hhmmPattern.test(value)) {
            setCardError(card, "Use HH:MM.");
            setCardStatus(card, "", "");
            return;
        }

        const body = new FormData();
        body.set("door", root.querySelector('[name="door"]')?.value || "");
        body.set("destination", card.dataset.doorDestination || "");
        body.set("pull_key", pullKey);
        body.set("operation_id", card.querySelector('[data-pull-operation]')?.value || '');
        body.set("mission_id", card.querySelector('[data-pull-mission]')?.value || '');
        body.set("original", card.querySelector(`[data-pull-original="${pullKey}"]`)?.value || '');
        body.set("actual_pull", toggle.checked ? "" : value);
        body.set("no_pull", toggle.checked ? "1" : "0");
        body.set("apply_to_both", scopeInput?.value === "1" ? "1" : "0");
        const submitted = {
            original: body.get('original'), operation_id: body.get('operation_id'),
            mission_id: body.get('mission_id'), value: body.get('actual_pull'),
            no_pull: body.get('no_pull') === '1',
        };

        const sequences = saveSequences.get(card) || {};
        const sequence = (sequences[pullKey] || 0) + 1;
        sequences[pullKey] = sequence;
        saveSequences.set(card, sequences);
        const ticket = window.neoErmacBeginPullSave();
        fieldWrite.running = true;
        fieldWrite.queued = false;
        card.dataset.pendingPullSaves = String(Number(card.dataset.pendingPullSaves || 0) + 1);
        const finish = () => {
            fieldWrite.running = false;
            card.dataset.pendingPullSaves = String(Number(card.dataset.pendingPullSaves) - 1);
            if (Number(card.dataset.pendingPullSaves) === 0) card.classList.remove("is-pull-saving");
            return window.neoErmacEndPullSave(ticket);
        };
        card.classList.add("is-pull-saving");
        setCardError(card, "");
        setCardStatus(card, "SAVING", "saving");
        let payload;
        try {
            const response = await fetch(saveUrl, {
                method: "POST",
                body,
                credentials: "same-origin",
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                },
            });
            payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) {
                const serverMessage = typeof payload.error === "string"
                    ? payload.error
                    : payload.error?.message;
                throw new Error(serverMessage || "Pull save failed.");
            }
        } catch (error) {
            finish();
            fieldWrite.queued = false; // Never replay a rejected write.
            if (sequences[pullKey] === sequence) {
                setCardError(card, error.message || "Pull save failed.");
                setCardStatus(card, "", "");
            }
            return;
        }

        const applySnapshot = finish();
        if (sequences[pullKey] !== sequence) return;
        try {
            applyCardState(card, payload.card, pullKey, submitted, applySnapshot);
            if (applySnapshot && payload.state?.destinations) {
                window.neoErmacReconcileDoorDestinations?.(payload.state.destinations);
            }
            if (applySnapshot && payload.state?.door_tab_alerts) {
                window.neoErmacApplyDoorTabAlerts?.(payload.state.door_tab_alerts);
            }
        } catch (_reconciliationError) {
            setCardError(card, "");
            setCardStatus(card, "SAVED - VIEW WILL REFRESH", "saved");
        }
        if (fieldWrite.queued && (Boolean(toggle.checked) !== submitted.no_pull
            || (toggle.checked ? '' : input.value.trim()) !== submitted.value)) {
            await savePull(card, pullKey);
        }
    };

    const scheduleSave = (input, delay = 350) => {
        const card = input.closest("[data-door-destination-card]");
        if (!card) {
            return;
        }
        const existing = pendingSaves.get(input);
        if (existing) {
            window.clearTimeout(existing);
        }
        pendingSaves.set(
            input,
            window.setTimeout(() => savePull(card, input.dataset.pullKey), delay)
        );
    };

    root?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-pull-edit-toggle]");
        if (!button) {
            return;
        }
        const card = button.closest("[data-door-destination-card]");
        if (!card) {
            return;
        }
        card.classList.remove("is-pulls-collapsed");
        button.setAttribute("aria-expanded", "true");
        button.textContent = "PULL ROWS OPEN";
        updatePullFormCompleteState();
    });

    const bindPullControls = () => {
        setPullScope(scopeInput?.value === '1', {persist: false});
        root.querySelectorAll("[data-hhmm-input]").forEach((input) => {
            if (input.dataset.pullBound) return;
            input.dataset.pullBound = 'true';
            input.addEventListener("input", () => {
                input.dataset.localDirty = "true";
                const digits = input.value.replace(/\D/g, "").slice(0, 4);
                if (digits.length >= 3) {
                    input.value = `${digits.slice(0, 2)}:${digits.slice(2)}`;
                } else {
                    input.value = digits;
                }
                if (!input.value || hhmmPattern.test(input.value)) {
                    const card = input.closest("[data-door-destination-card]");
                    if (card) {
                        setCardError(card, "");
                    }
                    scheduleSave(input);
                } else if (input.value.length >= 5) {
                    const card = input.closest("[data-door-destination-card]");
                    if (card) {
                        setCardError(card, "Use HH:MM.");
                        setCardStatus(card, "", "");
                    }
                }
            });

            input.addEventListener("blur", () => {
                if (!input.value || hhmmPattern.test(input.value)) {
                    scheduleSave(input, 0);
                } else {
                    const card = input.closest("[data-door-destination-card]");
                    if (card) {
                        setCardError(card, "Use HH:MM.");
                        setCardStatus(card, "", "");
                    }
                }
            });
        });

        root.querySelectorAll("[data-no-pull-toggle]").forEach((toggle) => {
            if (toggle.dataset.pullBound) return;
            toggle.dataset.pullBound = 'true';
            const input = findActualInput(toggle.closest('[data-door-destination-card]'), toggle.dataset.pullKey);
            if (!input) {
                return;
            }

            const syncInput = () => {
                input.disabled = toggle.checked;
                if (toggle.checked) {
                    input.value = "";
                }
            };

            toggle.addEventListener("change", () => {
                toggle.dataset.localDirty = "true";
                syncInput();
                const card = toggle.closest("[data-door-destination-card]");
                if (card) {
                    savePull(card, toggle.dataset.pullKey);
                }
            });
            syncInput();
        });
    };
    window.neoErmacBindPullControls = bindPullControls;
    bindPullControls();

    updatePullFormCompleteState();
})();
