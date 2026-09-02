(() => {
    "use strict";

    if (window.NeoLiveUpdates) {
        return;
    }

    const DEFAULT_FAILURE_THRESHOLD = 3;
    const MINIMUM_REFRESH_INTERVAL_MS = 5000;
    const INACTIVITY_TIMEOUT_MS = 10 * 60 * 1000;
    const USER_ACTIVITY_EVENTS = Object.freeze([
        ["pointerdown", true],
        ["mousedown", true],
        ["touchstart", true],
        ["keydown", true],
        ["scroll", true],
        ["input", true],
        ["change", true],
    ]);

    class LiveUpdateController {
        constructor(options) {
            this.poll = options.poll;
            this.intervalMs = Math.max(MINIMUM_REFRESH_INTERVAL_MS, Number(options.intervalMs));
            if (!Number.isFinite(this.intervalMs)) {
                throw new Error("A valid live-screen refresh interval is required.");
            }
            this.statusElement = options.statusElement || null;
            this.failureThreshold = Number(options.failureThreshold) || DEFAULT_FAILURE_THRESHOLD;
            this.immediate = options.immediate !== false;
            this.continuousWhileVisible = options.continuousWhileVisible === true;
            this.enabled = false;
            this.running = false;
            this.lastRefreshStartedAt = 0;
            this.timer = null;
            this.inactivityTimer = null;
            this.inactivityPaused = false;
            this.monitorMode = false;
            this.failures = 0;
            this.serverStatus = options.serverStatus || {};
            this.destroyed = false;
            this.statusLabelElement = null;
            this.monitorButton = null;
            this.monitorStateElement = null;
            this.onVisibilityChange = this.onVisibilityChange.bind(this);
            this.onUserActivity = this.onUserActivity.bind(this);
            this.onInactivityTimeout = this.onInactivityTimeout.bind(this);
            document.addEventListener("visibilitychange", this.onVisibilityChange);
            if (!this.continuousWhileVisible) {
                USER_ACTIVITY_EVENTS.forEach(([eventName, capture]) => {
                    document.addEventListener(eventName, this.onUserActivity, {
                        capture,
                        passive: ["pointerdown", "mousedown", "touchstart", "scroll"].includes(eventName),
                    });
                });
                this.buildMonitorControl();
            }
        }

        setServerStatus(status) {
            this.serverStatus = status || {};
            this.setEnabled(this.serverStatus.auto_refresh_enabled !== false);
        }

        setEnabled(enabled) {
            const wasEnabled = this.enabled;
            this.enabled = Boolean(enabled);
            this.updateMonitorControl();
            if (!this.enabled) {
                this.clearTimer();
                this.clearInactivityTimer();
                this.inactivityPaused = false;
                this.renderStatus(
                    this.serverStatus.reason || "outside_ops_window",
                    this.serverStatus.live_status_label
                        || this.serverStatus.message
                        || "Live updates off - outside Ops window"
                );
                return;
            }

            if (this.inactivityPaused) {
                this.renderInactiveStatus();
                return;
            }

            this.renderStatus("active", "Live updates on");
            if (!wasEnabled) {
                this.armInactivityTimer();
            }
            if (!wasEnabled && !document.hidden) {
                if (this.immediate) {
                    this.refreshNow({force: true});
                } else {
                    this.schedule();
                }
            }
        }

        refreshNow(options = {}) {
            if (
                !this.enabled
                || this.running
                || this.destroyed
                || document.hidden
                || this.inactivityPaused
            ) {
                return Promise.resolve(false);
            }
            const elapsed = Date.now() - this.lastRefreshStartedAt;
            if (!options.force && this.lastRefreshStartedAt && elapsed < MINIMUM_REFRESH_INTERVAL_MS) {
                this.clearTimer();
                this.timer = window.setTimeout(
                    () => this.refreshNow(),
                    MINIMUM_REFRESH_INTERVAL_MS - elapsed
                );
                return Promise.resolve(false);
            }
            this.clearTimer();
            this.running = true;
            this.lastRefreshStartedAt = Date.now();
            return Promise.resolve()
                .then(() => this.poll())
                .then(() => {
                    this.failures = 0;
                    if (this.enabled && !this.inactivityPaused) {
                        this.renderStatus("active", "Live updates on");
                    }
                    return true;
                })
                .catch(() => {
                    this.failures += 1;
                    if (!this.inactivityPaused && this.failures >= this.failureThreshold) {
                        this.renderStatus(
                            "reconnecting",
                            "Live updates paused - reconnecting..."
                        );
                    }
                    return false;
                })
                .finally(() => {
                    this.running = false;
                    this.schedule();
                });
        }

        schedule() {
            this.clearTimer();
            if (
                !this.enabled
                || this.destroyed
                || document.hidden
                || this.inactivityPaused
            ) {
                return;
            }
            this.timer = window.setTimeout(() => this.refreshNow(), this.intervalMs);
        }

        clearTimer() {
            if (this.timer !== null) {
                window.clearTimeout(this.timer);
                this.timer = null;
            }
        }

        clearInactivityTimer() {
            if (this.inactivityTimer !== null) {
                window.clearTimeout(this.inactivityTimer);
                this.inactivityTimer = null;
            }
        }

        armInactivityTimer() {
            this.clearInactivityTimer();
            if (
                !this.enabled
                || this.destroyed
                || document.hidden
                || this.inactivityPaused
                || this.monitorMode
                || this.continuousWhileVisible
            ) {
                return;
            }
            this.inactivityTimer = window.setTimeout(
                this.onInactivityTimeout,
                INACTIVITY_TIMEOUT_MS
            );
        }

        onInactivityTimeout() {
            this.inactivityTimer = null;
            if (
                !this.enabled
                || this.destroyed
                || document.hidden
                || this.monitorMode
                || this.continuousWhileVisible
            ) {
                return;
            }
            this.inactivityPaused = true;
            this.clearTimer();
            this.renderInactiveStatus();
        }

        onUserActivity(event) {
            if (
                this.continuousWhileVisible
                || event?.isTrusted === false
                || !this.enabled
                || document.hidden
            ) {
                return;
            }
            if (this.inactivityPaused) {
                this.inactivityPaused = false;
                this.renderStatus("active", "Live updates on");
                this.armInactivityTimer();
                this.refreshNow({force: true});
                return;
            }
            this.armInactivityTimer();
        }

        setMonitorMode(enabled) {
            if (this.continuousWhileVisible) {
                return;
            }
            this.monitorMode = Boolean(enabled);
            this.updateMonitorControl();
            if (this.monitorMode) {
                this.clearInactivityTimer();
                if (this.inactivityPaused) {
                    this.inactivityPaused = false;
                    this.renderStatus("active", "Live updates on");
                    this.refreshNow({force: true});
                }
                return;
            }
            this.armInactivityTimer();
        }

        onVisibilityChange() {
            if (document.hidden) {
                this.clearTimer();
                this.clearInactivityTimer();
                return;
            }
            if (this.enabled) {
                this.inactivityPaused = false;
                this.renderStatus("active", "Live updates on");
                this.armInactivityTimer();
                this.refreshNow({force: true});
            }
        }

        buildMonitorControl() {
            if (!this.statusElement?.parentNode) {
                return;
            }

            let controls = this.statusElement.parentElement?.matches("[data-live-update-controls]")
                ? this.statusElement.parentElement
                : null;
            if (!controls) {
                controls = document.createElement("div");
                controls.className = "live-update-controls";
                controls.dataset.liveUpdateControls = "true";
                this.statusElement.parentNode.insertBefore(controls, this.statusElement);
                controls.appendChild(this.statusElement);
            }

            this.statusLabelElement = this.statusElement.querySelector(
                "[data-live-update-label]"
            );
            if (!this.statusLabelElement) {
                this.statusLabelElement = document.createElement("span");
                this.statusLabelElement.dataset.liveUpdateLabel = "true";
                this.statusLabelElement.textContent = this.statusElement.textContent.trim();
                this.statusElement.textContent = "";
                this.statusElement.appendChild(this.statusLabelElement);
            }

            this.monitorButton = document.createElement("button");
            this.monitorButton.type = "button";
            this.monitorButton.className = "live-update-monitor-toggle";
            this.monitorButton.dataset.liveMonitorMode = "true";
            this.monitorButton.setAttribute("aria-pressed", "false");
            this.monitorButton.title = (
                "Keep this foreground page live until it closes. Hidden tabs and server windows still pause updates."
            );

            const monitorLabel = document.createElement("span");
            monitorLabel.textContent = "KEEP LIVE / MONITOR MODE";
            this.monitorStateElement = document.createElement("strong");
            this.monitorStateElement.className = "live-update-monitor-state";
            this.monitorStateElement.textContent = "OFF";
            this.monitorButton.append(monitorLabel, this.monitorStateElement);
            this.monitorButton.addEventListener("click", () => {
                this.setMonitorMode(!this.monitorMode);
            });
            controls.appendChild(this.monitorButton);
            this.updateMonitorControl();
        }

        updateMonitorControl() {
            if (!this.monitorButton) {
                return;
            }
            this.monitorButton.disabled = !this.enabled;
            this.monitorButton.setAttribute("aria-pressed", String(this.monitorMode));
            this.monitorButton.dataset.monitorModeActive = String(this.monitorMode);
            if (this.monitorStateElement) {
                this.monitorStateElement.textContent = this.monitorMode ? "ON" : "OFF";
            }
        }

        renderInactiveStatus() {
            this.renderStatus("inactive", "LIVE UPDATES PAUSED \u2014 INACTIVE");
        }

        renderStatus(state, label) {
            if (!this.statusElement) {
                return;
            }
            this.statusElement.dataset.liveUpdateState = state;
            if (this.statusLabelElement) {
                this.statusLabelElement.textContent = label;
            } else {
                this.statusElement.textContent = label;
            }
        }

        destroy() {
            this.destroyed = true;
            this.clearTimer();
            this.clearInactivityTimer();
            document.removeEventListener("visibilitychange", this.onVisibilityChange);
            if (!this.continuousWhileVisible) {
                USER_ACTIVITY_EVENTS.forEach(([eventName, capture]) => {
                    document.removeEventListener(eventName, this.onUserActivity, capture);
                });
            }
        }
    }

    const rowHasLocalWork = (row) => {
        const activeElement = document.activeElement;
        return row.dataset.liveDirty === "true"
            || row.contains(activeElement)
            || Boolean(row.querySelector("details[open], dialog[open], [aria-expanded='true']"));
    };

    const formKey = (form) => [
        String(form.method || "get").toLowerCase(),
        form.getAttribute("action") || "",
    ].join(":");

    const matchingForm = (replacement, currentForm) => (
        Array.from(replacement.querySelectorAll("form"))
            .find((form) => formKey(form) === formKey(currentForm))
    );

    const copyControlState = (sourceForm, targetForm) => {
        const sourceControls = Array.from(
            sourceForm.querySelectorAll("input, select, textarea")
        );
        const targetControls = Array.from(
            targetForm.querySelectorAll("input, select, textarea")
        );
        sourceControls.forEach((source, sourceIndex) => {
            const sameName = sourceControls
                .slice(0, sourceIndex + 1)
                .filter((field) => field.name === source.name).length - 1;
            const target = targetControls
                .filter((field) => field.name === source.name)[sameName];
            if (!target) {
                return;
            }
            if (source instanceof HTMLInputElement && ["checkbox", "radio"].includes(source.type)) {
                target.checked = source.checked;
            } else {
                target.value = source.value;
            }
            if (source.dataset.liveDirty === "true") {
                target.dataset.liveDirty = "true";
            }
            if (source === document.activeElement) {
                target.dataset.liveRestoreFocus = "true";
                if (typeof source.selectionStart === "number") {
                    target.dataset.liveSelectionStart = String(source.selectionStart);
                    target.dataset.liveSelectionEnd = String(source.selectionEnd);
                }
            }
        });
    };

    const preserveLocalControls = (current, replacement) => {
        const activeElement = document.activeElement;
        const protectedForms = Array.from(current.querySelectorAll("form")).filter(
            (form) => form.contains(activeElement)
                || Boolean(form.querySelector("[data-live-dirty='true']"))
        );
        for (const sourceForm of protectedForms) {
            const targetForm = matchingForm(replacement, sourceForm);
            if (!targetForm) {
                return { preserved: true, canReplace: false };
            }
            copyControlState(sourceForm, targetForm);
        }

        const currentDetails = Array.from(current.querySelectorAll("details"));
        const replacementDetails = Array.from(replacement.querySelectorAll("details"));
        currentDetails.forEach((details, index) => {
            if (replacementDetails[index]) {
                replacementDetails[index].open = details.open;
            }
        });

        if (protectedForms.length) {
            replacement.dataset.liveDirty = "true";
        }
        return {
            preserved: protectedForms.length > 0,
            canReplace: !current.querySelector("dialog[open], [data-live-confirmation-active='true']"),
        };
    };

    const parseRows = (container, html) => {
        const parser = new DOMParser();
        const isTableSection = ["TBODY", "THEAD", "TFOOT"].includes(container.tagName);
        const documentHtml = isTableSection
            ? `<table><tbody>${html || ""}</tbody></table>`
            : `<div data-live-row-source>${html || ""}</div>`;
        const parsed = parser.parseFromString(documentHtml, "text/html");
        const source = isTableSection
            ? parsed.querySelector("tbody")
            : parsed.querySelector("[data-live-row-source]");
        return Array.from(source?.children || []);
    };

    const preserveViewFlags = (current, replacement) => {
        replacement.hidden = current.hidden;
        if (current.classList.contains("is-selected")) {
            replacement.classList.add("is-selected");
        }
        if (current.getAttribute("aria-selected") === "true") {
            replacement.setAttribute("aria-selected", "true");
        }
    };

    const markRemoteConflict = (row, incomingVersion, removed = false) => {
        row.classList.add("is-live-conflict");
        row.dataset.liveRemoteChanged = "true";
        row.dataset.liveRemoteRemoved = removed ? "true" : "false";
        if (incomingVersion) {
            row.dataset.liveServerVersion = incomingVersion;
        }
    };

    const reconcileRows = (container, html) => {
        if (!container) {
            return { added: 0, updated: 0, removed: 0, protected: 0 };
        }
        const scrollHost = container.closest(".table-wrap") || container;
        const scrollTop = scrollHost.scrollTop;
        const scrollLeft = scrollHost.scrollLeft;
        const incomingChildren = parseRows(container, html);
        const incomingRows = incomingChildren.filter((row) => row.matches("[data-live-row]"));
        const incomingById = new Map(
            incomingRows.map((row) => [row.dataset.liveId, row])
        );
        const currentRows = Array.from(container.querySelectorAll(":scope > [data-live-row]"));
        const currentById = new Map(
            currentRows.map((row) => [row.dataset.liveId, row])
        );
        const result = { added: 0, updated: 0, removed: 0, protected: 0 };

        currentRows.forEach((current) => {
            const incoming = incomingById.get(current.dataset.liveId);
            if (!incoming) {
                if (rowHasLocalWork(current)) {
                    markRemoteConflict(current, "", true);
                    result.protected += 1;
                } else {
                    current.remove();
                    result.removed += 1;
                }
                return;
            }

            if (current.dataset.liveVersion === incoming.dataset.liveVersion) {
                return;
            }
            const localState = preserveLocalControls(current, incoming);
            if (!localState.canReplace) {
                markRemoteConflict(current, incoming.dataset.liveVersion);
                result.protected += 1;
                return;
            }

            preserveViewFlags(current, incoming);
            if (localState.preserved) {
                markRemoteConflict(incoming, incoming.dataset.liveVersion);
                result.protected += 1;
            }
            incoming.classList.add("is-live-updated");
            current.replaceWith(incoming);
            const focusTarget = incoming.querySelector("[data-live-restore-focus='true']");
            if (focusTarget) {
                focusTarget.removeAttribute("data-live-restore-focus");
                focusTarget.focus({ preventScroll: true });
                if (
                    typeof focusTarget.setSelectionRange === "function"
                    && focusTarget.dataset.liveSelectionStart
                ) {
                    focusTarget.setSelectionRange(
                        Number(focusTarget.dataset.liveSelectionStart),
                        Number(focusTarget.dataset.liveSelectionEnd)
                    );
                }
                focusTarget.removeAttribute("data-live-selection-start");
                focusTarget.removeAttribute("data-live-selection-end");
            }
            currentById.set(incoming.dataset.liveId, incoming);
            result.updated += 1;
        });

        container.querySelectorAll(":scope > [data-live-empty-row]").forEach((row) => row.remove());
        incomingRows.forEach((incoming) => {
            if (container.querySelector(`[data-live-id="${CSS.escape(incoming.dataset.liveId)}"]`)) {
                return;
            }
            incoming.classList.add("is-live-updated");
            container.appendChild(incoming);
            result.added += 1;
        });
        if (!container.querySelector(":scope > [data-live-row]")) {
            incomingChildren
                .filter((row) => row.matches("[data-live-empty-row]"))
                .forEach((row) => container.appendChild(row));
        }

        scrollHost.scrollTop = scrollTop;
        scrollHost.scrollLeft = scrollLeft;
        return result;
    };

    const markDirtyInput = (event) => {
        const input = event.target instanceof Element
            ? event.target.closest("input, select, textarea")
            : null;
        const row = input?.closest("[data-live-row]");
        if (row) {
            input.dataset.liveDirty = "true";
            row.dataset.liveDirty = "true";
        }
    };

    const clearConflict = (row) => {
        row?.classList.remove("is-live-conflict");
        row?.removeAttribute("data-live-remote-changed");
        row?.removeAttribute("data-live-remote-removed");
        row?.querySelector("[data-live-conflict-panel]")?.remove();
    };

    const showConflict = (form, payload, refresh) => {
        const row = form.closest("[data-live-row]");
        const existing = row?.querySelector("[data-live-conflict-panel]");
        existing?.remove();
        const panel = document.createElement("div");
        panel.className = "live-conflict-panel";
        panel.dataset.liveConflictPanel = "true";

        const message = document.createElement("p");
        message.textContent = payload.message || "This item changed while you were working.";
        panel.appendChild(message);

        const actions = document.createElement("div");
        actions.className = "live-conflict-actions";
        const latest = document.createElement("button");
        latest.type = "button";
        latest.textContent = "USE LATEST";
        latest.addEventListener("click", () => {
            if (row) {
                row.dataset.liveDirty = "false";
                row.querySelectorAll("[data-live-dirty]").forEach((field) => {
                    field.dataset.liveDirty = "false";
                });
            }
            clearConflict(row);
            refresh();
        });
        actions.appendChild(latest);

        if (payload.can_overwrite !== false) {
            const overwrite = document.createElement("button");
            overwrite.type = "button";
            overwrite.textContent = "OVERWRITE WITH MINE";
            overwrite.addEventListener("click", () => {
                let field = form.querySelector("input[name='force_overwrite']");
                if (!field) {
                    field = document.createElement("input");
                    field.type = "hidden";
                    field.name = "force_overwrite";
                    form.appendChild(field);
                }
                field.value = "1";
                submitLiveForm(form, refresh);
            });
            actions.appendChild(overwrite);
        }
        panel.appendChild(actions);
        (row || form).appendChild(panel);
    };

    const showActionError = (form, message) => {
        const row = form.closest("[data-live-row]");
        const panel = document.createElement("div");
        panel.className = "live-conflict-panel is-error";
        panel.dataset.liveConflictPanel = "true";
        panel.textContent = message || "The action could not be completed.";
        row?.querySelector("[data-live-conflict-panel]")?.remove();
        (row || form).appendChild(panel);
    };

    const submitLiveForm = async (form, refresh) => {
        if (form.dataset.liveSubmitting === "true") {
            return;
        }
        form.dataset.liveSubmitting = "true";
        try {
            const response = await fetch(form.action, {
                method: String(form.method || "POST").toUpperCase(),
                body: new FormData(form),
                cache: "no-store",
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
            });
            const payload = await response.json().catch(() => ({}));
            if (response.status === 409) {
                showConflict(form, payload.conflict || payload, refresh);
                return;
            }
            if (!response.ok || payload.ok === false) {
                showActionError(form, payload.error || payload.message);
                return;
            }
            const row = form.closest("[data-live-row]");
            if (row) {
                row.dataset.liveDirty = "false";
            }
            clearConflict(row);
            await refresh();
        } finally {
            form.dataset.liveSubmitting = "false";
        }
    };

    const bindConflictSafeForms = (root, refresh) => {
        root.addEventListener("input", markDirtyInput);
        root.addEventListener("change", markDirtyInput);
        root.addEventListener("submit", (event) => {
            const form = event.target instanceof HTMLFormElement ? event.target : null;
            if (!form?.matches("[data-live-action]")) {
                return;
            }
            event.preventDefault();
            submitLiveForm(form, refresh);
        });
    };

    const updateAlertTrayCount = (tray) => {
        const count = tray.querySelectorAll("[data-alert-unread='true']").length;
        tray.dataset.alertCount = String(count);
        const badge = tray.querySelector(".motherbrain-alert-count");
        if (badge) {
            badge.textContent = String(count);
        }
    };

    const markAlertReadInDocument = (alertId) => {
        document.querySelectorAll(`[data-alert-id="${CSS.escape(String(alertId))}"]`)
            .forEach((item) => {
                item.dataset.alertUnread = "false";
                item.classList.remove("is-unread");
                item.classList.add("is-read");
            });
        document.querySelectorAll("[data-my-alerts-tray]").forEach(updateAlertTrayCount);
    };

    const markAlertRead = async (item) => {
        const endpoint = item.dataset.alertReadUrl;
        const alertId = item.dataset.alertId;
        if (!endpoint || !alertId || item.dataset.alertReadPending === "true") {
            return;
        }
        item.dataset.alertReadPending = "true";
        try {
            const response = await fetch(endpoint, {
                method: "POST",
                cache: "no-store",
                credentials: "same-origin",
                headers: {"Accept": "application/json"},
            });
            if (response.ok) {
                markAlertReadInDocument(alertId);
            }
        } catch (_error) {
            // Reading an alert is best-effort; the unread state remains visible.
        } finally {
            item.removeAttribute("data-alert-read-pending");
        }
    };

    const bindAlertTray = (tray) => {
        if (!tray || tray.dataset.alertReadBound === "true") {
            return;
        }
        tray.dataset.alertReadBound = "true";
        tray.addEventListener("toggle", () => {
            if (!tray.open) {
                return;
            }
            tray.querySelectorAll(
                "[data-alert-unread='true'][data-alert-read-url]"
            ).forEach((item) => markAlertRead(item));
        });
    };

    const bindAlertTrays = (root = document) => {
        root.querySelectorAll("[data-my-alerts-tray]").forEach(bindAlertTray);
    };

    const reconcileAlertTrays = (html) => {
        if (typeof html !== "string") {
            return;
        }
        const parsed = new DOMParser().parseFromString(html, "text/html");
        const incoming = parsed.querySelector("[data-my-alerts-tray]");
        if (!incoming) {
            return;
        }
        document.querySelectorAll("[data-my-alerts-tray]").forEach((current) => {
            const replacement = incoming.cloneNode(true);
            replacement.open = current.open;
            current.replaceWith(replacement);
            bindAlertTray(replacement);
        });
    };

    const bindPeopleDrawerClose = () => {
        const console = document.querySelector(".neostaffing-people-console");
        if (!console) {
            return;
        }
        const closePeopleDrawers = () => {
            document.querySelectorAll("[data-neostaffing-drawer]").forEach((drawer) => {
                drawer.open = false;
            });
            document.querySelectorAll(".neostaffing-people-detail-drawer").forEach((drawer) => {
                drawer.hidden = true;
            });
        };
        document.addEventListener("click", (event) => {
            if (event.target.closest("[data-neostaffing-close-drawer]")) {
                closePeopleDrawers();
            }
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                closePeopleDrawers();
            }
        });
    };

    const bindPeopleEntryEnhancements = () => {
        const console = document.querySelector(".neostaffing-people-console");
        if (!console) return;
        const singleForm = document.querySelector('form[action="/neostaffing/app-management/people"]');
        const employeeId = singleForm?.querySelector('[name="employee_id"]');
        const seniority = singleForm?.querySelector('[name="seniority_date"]');
        const phone = singleForm?.querySelector('[name="phone_number"]');
        const classification = singleForm?.querySelector('[name="classification"]');
        document.querySelectorAll('[data-people-open-add="single"]').forEach((button) => {
            button.addEventListener("click", () => {
                requestAnimationFrame(() => employeeId?.focus());
            });
        });
        seniority?.addEventListener("change", () => {
            if (/^\d{4}-\d{2}-\d{2}$/.test(seniority.value)) phone?.focus();
        });
        phone?.addEventListener("input", () => {
            if (phone.value.replace(/\D/g, "").length === 10) classification?.focus();
        });
        singleForm?.addEventListener("submit", () => {
            try {
                sessionStorage.setItem("neostaffing.people.single-add", JSON.stringify(
                    Object.fromEntries(new FormData(singleForm).entries())
                ));
            } catch (_error) {}
        });
        const stored = (() => { try { return JSON.parse(sessionStorage.getItem("neostaffing.people.single-add")); } catch (_error) { return null; } })();
        if (stored && singleForm) {
            const succeeded = document.body.textContent.includes("Person added.");
            if (!succeeded) {
                Object.entries(stored).forEach(([name, value]) => {
                    const field = singleForm.elements.namedItem(name);
                    if (field && "value" in field) field.value = value;
                });
            }
            const drawer = document.querySelector('[data-people-add-drawer="single"]');
            if (drawer) drawer.open = true;
            requestAnimationFrame(() => employeeId?.focus());
            sessionStorage.removeItem("neostaffing.people.single-add");
        }
        const destination = document.querySelector('#people-selection-form select[name="work_area_unit_id"]');
        if (destination && !destination.dataset.peopleHierarchyPicker) {
            const root = {};
            Array.from(destination.options).filter((option) => option.value).forEach((option) => {
                let branch = root;
                option.textContent.split(" / ").forEach((part, index, parts) => {
                    branch[part] ||= { children: {}, option: null };
                    if (index === parts.length - 1) branch[part].option = option;
                    branch = branch[part].children;
                });
            });
            const picker = document.createElement("details");
            picker.className = "neostaffing-people-destination-picker";
            picker.innerHTML = '<summary data-people-destination-label>Select Work Area</summary>';
            const render = (items) => {
                const list = document.createElement("ul");
                Object.entries(items).forEach(([name, item]) => {
                    const row = document.createElement("li");
                    if (item.option) {
                        const button = document.createElement("button");
                        button.type = "button"; button.textContent = name;
                        button.addEventListener("click", () => {
                            destination.value = item.option.value;
                            picker.querySelector("[data-people-destination-label]").textContent = item.option.textContent;
                            picker.open = false;
                        });
                        row.append(button);
                    } else {
                        const group = document.createElement("details");
                        group.open = destination.selectedOptions[0]?.textContent.includes(name) || false;
                        group.innerHTML = `<summary>${name}</summary>`;
                        group.append(render(item.children)); row.append(group);
                    }
                    list.append(row);
                });
                return list;
            };
            picker.append(render(root));
            destination.hidden = true;
            destination.after(picker);
            destination.dataset.peopleHierarchyPicker = "true";
        }
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => {
            bindAlertTrays();
            bindPeopleDrawerClose();
            bindPeopleEntryEnhancements();
        });
    } else {
        bindAlertTrays();
        bindPeopleDrawerClose();
        bindPeopleEntryEnhancements();
    }

    window.NeoLiveUpdates = Object.freeze({
        create: (options) => new LiveUpdateController(options),
        inactivityTimeoutMs: INACTIVITY_TIMEOUT_MS,
        reconcileRows,
        bindConflictSafeForms,
        bindAlertTrays,
        reconcileAlertTrays,
    });
})();
