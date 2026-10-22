(() => {
    "use strict";

    const root = document.querySelector("[data-fuel-dispatch-live]");
    if (!root) {
        return;
    }

    function initializeDispatchRowHeight(scope) {
        const panel = scope.matches?.('[data-mission-row-height-percent]')
            ? scope : scope.querySelector('[data-mission-row-height-percent]');
        const value = Number(panel?.dataset.missionRowHeightPercent || 100);
        if (panel && Number.isInteger(value) && value >= 75 && value <= 150) {
            // CSSOM updates are allowed by CSP; HTML style attributes are not.
            panel.style.setProperty('--dispatch-row-height-scale', String(value / 100));
        }
    }

    initializeDispatchRowHeight(root);
    initializeSpearSplash(root);
    initializeDispatchDetails(root);
    initializeDispatchSelects(root);
    initializeDispatchApuEditors(root);
    let dispatchSortMode = root.dataset.sortMode === "arrival" ? "arrival" : "etd";
    let arrivedFirst = root.dataset.arrivedFirst !== "0";
    let truckCardsCollapsed = root.dataset.truckCardsCollapsed === "1";
    let boardTruckCardsCollapsed = root.dataset.boardTruckCardsCollapsed === "1";
    let statisticsCollapsed = root.dataset.statisticsCollapsed === "1";
    let spearDataCollapsed = root.dataset.spearDataCollapsed === "1";
    const pendingPreferences = new Set();
    let preferenceVersion = Number(root.dataset.preferenceVersion || 0);
    let preferenceSaving = false;
    let preferenceQueued = false;
    let preferenceMessage = "";
    applyDispatchSort(root);
    const dispatchScroll = initializeDispatchScroll(root);
    root.addEventListener("submit", (event) => {
        const button = event.submitter;
        if (button?.getAttribute("formaction")?.endsWith("/fuel-dispatch/complete")) {
            dispatchScroll.preserveCompletion(button);
        }
    }, true);
    root.addEventListener("click", (event) => {
        const button = event.target.closest("[data-dispatch-sort-mode], [data-dispatch-arrived-first], [data-dispatch-collapse]");
        if (!button) return;
        const saved = dispatchScroll.snapshot();
        const section = button.dataset.dispatchCollapse;
        if (section === "trucks") {
            if (document.body.classList.contains('operational-board-view')) {
                boardTruckCardsCollapsed = !boardTruckCardsCollapsed;
                pendingPreferences.add('board_truck_cards_collapsed');
            } else {
                truckCardsCollapsed = !truckCardsCollapsed;
                pendingPreferences.add("truck_cards_collapsed");
            }
        } else if (section === 'statistics') {
            statisticsCollapsed = !statisticsCollapsed;
            pendingPreferences.add('statistics_collapsed');
        } else if (section === "spear") {
            spearDataCollapsed = !spearDataCollapsed;
            pendingPreferences.add("spear_data_collapsed");
        } else if (button.hasAttribute("data-dispatch-sort-mode")) {
            dispatchSortMode = button.dataset.dispatchSortMode === "arrival" ? "arrival" : "etd";
            pendingPreferences.add("secondary_sort");
        } else {
            arrivedFirst = button.dataset.dispatchArrivedFirst === "1";
            pendingPreferences.add("arrived_first");
        }
        preferenceMessage = "SAVING…";
        if (section) renderSortControls(root);
        else applyDispatchSort(root);
        applyMissionSearch(root);
        // An intentional sort must expose the new order at the same scroll
        // offset, not scroll back to the former first mission and conceal it.
        // Automatic live refresh still restores its mission anchor separately.
        dispatchScroll.restoreSnapshot(section ? saved : {...saved, rowKey: null});
        saveSortPreferences();
    });
    async function saveSortPreferences() {
        if (preferenceSaving) { preferenceQueued = true; return; }
        preferenceSaving = true;
        preferenceMessage = "SAVING…";
        renderSortControls(root);
        const values = preferenceValues();
        const sent = new Map(Array.from(pendingPreferences, key => [key, values[key]]));
        try {
            const body = new URLSearchParams({expected_version: String(preferenceVersion),
                csrf_token: document.querySelector('meta[name="csrf-token"]')?.content || ""});
            sent.forEach((value, key) => body.set(key, typeof value === "boolean" ? (value ? "1" : "0") : value));
            const response = await fetch(root.dataset.sortPreferencesUrl, {method: "POST", body,
                credentials: "same-origin", headers: {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}});
            const payload = await response.json().catch(() => ({}));
            if (response.ok && payload.ok === true) {
                const current = preferenceValues();
                sent.forEach((value, key) => { if (current[key] === value) pendingPreferences.delete(key); });
            }
            if (payload.preferences) adoptPreferences(payload.preferences);
            if (!response.ok || payload.ok !== true) throw new Error(payload.error || "Unable to save Dispatch preferences.");
            preferenceMessage = "SAVED";
        } catch (error) {
            preferenceMessage = `NOT SAVED: ${error.message || "Connection failed. Select again to retry."}`;
            preferenceQueued = false;
        } finally {
            preferenceSaving = false;
            renderSortControls(root);
            if (preferenceQueued) { preferenceQueued = false; if (pendingPreferences.size) saveSortPreferences(); }
        }
    }
    function preferenceValues() {
        return {secondary_sort: dispatchSortMode, arrived_first: arrivedFirst,
            board_truck_cards_collapsed: boardTruckCardsCollapsed, statistics_collapsed: statisticsCollapsed,
            truck_cards_collapsed: truckCardsCollapsed, spear_data_collapsed: spearDataCollapsed};
    }
    function adoptPreferences(preferences) {
        if (!pendingPreferences.has("secondary_sort")) dispatchSortMode = preferences.secondary_sort === "arrival" ? "arrival" : "etd";
        if (!pendingPreferences.has("arrived_first")) arrivedFirst = preferences.arrived_first !== false;
        if (!pendingPreferences.has("truck_cards_collapsed")) truckCardsCollapsed = preferences.truck_cards_collapsed === true;
        if (!pendingPreferences.has('board_truck_cards_collapsed')) boardTruckCardsCollapsed = preferences.board_truck_cards_collapsed === true;
        if (!pendingPreferences.has('statistics_collapsed')) statisticsCollapsed = preferences.statistics_collapsed === true;
        if (!pendingPreferences.has("spear_data_collapsed")) spearDataCollapsed = preferences.spear_data_collapsed === true;
        preferenceVersion = Number(preferences.version || 0);
    }
    const preserveDispatchScroll = () => dispatchScroll.preserve();
    window.addEventListener("pagehide", preserveDispatchScroll);
    let missionSearch = '';
    root.addEventListener('input', event => {
        if (event.target.matches('[data-dispatch-search]')) {
            missionSearch = event.target.value;
            applyMissionSearch(root);
        }
    });
    root.addEventListener('change', () => applyMissionSearch(root));
    window.addEventListener('resize', () => { if (missionSearch) applyMissionSearch(root); });
    root.addEventListener('click', event => {
        if (!event.target.closest('[data-dispatch-search-clear]')) return;
        missionSearch = '';
        const input = root.querySelector('[data-dispatch-search]');
        if (input) { input.value = ''; input.focus({preventScroll:true}); }
        applyMissionSearch(root);
    });
    function applyMissionSearch(scope) {
        const query = missionSearch.trim().toLocaleLowerCase();
        scope.querySelectorAll('[data-dispatch-search]').forEach(input => {
            if (input.value !== missionSearch) input.value = missionSearch;
        });
        const groups = new Map();
        scope.querySelectorAll('tbody > tr[data-dispatch-sort-group]').forEach(row => {
            const id = row.dataset.dispatchSortGroup;
            if (!groups.has(id)) groups.set(id, []);
            groups.get(id).push(row);
        });
        groups.forEach(rows => {
            // Search the displayed mission line, never hidden option lists,
            // CSRF values, history/DETAILS or another visit's evidence.
            const primary = rows.find(row => row.matches('.neoscorpion-dispatch-primary-row'));
            const values = primary ? Array.from(primary.querySelectorAll('input:not([type=hidden]), select'))
                .filter(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden')
                .map(el => el.tagName === 'SELECT' ? el.selectedOptions[0]?.textContent || '' : el.value) : [];
            const labels = [];
            if (primary) {
                const walker = document.createTreeWalker(primary, NodeFilter.SHOW_TEXT);
                while (walker.nextNode()) {
                    const parent = walker.currentNode.parentElement;
                    if (!parent.closest('select, option, script, style') && parent.getClientRects().length
                            && getComputedStyle(parent).visibility !== 'hidden') labels.push(walker.currentNode.textContent);
                }
            }
            const text = [...labels, ...values].join(' ').toLocaleLowerCase();
            const match = !!query && text.includes(query);
            rows.forEach(row => {
                row.classList.toggle('is-search-match', match);
                row.classList.toggle('is-search-dim', !!query && !match);
            });
        });
        scope.querySelectorAll('[data-dispatch-search-clear]').forEach(button => button.hidden = !missionSearch);
    }
    let topOffAlertTimer = null;
    function updateTopOffAlerts(scope) {
        if (topOffAlertTimer !== null) window.clearTimeout(topOffAlertTimer);
        const now = Date.now();
        let next = 60000;
        scope.querySelectorAll('[data-dispatch-truck-id]').forEach(card => {
            const raw = card.dataset.topOffReturnUtc || '';
            // Only explicit timezone-bearing instants; no local HH:MM guessing.
            const eta = /(?:Z|[+-]\d\d:\d\d)$/.test(raw) ? Date.parse(raw) : NaN;
            const active = card.dataset.truckOperationalStatus === 'topping_off'
                && !card.querySelector('.neoscorpion-truck-activity')?.textContent.trim().startsWith('FUELING');
            const overdue = active && Number.isFinite(eta) && now > eta;
            if (overdue && !card.classList.contains('is-top-off-overdue')) {
                // A refreshed card resumes the same wall-clock animation phase.
                card.style.setProperty('--top-off-pulse-delay', `${-(now % 3000)}ms`);
            }
            card.classList.toggle('is-top-off-overdue', overdue);
            if (active && Number.isFinite(eta) && !overdue) next = Math.min(next, eta - now + 1);
        });
        topOffAlertTimer = window.setTimeout(() => updateTopOffAlerts(root), Math.max(1,next));
    }
    updateTopOffAlerts(root);
    document.addEventListener('visibilitychange', () => updateTopOffAlerts(root));
    window.addEventListener('pagehide', () => window.clearTimeout(topOffAlertTimer));
    window.addEventListener('pageshow', () => updateTopOffAlerts(root));
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

    function renderSortControls(scope) {
        scope.querySelectorAll("[data-dispatch-sort-mode]").forEach(button => {
            button.setAttribute("aria-pressed", String(button.dataset.dispatchSortMode === dispatchSortMode));
        });
        scope.querySelectorAll("[data-dispatch-arrived-first]").forEach(button => {
            button.setAttribute("aria-pressed", String((button.dataset.dispatchArrivedFirst === "1") === arrivedFirst));
        });
        scope.querySelectorAll("[data-dispatch-collapse]").forEach(button => {
            const trucks = button.dataset.dispatchCollapse === "trucks";
            const collapsed = trucks ? (document.body.classList.contains('operational-board-view') ? boardTruckCardsCollapsed : truckCardsCollapsed)
                : button.dataset.dispatchCollapse === 'statistics' ? statisticsCollapsed : spearDataCollapsed;
            button.setAttribute("aria-expanded", String(!collapsed));
            button.setAttribute("aria-label", `${collapsed ? "Expand" : "Collapse"} ${trucks ? "truck fuel status" : button.dataset.dispatchCollapse === 'statistics' ? 'Fueling Statistics' : "SPEAR data"}`);
            button.textContent = collapsed ? "EXPAND" : "COLLAPSE";
            const body = scope.querySelector(`[data-dispatch-section="${button.dataset.dispatchCollapse}"]`);
            if (body) body.hidden = collapsed;
        });
        scope.querySelectorAll("[data-dispatch-preference-status]").forEach(status => {
            const error = preferenceMessage.startsWith("NOT SAVED");
            status.textContent = error ? "NOT SAVED · select again" : preferenceMessage;
            status.title = preferenceMessage;
            status.classList.toggle("is-error", error);
        });
    }

    new MutationObserver(() => renderSortControls(root)).observe(document.body, {attributes:true,attributeFilter:['class']});

    function applyDispatchSort(scope) {
        renderSortControls(scope);
        const tbody = scope.querySelector(".neoscorpion-dispatch-table--compact tbody");
        if (!tbody) return;
        const groups = new Map();
        Array.from(tbody.children).forEach(row => {
            const id = row.dataset.dispatchSortGroup;
            if (!id) return;
            if (!groups.has(id)) groups.set(id, {rows: [], rank: Infinity, completed: false, present: false});
            const group = groups.get(id);
            group.rows.push(row);
            if (row.matches(".neoscorpion-dispatch-primary-row")) {
                group.rank = Number(row.dataset[dispatchSortMode === "etd" ? "dispatchEtdRank" : "dispatchArrivalRank"]);
                group.completed = row.classList.contains("is-complete");
                group.present = row.classList.contains("is-arrived") || row.classList.contains("is-on-ground");
            }
        });
        const focused = document.activeElement;
        const selection = focused?.selectionStart;
        const selectionEnd = focused?.selectionEnd;
        Array.from(groups.values()).sort((a, b) => Number(a.completed) - Number(b.completed)
            || (arrivedFirst && !a.completed ? Number(b.present) - Number(a.present) : 0)
            || a.rank - b.rank).forEach(group => {
            group.rows.forEach(row => {
                // State-preserving moves retain focus, comboboxes and edits.
                if (tbody.moveBefore) tbody.moveBefore(row, null);
                else tbody.appendChild(row);
            });
        });
        if (focused && document.activeElement !== focused && scope.contains(focused)) {
            focused.focus({preventScroll: true});
            if (typeof selection === "number") focused.setSelectionRange(selection, selectionEnd);
        }
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
            const manageAssets = event.target.closest("[data-assets-open]");
            if (manageAssets) {
                event.preventDefault();
                const workspace = scope.querySelector('[data-assets-workspace]');
                if (!workspace) return;
                workspace.open = true;
                const truck = scope.querySelector(`[id="nightly-truck-${manageAssets.dataset.assetsOpen}"]`);
                const target = truck || scope.querySelector('#nightly-trucks') || workspace;
                target.scrollIntoView({block:'start'});
                target.setAttribute('tabindex','-1');
                target.focus({preventScroll:true});
                return;
            }
            const truckReview = event.target.closest("[data-dispatch-truck-review]");
            if (truckReview) {
                const section = scope.querySelector('[data-dispatch-section="trucks"]');
                if (section?.hidden) scope.querySelector('[data-dispatch-collapse="trucks"]')?.click();
                const card = Array.from(scope.querySelectorAll('[data-dispatch-truck-id]'))
                    .find(item => item.dataset.dispatchTruckId === truckReview.dataset.dispatchTruckReview);
                card?.scrollIntoView({block: 'nearest'});
                card?.querySelector('input:not([type=hidden]), button, a')?.focus({preventScroll:true});
                return;
            }
            const toggle = event.target.closest("[data-neoscorpion-dispatch-details], [data-neoscorpion-mobile-details], [data-dispatch-completion-explain]");
            if (!toggle) return;
            event.preventDefault();
            const detail = document.getElementById(toggle.getAttribute("aria-controls"));
            if (!detail) return;
            const targeted = toggle.dataset.dispatchVisitTarget || toggle.dataset.dispatchActionTarget;
            const expanded = !targeted && toggle.getAttribute("aria-expanded") === "true";
            scope.querySelectorAll("[data-neoscorpion-dispatch-details], [data-neoscorpion-mobile-details]").forEach(button => {
                if (button.getAttribute("aria-controls") === detail.id) button.setAttribute("aria-expanded", String(!expanded));
            });
            detail.hidden = expanded;
            detail.setAttribute("aria-hidden", String(expanded));
            if (targeted) {
                const workspace = detail.querySelector(window.innerWidth <= 900 ? '.scorpion-mobile-readonly' : '.scorpion-desktop-console');
                const target = workspace?.querySelector(toggle.dataset.dispatchVisitTarget
                    ? `[data-console-visit="${toggle.dataset.dispatchVisitTarget}"]`
                    : `[data-action-key="${toggle.dataset.dispatchActionTarget}"]`);
                if (target) {
                    for (let parent = target.parentElement; parent && parent !== detail; parent = parent.parentElement) {
                        if (parent.tagName === 'DETAILS') parent.open = true;
                    }
                    if (toggle.dataset.dispatchVisitTarget) target.querySelector('[data-visit-evidence]')?.setAttribute('open','');
                    target.scrollIntoView({block:'nearest'});
                }
            }
        });
    }

    function initializeDispatchScroll(scope) {
        const storageKey = "neoapps.neoscorpion.fuel-dispatch.scroll.v3";
        const tableWrap = () => scope.querySelector(".neoscorpion-table-wrap--sticky");
        let completionSnapshot = null;
        let anchorStyle = null;
        const suspendNativeAnchor = () => {
            if (anchorStyle === null) anchorStyle = scope.style.overflowAnchor;
            scope.style.overflowAnchor = "none";
        };
        const releaseNativeAnchor = () => window.requestAnimationFrame(() => {
            window.requestAnimationFrame(() => {
                if (anchorStyle !== null) scope.style.overflowAnchor = anchorStyle;
                anchorStyle = null;
            });
        });
        const completionRestore = (saved, candidateScope = scope) => {
            const anchor = saved.rowKey && candidateScope.querySelector(
                `.neoscorpion-dispatch-table--compact tr[data-dispatch-row-key="${saved.rowKey}"]`);
            if (saved.completing || (saved.anchorComplete === false && anchor?.classList.contains("is-complete"))) {
                suspendNativeAnchor();
                return {...saved, rowKey: null, completing: true};
            }
            return saved;
        };

        // A cancelled uplift restores an earlier cycle key. Resolve only a
        // missing key to this mission's active row; existing history stays exact.
        const activeRowForKey = (key) => {
            const missionId = String(key || "").split("-")[0];
            if (!/^\d+$/.test(missionId)) return null;
            return Array.from(scope.querySelectorAll(
                ".neoscorpion-dispatch-primary-row[data-dispatch-row-key]:not([data-cycle-history])"
            )).find(row => row.dataset.dispatchRowKey.startsWith(`${missionId}-`)) || null;
        };

        const restoreDetailRows = (detailIds, activeDetailIds = []) => {
            if (!Array.isArray(detailIds)) return;
            detailIds.forEach((detailId) => {
                // Rollover freezes the former active ID into history. Keep an
                // open active editor on the mission's new cycle, while an
                // intentionally opened historical DETAILS stays historical.
                const active = activeDetailIds.includes(detailId)
                    ? activeRowForKey(detailId.replace("neoscorpion-dispatch-detail-", "")) : null;
                let toggle = active?.querySelector("[data-neoscorpion-dispatch-details]") || scope.querySelector(
                    `[data-neoscorpion-dispatch-details][aria-controls="${detailId}"]`
                );
                let detail = document.getElementById(toggle?.getAttribute("aria-controls") || detailId);
                if (!detail || !toggle) {
                    const row = activeRowForKey(detailId.replace("neoscorpion-dispatch-detail-", ""));
                    toggle = row?.querySelector("[data-neoscorpion-dispatch-details]");
                    detail = toggle ? document.getElementById(toggle.getAttribute("aria-controls")) : null;
                }
                if (!detail || !toggle) return;
                detail.hidden = false;
                detail.setAttribute("aria-hidden", "false");
                toggle.setAttribute("aria-expanded", "true");
                scope.querySelector(`[data-neoscorpion-mobile-details][aria-controls="${detail.id}"]`)?.setAttribute("aria-expanded", "true");
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
                anchorComplete: anchor?.classList.contains("is-complete") || false,
                windowMissionOffset: anchorRect?.top ?? null,
                tableMissionOffset: (
                    anchorRect && wrapRect
                        ? anchorRect.top - wrapRect.top
                        : null
                ),
                tableScrollTop: wrap?.scrollTop || 0,
                tableScrollLeft: wrap?.scrollLeft || 0,
                openDetails,
                assetWorkspaceOpen: scope.querySelector('[data-assets-workspace]')?.open,
                assetWorkspaceScrollTop: scope.querySelector('[data-assets-workspace]')?.scrollTop || 0,
                openAssetSections: Array.from(scope.querySelectorAll('[data-assets-disclosure][open]')).map(item => item.dataset.assetsDisclosure),
                openConsoleSections: Array.from(scope.querySelectorAll('details[data-console-section][open]'))
                    .map(item => [item.closest('[data-neoscorpion-dispatch-detail-row]')?.id, item.dataset.consoleSection]),
                openActiveDetails: Array.from(scope.querySelectorAll(
                    ".neoscorpion-dispatch-primary-row [data-neoscorpion-dispatch-details][aria-expanded='true']"
                )).map(toggle => toggle.getAttribute("aria-controls")).filter(Boolean),
            };
        };

        const applyRestore = (saved) => {
            if (!saved) return;
            saved = completionRestore(saved);
            if (saved.completing && document.activeElement?.closest(".is-complete")) {
                document.activeElement.blur();
            }
            restoreDetailRows(saved.openDetails, saved.openActiveDetails);
            const assets = scope.querySelector('[data-assets-workspace]');
            if (assets && typeof saved.assetWorkspaceOpen === 'boolean') assets.open = saved.assetWorkspaceOpen;
            (saved.openAssetSections || []).forEach(key => {
                Array.from(scope.querySelectorAll('[data-assets-disclosure]'))
                    .filter(item => item.dataset.assetsDisclosure === key).forEach(item => item.open = true);
            });
            if (assets && Number.isFinite(saved.assetWorkspaceScrollTop)) assets.scrollTop = saved.assetWorkspaceScrollTop;
            (saved.openConsoleSections || []).forEach(([id, key]) => {
                const row = scope.querySelector(`[id="${id}"]`);
                Array.from(row?.querySelectorAll('details[data-console-section]') || [])
                    .filter(item => item.dataset.consoleSection === key).forEach(item => item.open = true);
            });
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
                    releaseNativeAnchor();
                    window.sessionStorage.removeItem(storageKey);
                });
            } catch (_error) {
                // Scroll restoration must never interfere with Dispatch actions.
            }
        };

        restoreSession();
        return {
            snapshot,
            prepareRefresh: completionRestore,
            preserveCompletion(button) {
                // Cache before native POST/redirect. pagehide must not replace
                // this with an anchor pointing at the now-completed mission.
                completionSnapshot = {...snapshot(), rowKey: null, completing: true};
                button.blur();
                suspendNativeAnchor();
                this.preserve();
            },
            restoreSnapshot(saved) {
                applyRestore(saved);
                window.requestAnimationFrame(() => { applyRestore(saved); releaseNativeAnchor(); });
            },
            preserve() {
                try {
                    window.sessionStorage.setItem(storageKey, JSON.stringify(completionSnapshot || snapshot()));
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
                trigger.textContent = selected?.textContent?.trim() || "";
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
                if (!option.value) choice.setAttribute("aria-label", "Clear selection");
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
    const checkUrl = root.dataset.checkUrl;
    const spearActionUrl = root.dataset.spearActionUrl;
    const spearRecalculationMs = Math.max(
        60000,
        Number(root.dataset.spearRecalculationMs || 120000)
    );
    let spearRenderedAt = Date.now();
    let operationId = root.dataset.operationId || "none";
    let revision = Number(root.dataset.revision || 0);
    let tailRevision = root.dataset.tailRevision || "";
    let stateGeneration = 0;
    let latestObserved = {operation: operationId, revision, tail: tailRevision};
    const observeLiveState = (operation, nextRevision, tail) => {
        if (operation === latestObserved.operation && nextRevision < latestObserved.revision) return false;
        if (operation !== latestObserved.operation || nextRevision !== latestObserved.revision || tail !== latestObserved.tail) {
            latestObserved = {operation, revision: nextRevision, tail};
            stateGeneration += 1;
        }
        return true;
    };
    const currentResponse = (payload, generation) => generation === stateGeneration
        && String(payload.operation_id ?? "none") === latestObserved.operation
        && Number(payload.revision || 0) >= latestObserved.revision;
    let tailNotesRevision = 0;
    const initialControlValues = new WeakMap();
    let reloading = false;
    let lifecycleSaving = false;
    let dispatcherCheckSaving = 0;
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
        + "select[name='assigned_truck_id']:not([disabled]), [data-cycle-start] input:not([type=hidden]), "
        + "[data-dispatch-action-center] input:not([type=hidden]), [data-dispatch-action-center] select, [data-dispatch-action-center] textarea, "
        + "[data-pending-uplift-form] input:not([type=hidden]), [data-pending-uplift-form] select, "
        + ".scorpion-desktop-console input:not([type=hidden]):not([disabled]), .scorpion-desktop-console select:not([disabled]), .scorpion-desktop-console textarea:not([disabled]), "
        + "[data-assets-workspace] input:not([type=hidden]):not([disabled]), [data-assets-workspace] select:not([disabled]), [data-assets-workspace] textarea:not([disabled]), "
        + "[data-truck-card-actions] input:not([type=hidden])"
    )).filter(control => !control.closest("[data-tail-note-form]"));

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
        !!root.querySelector('[data-assignment-saving="true"], [data-assignment-failed="true"]') ||
        lifecycleSaving || !!root.querySelector('[data-truck-card-busy="true"]') || document.activeElement?.matches?.(".scorpion-desktop-console input:not([type=hidden]), .scorpion-desktop-console select, .scorpion-desktop-console textarea, [data-assets-workspace] input:not([type=hidden]), [data-assets-workspace] select, [data-assets-workspace] textarea") ||
        Array.from(root.querySelectorAll("[data-tail-note-form]")).some(form =>
            form.dataset.saving === "true" || form.elements.notes.value !== form.elements.notes.defaultValue) ||
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
        const nextOperation = String(payload.operation_id ?? "none");
        if (!observeLiveState(nextOperation, Number(payload.revision || 0), String(payload.tail_revision || ""))) return;
        if (payload.tail_notes_revision !== undefined) tailNotesRevision = Number(payload.tail_notes_revision);
        operationId = payload.operation_id === null
            ? "none"
            : String(payload.operation_id);
        revision = Number(payload.revision || 0);
        tailRevision = String(payload.tail_revision || "");
        root.dataset.operationId = operationId;
        root.dataset.revision = String(revision);
    };

    const updateNeoFuel = (missionId, presentation) => {
        if (!presentation) return;
        const row = root.querySelector(`[data-dispatch-mission-id='${missionId}']`);
        const value = row?.querySelector("[data-dispatch-neo-fuel]");
        if (!value) return;
        value.textContent = presentation.available ? presentation.display : "-";
        const shortfall = presentation.available === true && presentation.shortfall === true;
        value.classList.toggle("is-neo-fuel-excess", presentation.available === true && presentation.excess === true);
        value.classList.toggle("is-neo-fuel-shortfall", shortfall);
        row.querySelector("[data-dispatch-required-fuel]")?.classList.toggle("is-neo-fuel-shortfall", shortfall);
    };

    const saveDispatcherCheck = async (input) => {
        if (!checkUrl || input.disabled) return;
        const previous = input.dataset.savedChecked === "1";
        const status = input.parentElement.querySelector("[data-dispatch-check-status]");
        const body = new FormData();
        body.set("mission_id", input.dataset.missionId);
        body.set("operation_id", input.dataset.operationId);
        body.set("checked", input.checked ? "1" : "0");
        body.set("expected_checked", previous ? "1" : "0");
        dispatcherCheckSaving += 1;
        input.disabled = true;
        input.title = "Saving personal check…";
        setStatus(status, "Saving personal check");
        try {
            const response = await fetch(checkUrl, {
                method: "POST", body, cache: "no-store", credentials: "same-origin",
                headers: {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok !== true) throw new Error(payload.error || "Checkbox save failed.");
            input.checked = payload.checked === true;
            input.dataset.savedChecked = input.checked ? "1" : "0";
            input.title = "Personal dispatcher check saved";
            input.removeAttribute("aria-invalid");
            setStatus(status, "Personal check saved");
        } catch (error) {
            input.checked = previous;
            input.title = `Save Failed: ${error.message}`;
            input.setAttribute("aria-invalid", "true");
            setStatus(status, input.title, "error");
        } finally {
            input.disabled = false;
            dispatcherCheckSaving -= 1;
            if (pendingFuelDataRefresh && !hasUnsavedControls()) reloadPage();
        }
    };

    let pendingFuelDataRefresh = false;
    let criticalRefreshKey = "";
    let criticalRefreshBusy = false;

    const applyCriticalCompletion = (nextPanel) => {
        const scrollState = typeof dispatchScroll === 'undefined' ? null
            : dispatchScroll.prepareRefresh(dispatchScroll.snapshot(), nextPanel);
        let rowHeightChanged = false;
        // Shared presentation settings must update even when another row's
        // unsaved inputs defer full replacement. Never replace those inputs.
        const currentPreferences = root.querySelector('[data-dispatch-preferences]');
        const nextPreferences = nextPanel.matches?.('[data-dispatch-preferences]')
            ? nextPanel : nextPanel.querySelector('[data-dispatch-preferences]');
        if (currentPreferences && nextPreferences) {
            currentPreferences.dataset.readyAssignmentPulse = nextPreferences.dataset.readyAssignmentPulse;
            const percent = Number(nextPreferences.dataset.missionRowHeightPercent || 100);
            const height = Number.isInteger(percent) && percent >= 75 && percent <= 150 ? percent : 100;
            rowHeightChanged = currentPreferences.dataset.missionRowHeightPercent !== String(height);
            if (rowHeightChanged) {
                currentPreferences.dataset.missionRowHeightPercent = String(height);
                currentPreferences.style.setProperty('--dispatch-row-height-scale', String(height / 100));
            }
        }
        let regrouped = false;
        const groupDirty = nodes => nodes.some(node => node.querySelector?.('[data-assignment-saving="true"], [data-assignment-failed="true"]') || Array.from(node.querySelectorAll('input,select,textarea')).some(control => {
            if (control.type === 'hidden' || control.disabled || control.readOnly) return false;
            if (initialControlValues.has(control)) return initialControlValues.get(control) !== controlValue(control);
            if (control.matches('[data-dispatch-autosave]')) return control.dataset.autosaveSaving === 'true' || control.value.trim() !== (control.dataset.savedValue || '');
            if (control.type === 'checkbox') return control.checked !== (control.dataset.savedChecked === '1');
            return control.tagName !== 'SELECT' && control.value !== control.defaultValue;
        }));
        const reconcileTruckActions = (actions, nextActions) => {
            const editing=actions?.contains(document.activeElement)
                && !document.activeElement?.closest('form')?.dataset.truckCardSettled;
            if (actions && nextActions && !editing && !groupDirty([actions])
                    && !actions.querySelector('[data-truck-card-busy="true"]')) {
                actions.replaceWith(nextActions.cloneNode(true));
                protectedControls().forEach(control=>{
                    if(!initialControlValues.has(control))initialControlValues.set(control,controlValue(control));
                });
            }
        };
        const live = new Map(Array.from(root.querySelectorAll(
            ".neoscorpion-dispatch-primary-row[data-dispatch-mission-id]"
        ), row => [row.dataset.dispatchMissionId, row]));
        nextPanel.querySelectorAll(".neoscorpion-dispatch-primary-row[data-dispatch-mission-id]").forEach(nextRow => {
            const missionId = nextRow.dataset.dispatchMissionId;
            const currentRow = live.get(missionId);
            if (!currentRow) return;
            const currentStack = currentRow.querySelector(".neoscorpion-dispatch-action-stack");
            const nextStack = nextRow.querySelector(".neoscorpion-dispatch-action-stack");
            if (!currentStack || !nextStack) return;
            const oldSlot = currentStack.querySelector('[data-dispatch-completion-slot]');
            const newSlot = nextStack.querySelector('[data-dispatch-completion-slot]');
            const oldIdentity = oldSlot?.dataset.completionIdentity.split(':') || [];
            const newIdentity = newSlot?.dataset.completionIdentity.split(':') || [];
            // A board opened before the first preparation save has no Work ID.
            // Its first visit can safely acquire an independent approval form;
            // a previously allocated visit must never be silently substituted.
            const sameVisit = oldSlot && newSlot && (oldSlot.dataset.completionIdentity === newSlot.dataset.completionIdentity
                || (['', 'None'].includes(oldIdentity[5]) && oldIdentity.length === newIdentity.length
                    && oldIdentity.every((part,index) => index === 5 || part === newIdentity[index])));
            const tbody = currentRow.closest?.('tbody');
            const group = tbody ? Array.from(tbody.children).filter(node => node.dataset.dispatchSortGroup === missionId) : [];
            const dirty = group.length && groupDirty(group);
            if (tbody && !dirty && (!sameVisit || currentRow.classList.contains('is-complete') !== nextRow.classList.contains('is-complete'))) {
                // Reconcile only this clean mission, not another operator's
                // draft. Includes history/DETAILS and pending-cycle promotion.
                const incoming = Array.from(nextRow.closest('tbody').children).filter(node => node.dataset.dispatchSortGroup === missionId);
                incoming.forEach(node => tbody.insertBefore(node.cloneNode(true),group[0]));
                group.forEach(node => node.remove());
                regrouped = true;
                return;
            }
            if (sameVisit) oldSlot.replaceWith(newSlot.cloneNode(true));
            else if (oldSlot) {
                // Never attach a new cycle's approval to an older visible tail
                // or form. Keep dirty edits, but require identity reconciliation.
                oldSlot.querySelector('button')?.remove();
                oldSlot.textContent = 'VISIT CHANGED';
                oldSlot.title = 'SAVE OR CANCEL THIS MISSION’S EDITS TO REFRESH THE CHANGED VISIT';
            }
            if (!sameVisit) {
                // A protected draft may still show the old visit until it is
                // reconciled. Never keep its READY attention animation running
                // after the authoritative resource/visit identity changed.
                currentRow.classList?.toggle('is-ready-to-assign', false);
                currentRow.classList?.toggle('is-assignment-alert-red', false);
            }
            if (dirty) currentStack.querySelector('button[formaction$="/fuel-dispatch/complete"]')?.setAttribute('disabled','');
            if (sameVisit) {
                const oldStatus = currentRow.querySelector('[data-fuel-status]');
                const newStatus = nextRow.querySelector('[data-fuel-status]');
                // Same-stage messages, forecast metadata and urgency can change
                // while a different field/dialog prevents full-panel refresh.
                if (oldStatus && newStatus && oldStatus.outerHTML !== newStatus.outerHTML) oldStatus.replaceWith(newStatus.cloneNode(true));
                currentRow.classList?.toggle('is-ready-to-assign', nextRow.classList?.contains('is-ready-to-assign') || false);
                currentRow.classList?.toggle('is-assignment-alert-red', nextRow.classList?.contains('is-assignment-alert-red') || false);
                if (nextRow.dataset.etdUtc) currentRow.dataset.etdUtc = nextRow.dataset.etdUtc;
                else delete currentRow.dataset.etdUtc;
                ['[data-dispatch-neo-fuel]', '[data-dispatch-actual]', '[data-dispatch-apu-effective]', '.neoscorpion-dispatch-center-fuel'].forEach(selector => {
                    const oldValue = currentRow.querySelector(selector), newValue = nextRow.querySelector(selector);
                    if (oldValue && newValue) { oldValue.textContent = newValue.textContent; oldValue.className = newValue.className; }
                });
                // Patch comparison styling even while dirty inputs/dialogs defer
                // full replacement. Never overwrite the editable Fuel Load value.
                currentRow.querySelector('[data-dispatch-required-fuel]')?.classList?.toggle(
                    'is-neo-fuel-shortfall',
                    nextRow.querySelector('[data-dispatch-neo-fuel]')?.classList?.contains('is-neo-fuel-shortfall') || false);
            }
            const detailFor = (scope, row) => {
                const id = row.querySelector('[data-neoscorpion-dispatch-details]')?.getAttribute('aria-controls');
                return id ? Array.from(scope.querySelectorAll('.neoscorpion-dispatch-detail-row')).find(item => item.id === id) : null;
            };
            const currentDetails = detailFor(root, currentRow);
            const nextDetails = detailFor(nextPanel, nextRow);
            // Read-only visit/plan lines carry no drafts. Keep their independent
            // receipts visible even while another form defers a full refresh.
            const lineSelector = `[data-truck-lines-mission="${missionId}"]`;
            const oldLines = Array.from(root.querySelectorAll(lineSelector));
            const newLines = Array.from(nextPanel.querySelectorAll(lineSelector), node => node.cloneNode(true));
            newLines.forEach(node => node.querySelectorAll('[aria-controls]').forEach(button => {
                button.setAttribute('aria-expanded',String(Boolean(currentDetails && !currentDetails.hidden)));
            }));
            oldLines.forEach(node => node.remove());
            if (newLines.length) currentRow.after(...newLines);
            // Completion-only responses intentionally omit expensive SPEAR
            // enrichment. Omission is not evidence that forecasts disappeared.
            const hasSpear = nextDetails?.querySelector('[data-console-region="spear"]')?.dataset.spearAvailable !== 'false';
            const oldStatusDetails=currentDetails?.querySelector('[data-fuel-status-details]');
            const newStatusDetails=nextDetails?.querySelector('[data-fuel-status-details]');
            if (sameVisit && oldStatusDetails && newStatusDetails && !groupDirty([oldStatusDetails])) {
                oldStatusDetails.replaceWith(newStatusDetails.cloneNode(true));
            }
            const oldMobileDetails=currentDetails?.querySelector('.scorpion-mobile-readonly');
            const newMobileDetails=nextDetails?.querySelector('.scorpion-mobile-readonly');
            if (sameVisit && oldMobileDetails && newMobileDetails) oldMobileDetails.replaceWith(newMobileDetails.cloneNode(true));
            const oldTimeline=currentDetails?.querySelector('[data-dispatch-work-timeline]');
            const newTimeline=nextDetails?.querySelector('[data-dispatch-work-timeline]');
            if (oldTimeline && newTimeline && !groupDirty([oldTimeline]) && !oldTimeline.contains(document.activeElement)) {
                const timeline=newTimeline.cloneNode(true);
                // Assignment-change disclosures live inside the read-only
                // timeline. Keep them open during critical status patches.
                const disclosureKey=item=>item.dataset.consoleSection ||
                    `${item.closest('[data-console-visit]')?.dataset.consoleVisit || 'current'}:${item.querySelector('summary')?.textContent}`;
                const openDisclosures=new Set(Array.from(oldTimeline.querySelectorAll('details[open]'),disclosureKey));
                timeline.querySelectorAll('details').forEach(item=>item.open=openDisclosures.has(disclosureKey(item)));
                oldTimeline.replaceWith(timeline);
                timeline.querySelectorAll('input,select,textarea').forEach(control=>initialControlValues.set(control,controlValue(control)));
            }
            // Read-only receipts refresh independently of dirty assignment/notes
            // controls, including when a resource swap allocated a new visit.
            const oldSent=currentDetails?.querySelector('[data-dispatch-assignment-times]');
            const newSent=nextDetails?.querySelector('[data-dispatch-assignment-times]');
            if (oldSent && newSent && oldSent.outerHTML !== newSent.outerHTML) {
                const copy=newSent.cloneNode(true);
                if (copy.querySelector('details') && oldSent.querySelector('details')?.open) copy.querySelector('details').open=true;
                oldSent.replaceWith(copy);
            }
            if (sameVisit && currentDetails && nextDetails) {
                // Empty read-only columns disappear, but can become applicable
                // on the next poll without replacing editable sibling regions.
                ['planning','spear'].forEach(region => {
                    if (region === 'spear' && !hasSpear) return;
                    const oldRegion=currentDetails.querySelector(`[data-console-region="${region}"]`);
                    const newRegion=nextDetails.querySelector(`[data-console-region="${region}"]`);
                    if (oldRegion && newRegion) oldRegion.hidden=newRegion.hidden && !groupDirty([oldRegion]);
                });
                const incoming = new Map(Array.from(nextDetails.querySelectorAll('details[data-console-section]')).filter(item => !item.closest('[data-dispatch-action-center]') && (hasSpear || !item.closest('[data-console-region="spear"]'))).map(
                    item => [item.dataset.consoleSection, item]));
                Array.from(currentDetails.querySelectorAll('details[data-console-section]')).filter(item => !item.closest('[data-dispatch-action-center]')).forEach(item => {
                    if (!hasSpear && item.closest('[data-console-region="spear"]')) {
                        incoming.delete(item.dataset.consoleSection);
                        return;
                    }
                    const fresh = incoming.get(item.dataset.consoleSection);
                    if (!groupDirty([item]) && !item.contains(document.activeElement)) {
                        if (!fresh) item.remove();
                        else {
                            const copy = fresh.cloneNode(true);
                            copy.open = item.open;
                            if (item.outerHTML !== copy.outerHTML) item.replaceWith(copy);
                        }
                    } else if (!fresh) {
                        item.querySelectorAll('button[type="submit"], button:not([type])').forEach(button => button.disabled = true);
                    } else if (fresh.dataset.operationUnavailable !== undefined) {
                        // Keep the draft, focus and disclosure state, while
                        // current prerequisites still govern execution.
                        item.querySelectorAll('button[type="submit"], button:not([type])').forEach(button => {
                            button.disabled = fresh.dataset.operationUnavailable === 'true';
                        });
                        item.querySelector('summary')?.replaceWith(fresh.querySelector('summary').cloneNode(true));
                    }
                    incoming.delete(item.dataset.consoleSection);
                });
                incoming.forEach(item => {
                    const region=item.closest('[data-console-region]')?.dataset.consoleRegion;
                    const destination=region ? currentDetails.querySelector(`[data-console-region="${region}"]`) :
                        currentDetails.querySelector('[data-details-region="secondary"]');
                    destination?.appendChild(item.cloneNode(true));
                });
                protectedControls().filter(control => currentDetails.contains(control)).forEach(control => {
                    if (!initialControlValues.has(control)) initialControlValues.set(control,controlValue(control));
                });
            }
            // Independently addressed actions must appear even while another
            // mission/plan is being edited or the Fueler Data dialog is open.
            // Preserve dirty action inputs, not an entire stale Action Center.
            const oldCenter=currentDetails?.querySelector('[data-dispatch-action-center]');
            const newCenter=nextDetails?.querySelector('[data-dispatch-action-center]');
            if (oldCenter && newCenter) {
                oldCenter.hidden = newCenter.hidden && !groupDirty([oldCenter]);
                const incoming=new Map(Array.from(newCenter.querySelectorAll('[data-action-key]'),item=>[item.dataset.actionKey,item]));
                oldCenter.querySelectorAll('[data-action-key]').forEach(item=>{
                    const fresh=incoming.get(item.dataset.actionKey);
                    if (!groupDirty([item]) && !(fresh && item.contains(document.activeElement))) {
                        if (fresh && item.outerHTML !== fresh.outerHTML) item.replaceWith(fresh.cloneNode(true)); else if (!fresh) item.remove();
                    } else if (!fresh) {
                        // Retain the draft but never leave a resolved/stale
                        // action executable against changed evidence.
                        item.querySelectorAll('button[type="submit"], button:not([type])').forEach(button=>button.disabled=true);
                        const status=item.querySelector('[data-lifecycle-status]');
                        if (status) status.textContent='ACTION CHANGED — review refreshed state before submitting.';
                    }
                    incoming.delete(item.dataset.actionKey);
                });
                incoming.forEach(item=>oldCenter.appendChild(item.cloneNode(true)));
                oldCenter.querySelector('header').replaceWith(newCenter.querySelector('header').cloneNode(true));
                oldCenter.querySelectorAll(':scope > small').forEach(item=>item.remove());
                newCenter.querySelectorAll(':scope > small').forEach(item=>oldCenter.appendChild(item.cloneNode(true)));
                oldCenter.querySelectorAll('input,select,textarea').forEach(control=>{
                    if (!initialControlValues.has(control)) initialControlValues.set(control,controlValue(control));
                });
            }
            ['[data-dispatch-action-count]', '[data-mobile-dispatch-action-count]'].forEach(selector => {
                const oldIndicator=currentRow.querySelector(selector);
                const newIndicator=nextRow.querySelector(selector);
                if (oldIndicator && newIndicator) oldIndicator.replaceWith(newIndicator.cloneNode(true));
            });
            const currentHandoff = currentDetails?.querySelector("[data-dispatch-fueler-submission]");
            const nextHandoff = nextDetails?.querySelector("[data-dispatch-fueler-submission]");
            if (sameVisit && currentHandoff && nextHandoff) currentHandoff.textContent = nextHandoff.textContent;
            if (sameVisit) ['[data-dispatch-console-facts]', '[data-dispatch-console-planning]', '[data-dispatch-visit-notes]', '[data-dispatch-spear-summary]'].forEach(selector => {
                if (selector === '[data-dispatch-spear-summary]' && !hasSpear) return;
                const oldValue = currentDetails?.querySelector(selector), newValue = nextDetails?.querySelector(selector);
                if (oldValue && newValue && oldValue.outerHTML !== newValue.outerHTML && !oldValue.contains(document.activeElement)) oldValue.replaceWith(newValue.cloneNode(true));
            });
            const oldTail = currentRow.querySelector(".neoscorpion-dispatch-tail strong");
            const newTail = nextRow.querySelector(".neoscorpion-dispatch-tail strong");
            const previousWarning = currentStack.querySelector("[data-dispatch-tail-change-warning]");
            if (oldTail && newTail && oldTail.textContent.trim() !== newTail.textContent.trim()) {
                const warning = previousWarning || document.createElement("small");
                warning.setAttribute("data-dispatch-tail-change-warning", "");
                warning.className = "neoscorpion-calculation-warning";
                warning.textContent = "TAIL CHANGED TO " + newTail.textContent.trim()
                    + " · SAVE OR CANCEL EDITS TO REFRESH";
                if (!previousWarning) currentStack.insertBefore(warning, currentStack.firstChild);
            } else if (previousWarning) {
                previousWarning.remove();
            }
        });
        if (regrouped) {
            initializeDispatchSelects(root);
            initializeDispatchApuEditors(root);
            protectedControls().forEach(control => {
                if (!initialControlValues.has(control)) initialControlValues.set(control,controlValue(control));
            });
            applyDispatchSort(root);
            if (scrollState) dispatchScroll.restoreSnapshot(scrollState);
        }
        const oldAssets=root.querySelector('[data-assets-workspace]'), newAssets=nextPanel.querySelector('[data-assets-workspace]');
        if (oldAssets && newAssets && oldAssets.dataset.assetsOperationId === newAssets.dataset.assetsOperationId) {
            const incoming=new Map(Array.from(newAssets.querySelectorAll('[data-assets-unit]'),item=>[item.dataset.assetsUnit,item]));
            oldAssets.querySelectorAll('[data-assets-unit]').forEach(item=>{
                const fresh=incoming.get(item.dataset.assetsUnit);
                const editing=item.contains(document.activeElement) && document.activeElement?.matches('input,select,textarea');
                if (!groupDirty([item]) && !editing && !lifecycleSaving && !item.querySelector('[data-truck-card-busy="true"]')) {
                    if (!fresh) item.remove();
                    else {
                        const copy=fresh.cloneNode(true);
                        item.querySelectorAll('[data-assets-disclosure][open]').forEach(open=>{
                            Array.from(copy.querySelectorAll('[data-assets-disclosure]')).filter(n=>n.dataset.assetsDisclosure===open.dataset.assetsDisclosure).forEach(n=>n.open=true);
                        });
                        if(item.outerHTML!==copy.outerHTML)item.replaceWith(copy);
                    }
                } else if (fresh) {
                    ['[data-assets-truck-facts]','[data-assets-driver-name]'].forEach(selector=>{
                        const old=item.querySelector(selector),next=fresh.querySelector(selector);
                        if(old&&next)old.replaceWith(next.cloneNode(true));
                    });
                    reconcileTruckActions(item.querySelector('[data-assets-top-off-actions]'),fresh.querySelector('[data-assets-top-off-actions]'));
                } else {
                    item.querySelectorAll('button[type="submit"],button:not([type])').forEach(button=>button.disabled=true);
                    const status=item.querySelector('[data-lifecycle-status]');
                    if(status)status.textContent='STATE CHANGED — refresh this request before submitting.';
                }
                incoming.delete(item.dataset.assetsUnit);
            });
            incoming.forEach(item=>{
                const slot=item.dataset.assetsUnit.startsWith('account-')?'[data-assets-account-list]':item.dataset.assetsUnit.startsWith('truck-')?'[data-assets-truck-list]':null;
                if(slot)oldAssets.querySelector(slot)?.appendChild(item.cloneNode(true));
            });
            if(oldAssets.querySelector('[data-assets-unit^="truck-"]'))oldAssets.querySelector('[data-assets-empty]')?.remove();
            protectedControls().filter(control=>oldAssets.contains(control)).forEach(control=>{
                if(!initialControlValues.has(control))initialControlValues.set(control,controlValue(control));
            });
        }
        // Truck physical status must not be frozen by a mission/inventory draft.
        // Preserve every editable control and optional planning enrichment.
        nextPanel.querySelectorAll('[data-dispatch-truck-id]').forEach(nextCard => {
            const currentCard=Array.from(root.querySelectorAll('[data-dispatch-truck-id]')).find(card => card.dataset.dispatchTruckId===nextCard.dataset.dispatchTruckId);
            if (!currentCard) return;
            const oldBadge=currentCard.querySelector('.neoscorpion-truck-activity');
            const newBadge=nextCard.querySelector('.neoscorpion-truck-activity');
            if (oldBadge && newBadge) oldBadge.replaceWith(newBadge.cloneNode(true));
            if (currentCard.dataset.truckOperationalStatus!==nextCard.dataset.truckOperationalStatus || newBadge?.textContent.trim().startsWith('FUELING')) {
                const oldNotes=currentCard.querySelector('[data-truck-status-notes]');
                const newNotes=nextCard.querySelector('[data-truck-status-notes]');
                if (oldNotes && newNotes) oldNotes.replaceWith(newNotes.cloneNode(true));
            }
            currentCard.dataset.truckOperationalStatus=nextCard.dataset.truckOperationalStatus;
            currentCard.dataset.topOffReturnUtc=nextCard.dataset.topOffReturnUtc || '';
            reconcileTruckActions(currentCard.querySelector('[data-truck-card-actions]'),nextCard.querySelector('[data-truck-card-actions]'));
            ['.neoscorpion-truck-visual-values','.neoscorpion-truck-gauges','.neoscorpion-truck-driver-name'].forEach(selector=>{
                const oldValue=currentCard.querySelector(selector),nextValue=nextCard.querySelector(selector);
                if(oldValue && nextValue && oldValue.outerHTML!==nextValue.outerHTML && !oldValue.contains(document.activeElement))oldValue.replaceWith(nextValue.cloneNode(true));
            });
        });
        updateAssignmentAlerts(root);
        updateTopOffAlerts(root);
        applyMissionSearch(root);
        if (rowHeightChanged && !regrouped && scrollState) dispatchScroll.restoreSnapshot(scrollState);
    };

    const refreshCriticalCompletion = async (nextOperationId, nextRevision, nextTailRevision) => {
        const key = [nextOperationId, nextRevision, nextTailRevision].join(":");
        if (criticalRefreshBusy || criticalRefreshKey === key || !panelUrl
                || nextOperationId !== operationId) return false;
        criticalRefreshBusy = true;
        const requestGeneration = stateGeneration;
        try {
            const criticalUrl = new URL(panelUrl, window.location.href);
            criticalUrl.searchParams.set("completion_only", "1");
            const response = await fetch(criticalUrl, {
                cache: "no-store", credentials: "same-origin",
                headers: {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok !== true || !payload.html
                    || String(payload.operation_id) !== nextOperationId || !currentResponse(payload, requestGeneration)) return false;
            const holder = document.createElement("template");
            holder.innerHTML = payload.html.trim();
            const nextPanel = holder.content.firstElementChild;
            if (!nextPanel?.matches(".neoscorpion-panel")) return false;
            // Deliberately update *only* read-only handoff text and server-rendered
            // COMPLETE actions. Full page replacement still waits for clean inputs.
            applyCriticalCompletion(nextPanel);
            stateGeneration += 1;
            criticalRefreshKey = key;
            return true;
        } catch (error) {
            const status = root.querySelector("[data-spear-fleet-status]");
            setStatus(status, "CRITICAL REFRESH: " + (error.message || "Retrying"), "error");
            return false;
        } finally {
            criticalRefreshBusy = false;
        }
    };

    const reloadPage = async () => {
        // Protect personal saves from markup replacement without making them
        // operational dirty controls or delaying SPEAR automation.
        if (dispatcherCheckSaving > 0 || preferenceSaving) {
            pendingFuelDataRefresh = true;
            return false;
        }
        if (window.NeoScorpionFuelData?.isOpen()) {
            pendingFuelDataRefresh = true;
            return false;
        }
        if (reloading || hasUnsavedControls() || !panelUrl) return false;
        reloading = true;
        const requestGeneration = stateGeneration;
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
            if (!currentResponse(payload, requestGeneration)) return false;
            // Input may have become dirty while the network request was in
            // flight. Never replace live operator typing with fetched markup.
            if (hasUnsavedControls() || dispatcherCheckSaving > 0 || preferenceSaving) {
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
            // A dispatcher may scroll while the panel request is in flight.
            // Restore the working position at replacement, not request start.
            const saved = dispatchScroll.prepareRefresh(dispatchScroll.snapshot(), nextPanel);
            initializeDispatchRowHeight(nextPanel);
            // Keep the actual toolbar node, query and selection through refresh.
            const toolbar = currentPanel.querySelector('.neoscorpion-dispatch-sort');
            const search = toolbar?.querySelector('[data-dispatch-search]');
            const searchFocused = search === document.activeElement;
            const selection = searchFocused ? [search.selectionStart, search.selectionEnd] : null;
            const incomingToolbar = nextPanel.querySelector('.neoscorpion-dispatch-sort');
            if (toolbar && incomingToolbar) incomingToolbar.replaceWith(toolbar);
            currentPanel.replaceWith(nextPanel);
            if (searchFocused && search?.isConnected) {
                search.focus({preventScroll:true});
                search.setSelectionRange(...selection);
            }
            const preferences = nextPanel.matches("[data-dispatch-preferences]") ? nextPanel : nextPanel.querySelector("[data-dispatch-preferences]");
            if (preferences && Number(preferences.dataset.preferenceVersion || 0) >= preferenceVersion && !preferenceMessage.startsWith("NOT SAVED")) {
                adoptPreferences({secondary_sort: preferences.dataset.sortMode,
                    arrived_first: preferences.dataset.arrivedFirst !== "0",
                    truck_cards_collapsed: preferences.dataset.truckCardsCollapsed === "1",
                    board_truck_cards_collapsed: preferences.dataset.boardTruckCardsCollapsed === "1",
                    statistics_collapsed: preferences.dataset.statisticsCollapsed === "1",
                    spear_data_collapsed: preferences.dataset.spearDataCollapsed === "1",
                    version: preferences.dataset.preferenceVersion});
            }
            applyDispatchSort(root);
            updateAssignmentAlerts(root);
            updateTopOffAlerts(root);
            applyMissionSearch(root);
            adoptFingerprint(payload);
            root.dataset.spearPlanToken = payload.spear_plan_token || "";
            root.dataset.spearAutomaticReference = payload.spear_automatic_reference || "";
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

    const handleChangedFingerprint = async (nextOperationId, nextRevision, nextTailRevision) => {
        // Publish operational handoff first. Optional SPEAR planning must not
        // hold the COMPLETE control behind an expensive full-panel request.
        const critical = await refreshCriticalCompletion(nextOperationId, nextRevision, nextTailRevision);
        if (!hasUnsavedControls()) {
            if (await reloadPage()) return true;
        }
        // A dispatcher may have unsaved edits or an OPEN Fueler Data dialog.
        // Those states must not hide a newly valid COMPLETE action for minutes.
        pendingFuelDataRefresh = true;
        return critical;
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
        const nextTailRevision = String(payload.tail_revision || "");
        if (!observeLiveState(nextOperationId, nextRevision, nextTailRevision)) return;
        if (nextOperationId !== operationId || nextRevision !== revision
                || nextTailRevision !== tailRevision
                || (pendingFuelDataRefresh && criticalRefreshKey !== [nextOperationId,nextRevision,nextTailRevision].join(':'))) {
            // Concurrent autosaves can adopt a revision while its critical GET
            // is busy/discarded. A dirty form must not suppress the next read
            // merely because that revision is already known locally.
            window.NeoScorpionFuelData?.revisionChanged(nextRevision, nextOperationId);
            await handleChangedFingerprint(nextOperationId, nextRevision, nextTailRevision);
        } else if (Number(payload.tail_notes_revision || 0) !== tailNotesRevision) {
            pendingFuelDataRefresh = true;
            if (!hasUnsavedControls()) await reloadPage();
        } else if (pendingFuelDataRefresh && !hasUnsavedControls()) {
            await reloadPage();
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
            updateNeoFuel(missionId, payload.neo_fuel);
            if (payload.changed) {
                pendingFuelDataRefresh = true;
                // Required-fuel edits can create a mandatory next-visit review.
                // Adopting our revision must not hide that action merely
                // because an unrelated mission still has unsaved controls.
                await refreshCriticalCompletion(String(payload.operation_id ?? 'none'),
                    Number(payload.revision || 0),String(payload.tail_revision || ''));
            }
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
            if (pendingFuelDataRefresh && !hasUnsavedControls()) await reloadPage();
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
        const editor = button?.closest("[data-dispatch-apu-editor]")
            || form.querySelector("[data-dispatch-apu-editor]");
        if (effective) {
            if (effectiveLbs === null || effectiveLbs === undefined) {
                effective.textContent = "APU: -";
            } else {
                const thousands = Number(effectiveLbs) / 1000;
                effective.textContent = `APU: ${thousands.toFixed(1)}`;
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
        // The compact table uses external form-associated controls. Feedback
        // lives in the ACTION cell, not inside its hidden-input-only form.
        const status = form.closest('[data-dispatch-mission-id]')?.querySelector("[data-assignment-save-status]")
            || form.querySelector("[data-assignment-save-status]");
        const errors = root.querySelector('[data-assignment-errors]');
        const previousError = errors?.querySelector(`[data-assignment-error-form="${form.id}"]`);
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
        form.dataset.assignmentFailed = "false";
        button.disabled = true;
        setStatus(status, "Saving...");
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 30000);
        let rejected = false;
        try {
            const response = await fetch(form.action, {
                method: "POST",
                body: new FormData(form),
                cache: "no-store",
                credentials: "same-origin",
                signal: controller.signal,
                headers: {
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok !== true) {
                rejected = response.status >= 400 && response.status < 500 && payload.ok === false;
                throw new Error(payload.error || "Assignment save failed.");
            }
            adoptFingerprint(payload);
            updateAssignmentBaseline(form, payload);
            previousError?.remove();
            if (errors) errors.hidden = !errors.children.length;
            updateApuAllowanceDisplay(form, payload, button);
            updateNeoFuel(form.elements.namedItem("mission_id")?.value, payload.neo_fuel);
            if (payload.changed) pendingFuelDataRefresh = true;
            button.textContent = payload.button_label || "UPDATE ASSIGNMENT";
            setStatus(status, payload.changed ? "Saved" : "No change");
            if (payload.changed && resourceChangeRequested) {
                await reloadPage();
                return;
            }
        } catch (error) {
            form.dataset.assignmentFailed = "true";
            const message = rejected
                    ? `NOT SAVED: ${error.message || "Unable to update this assignment."} Your selections are unsaved. Reload Fuel Dispatch to check the saved assignment before trying again.`
                    : "RESULT UNKNOWN: Assignment confirmation was not received. Your selections are unsaved. Reload Fuel Dispatch and check the saved assignment before trying again; do not resend blindly.";
            setStatus(status, errors ? (rejected ? 'NOT SAVED · SEE ERROR' : 'RESULT UNKNOWN · SEE ERROR') : message, "error");
            if (errors) {
                const item = previousError || document.createElement('p');
                item.dataset.assignmentErrorForm = form.id;
                item.textContent = `Mission ${form.elements.namedItem('mission_id')?.value || ''}: ${message}`;
                if (!previousError) errors.appendChild(item);
                errors.hidden = false;
                errors.focus();
            }
        } finally {
            window.clearTimeout(timeout);
            form.dataset.assignmentSaving = "false";
            button.disabled = false;
            syncDirtyState();
            if (pendingFuelDataRefresh && !hasUnsavedControls()) await reloadPage();
        }
    };

    const submitLifecycleAction = async (form, button) => {
        if (lifecycleSaving) return;
        if (form.matches("[data-pending-uplift-form]") && button?.value === "retry"
                && protectedControls().some(control => control.form === form && initialControlValues.get(control) !== controlValue(control))) {
            setStatus(form.querySelector("[data-lifecycle-status]"), "Save pending uplift changes before retrying promotion.", "error");
            return;
        }
        const pendingControls = [];
        let status = form.querySelector("[data-lifecycle-status]");
        const assetsWorkspace=form.closest('[data-assets-workspace]');
        if(assetsWorkspace && !status) {
            status=document.createElement('small');
            status.setAttribute('data-lifecycle-status','');
            status.setAttribute('role','status');
            form.appendChild(status);
        }
        if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) return;
        lifecycleSaving = true;
        if (button) button.disabled = true;
        setStatus(status, "Saving...");
        syncDirtyState();
        try {
            const body = new FormData(form);
            if(assetsWorkspace) {
                body.set('dispatch_assets_workspace','1');
                body.set('operation_id',assetsWorkspace.dataset.assetsOperationId || '');
            }
            if (button?.name) body.set(button.name, button.value);
            if (form.matches("[data-pending-uplift-form]")) {
                Array.from(form.elements).filter(control => !control.disabled).forEach(control => {
                    pendingControls.push(control);
                    control.disabled = true;
                });
            }

            const response = await fetch(form.getAttribute("action"), {
                method: "POST", body, cache: "no-store", credentials: "same-origin",
                headers: {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok !== true) throw new Error(payload.error || "Action failed.");
            lifecycleSaving = false;
            // Accept only the values actually submitted in this action, not
            // newer typing or drafts elsewhere. Resolved review forms can then
            // disappear promptly rather than masquerading as unsaved work.
            Array.from(form.elements).forEach(control=>{
                if (!control.name || control.type==='hidden' || control.tagName==='BUTTON') return;
                const submitted=(control.type==='checkbox' || control.type==='radio')
                    ? (body.getAll(control.name).includes(control.value) ? '1':'0') : body.get(control.name);
                if (submitted===controlValue(control)) initialControlValues.set(control,controlValue(control));
            });
            if (form.matches("[data-pending-uplift-form]")) {
                if (form.elements.expected_version && payload.pending_version) form.elements.expected_version.value = String(payload.pending_version);
                protectedControls().filter(control => control.form === form)
                    .forEach(control => initialControlValues.set(control, controlValue(control)));
            }
            setStatus(status, payload.changed ? "Saved" : "No change");
            // The existing panel refresh preserves row/details/scroll and defers
            // while another field or the shared Fueler Data modal is in use.
            pendingFuelDataRefresh = true;
            if (payload.operation_id != null) {
                observeLiveState(String(payload.operation_id ?? 'none'),Number(payload.revision || 0),String(payload.tail_revision || ''));
                await handleChangedFingerprint(String(payload.operation_id ?? 'none'),Number(payload.revision || 0),String(payload.tail_revision || ''));
            } else await reloadPage();
        } catch (error) {
            setStatus(status, `Save Failed: ${error.message || "Unable to save."}`, "error");
        } finally {
            pendingControls.forEach(control => { control.disabled = false; });
            lifecycleSaving = false;
            if (button) button.disabled = false;
            syncDirtyState();
        }
    };

    const submitTruckCardAction = async (form, button) => {
        if (form.dataset.truckCardBusy === "true" || form.dataset.truckCardSettled) {
            return;
        }
        const status = form.querySelector("[data-dispatch-truck-card-status], [data-lifecycle-status]");
        form.dataset.truckCardBusy = "true";
        if (button) button.disabled = true;
        setStatus(status, "Saving...");
        const submitted = new FormData(form);
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 30000);
        let acknowledged = false;
        try {
            // The hidden operational field is named "action". HTML form named
            // properties can therefore shadow HTMLFormElement.action and turn
            // form.action into the input element instead of the endpoint URL.
            const actionUrl = form.getAttribute("action");
            const response = await fetch(actionUrl, {
                method: "POST",
                body: submitted,
                signal: controller.signal,
                cache: "no-store",
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
            });
            const payload = await response.json().catch(() => ({}));
            acknowledged = payload.ok === true || payload.ok === false;
            if (!response.ok || payload.ok !== true) {
                throw new Error(payload.error || "Truck update failed.");
            }
            // A committed mutation is successful even when a draft/dialog defers
            // full-panel replacement. Never leave Saving tied to that refresh.
            form.dataset.truckCardSettled = "saved";
            const action = submitted.get('action');
            setStatus(status, action === 'mark_topping_off' ? 'TOP OFF saved · TOPPING OFF'
                : action === 'complete_top_off' ? 'RETURN saved · AVAILABLE' : 'Saved', 'saved');
        } catch (error) {
            if (!acknowledged) form.dataset.truckCardSettled = "uncertain";
            setStatus(status, acknowledged ? `Save Failed: ${error.message || "Unable to update this truck."}`
                : 'RESULT UNKNOWN — check live truck status before retrying. No automatic retry.', "error");
        } finally {
            window.clearTimeout(timeout);
            form.dataset.truckCardBusy = "false";
            if (button) button.disabled = !!form.dataset.truckCardSettled;
            // Submitted readings are no longer an unsent draft, even when the
            // acknowledgement was lost. Preserve typing that happened later;
            // let a read-only refresh resolve the actual truck state. This does
            // not label an uncertain transaction successful or resend it.
            if (form.dataset.truckCardSettled) Array.from(form.elements).forEach(control=>{
                if(submitted.get(control.name) === controlValue(control))
                    initialControlValues.set(control,controlValue(control));
            });
            syncDirtyState();
            pendingFuelDataRefresh = true;
            criticalRefreshKey = "";
            stateGeneration += 1;
            // The acknowledgement need not include the operation/tail
            // fingerprint. Obtain it with a read, never a mutation retry.
            void poll().catch(() => {
                if(form.isConnected) setStatus(status, (form.dataset.truckCardSettled === 'saved' ? 'Saved' : 'Check truck status')
                    + ' — live status unavailable; refresh before another action.', 'error');
            });
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
        if (isEditableControl(event.target) && !event.target.matches("[data-dispatch-autosave], [data-dispatch-check]")) {
            syncDirtyState();
        }
    });
    root.addEventListener("change", (event) => {
        if (event.target.matches("[data-dispatch-check]")) {
            saveDispatcherCheck(event.target);
        } else if (event.target.matches("[data-dispatch-autosave]")) {
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
        const topOffForm=event.target.closest('[data-top-off-form]');
        if(topOffForm) {
            event.preventDefault();
            submitTruckCardAction(topOffForm,event.submitter || topOffForm.querySelector('button'));
            return;
        }
        const lifecycleForm = event.target.closest("[data-dispatch-lifecycle-form]")
            || (event.target.closest("[data-assets-workspace]") && event.target.closest("form"));
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
        const form = editor?.querySelector("[data-dispatch-apu-override-value]")?.form
            || cancel.closest("[data-dispatch-assignment-form]")
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
            body.set("spear_decision_reference", root.dataset.spearAutomaticReference || "");
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
