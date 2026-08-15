((global) => {
    "use strict";

    const normalizeTail = (value) => String(value || "").trim().toUpperCase();
    const uniqueTails = (values) => Array.from(new Set(
        (values || []).map(normalizeTail).filter(Boolean)
    ));
    const normalizeState = (state = {}) => {
        const allTails = uniqueTails(state.allTails || state.all_tails);
        const knownTails = new Set(allTails);
        const unassignedTails = uniqueTails(
            state.unassignedTails || state.unassigned_tails
        ).filter((tail) => knownTails.has(tail));
        return {allTails, unassignedTails};
    };
    const fromLiveTails = (tails) => normalizeState({
        allTails: (tails || []).map((row) => row.tail_number),
        unassignedTails: (tails || [])
            .filter((row) => row.source?.location === "unassigned")
            .map((row) => row.tail_number),
    });
    const matchTail = (tails, rawValue) => {
        const options = uniqueTails(tails);
        const typed = normalizeTail(rawValue).replace(/\s+/g, "");
        const prefixMatches = typed
            ? options.filter((tail) => tail.startsWith(typed))
            : [];
        const partialMatches = typed
            ? options.filter((tail) => tail.includes(typed))
            : [];
        return options.find((tail) => tail === typed)
            || (prefixMatches.length === 1 ? prefixMatches[0] : "")
            || (partialMatches.length === 1 ? partialMatches[0] : "")
            || "";
    };
    const readBootstrap = (root) => {
        const element = root?.querySelector("[data-parking-picker-bootstrap]");
        if (!element) {
            return normalizeState();
        }
        try {
            return normalizeState(JSON.parse(element.textContent || "{}"));
        } catch (_error) {
            return normalizeState();
        }
    };
    const makeOption = (documentRef, label, value, {disabled = false} = {}) => {
        const option = documentRef.createElement("option");
        option.value = value;
        option.textContent = label;
        option.disabled = disabled;
        return option;
    };
    const hydrate = (root, rawState, {canEdit = true} = {}) => {
        const state = normalizeState(rawState);
        const documentRef = root.ownerDocument || global.document;
        const hasUnassigned = state.unassignedTails.length > 0;

        root.querySelectorAll("[data-direct-slot-options]").forEach((list) => {
            list.replaceChildren(...state.unassignedTails.map(
                (tail) => makeOption(documentRef, tail, tail)
            ));
        });
        root.querySelectorAll("[data-direct-slot-input]").forEach((input) => {
            input.placeholder = hasUnassigned ? "SELECT TAIL" : "NO UNPARKED TAILS";
            input.disabled = !canEdit || !hasUnassigned;
        });
        root.querySelectorAll("[data-direct-slot-select]").forEach((select) => {
            const previous = normalizeTail(select.value);
            const placeholder = hasUnassigned ? "SELECT TAIL" : "NO UNPARKED TAILS";
            select.replaceChildren(
                makeOption(documentRef, placeholder, ""),
                ...state.unassignedTails.map(
                    (tail) => makeOption(documentRef, tail, tail)
                )
            );
            select.value = state.unassignedTails.includes(previous) ? previous : "";
            select.disabled = !canEdit || !hasUnassigned;
        });
        root.querySelectorAll("[data-mobile-slot-tail-picker] select[name='tail_number']")
            .forEach((select) => {
                const previous = normalizeTail(select.value);
                const occupied = normalizeTail(
                    select.closest("[data-parking-lane]")?.dataset.occupiedTail
                );
                const selected = occupied || previous;
                select.replaceChildren(
                    makeOption(documentRef, "SELECT TAIL", ""),
                    ...state.allTails.map((tail) => makeOption(documentRef, tail, tail))
                );
                select.value = state.allTails.includes(selected) ? selected : "";
                if (!canEdit) {
                    select.disabled = true;
                }
            });
        return state;
    };
    const mobileEditorState = (lane, selectedTail = null, source = {}) => {
        const occupiedTail = normalizeTail(lane?.dataset.occupiedTail);
        const tail = selectedTail === null
            ? occupiedTail
            : normalizeTail(selectedTail);
        return {
            rampCode: lane?.dataset.rampCode || "",
            positionCode: lane?.dataset.positionCode || "",
            laneNumber: lane?.dataset.laneNumber || "",
            occupiedTail,
            targetVersion: lane?.dataset.parkingSlotVersion || "empty",
            replaceOccupied: occupiedTail ? "1" : "0",
            selectedTail: tail,
            sourceLocation: source.location || "",
            sourceVersion: source.version || "",
            operationId: source.operationId || "",
            label: occupiedTail ? "Swap Tail" : "Change Tail",
            action: occupiedTail ? "Swap" : "Change",
        };
    };
    const populateMobileEditor = (form, lane, selectedTail = null, source = {}) => {
        const state = mobileEditorState(lane, selectedTail, source);
        const setValue = (name, value) => {
            const control = form?.querySelector(`[name='${name}']`);
            if (control) control.value = value;
        };
        setValue("operation_id", state.operationId);
        setValue("ramp_code", state.rampCode);
        setValue("position_code", state.positionCode);
        setValue("lane_number", state.laneNumber);
        setValue("replace_occupied", state.replaceOccupied);
        setValue("parking_snapshot", "1");
        setValue("expected_source_location", state.sourceLocation);
        setValue("expected_source_version", state.sourceVersion);
        setValue("expected_target_tail", state.occupiedTail);
        setValue("expected_target_version", state.targetVersion);
        setValue("tail_number", state.selectedTail);
        const label = form?.querySelector("[data-mobile-slot-editor-label]");
        const submit = form?.querySelector("[data-mobile-slot-editor-submit]");
        if (label) label.textContent = state.label;
        if (submit) submit.textContent = state.action;
        if (form) {
            form.setAttribute(
                "aria-label",
                `${state.label} for ${state.positionCode} Slot ${state.laneNumber}`
            );
        }
        return state;
    };

    global.NeoParkingPickerOptions = Object.freeze({
        fromLiveTails,
        hydrate,
        matchTail,
        normalizeState,
        normalizeTail,
        readBootstrap,
    });
    global.NeoParkingReusableControls = Object.freeze({
        mobileEditorState,
        populateMobileEditor,
    });
})(window);

(() => {
    "use strict";

    const page = document.querySelector("[data-parking-plan]");
    if (!page || page.dataset.parkingLiveBound === "true") {
        return;
    }
    page.dataset.parkingLiveBound = "true";

    const canEdit = page.dataset.canEdit === "1";
    const stateUrl = page.dataset.stateUrl || "";
    const statusElement = page.querySelector("[data-live-update-status]");
    const messageElement = page.querySelector("[data-parking-live-message]");
    const unassignedList = page.querySelector("[data-parking-unassigned-list]");
    const optimizerPanel = page.querySelector("[data-parking-optimizer-panel]");
    const optimizerApplyForm = page.querySelector(".parking-optimizer-apply-form");
    const reusableControlHost = page.querySelector("[data-parking-reusable-control-host]");
    const directSlotEditor = page.querySelector("[data-reusable-direct-slot-editor]");
    const mobileSlotEditor = page.querySelector("[data-reusable-mobile-slot-editor]");
    const dragEnabled = canEdit
        && window.matchMedia("(min-width: 721px) and (pointer: fine)").matches;
    let currentRevision = page.dataset.parkingRevision || "";
    let selectedTail = "";
    let dragContext = null;
    let dropInFlight = false;
    let confirmationActive = false;
    let deferredPayload = null;
    let controller = null;

    if (!canEdit) {
        page.querySelectorAll(
            ".parking-mobile-assignment input, .parking-mobile-assignment select, "
            + ".parking-mobile-assignment button, [data-parking-typed-assign-form] input, "
            + "[data-parking-typed-assign-form] select, [data-parking-typed-assign-form] button, "
            + "[data-mobile-slot-tail-picker] select, [data-mobile-slot-tail-picker] button, "
            + "[data-direct-slot-input], [data-direct-slot-select]"
        ).forEach((control) => {
            control.disabled = true;
        });
    }

    const pickerOptions = window.NeoParkingPickerOptions;
    const reusableControls = window.NeoParkingReusableControls;
    const normalizeTail = pickerOptions.normalizeTail;
    const normalizeTypedPosition = (value) => {
        const cleaned = String(value || "").trim().toUpperCase().replace(/\s+/g, "");
        const match = cleaned.match(/^([A-ER])0*(\d{1,2})$/);
        return match
            ? `${match[1]}${String(Number(match[2])).padStart(2, "0")}`
            : cleaned;
    };
    const tailCard = (tail) => page.querySelector(
        `[data-parking-tail][data-tail-number="${CSS.escape(normalizeTail(tail))}"]`
    );
    const laneFor = (positionCode, laneNumber) => Array.from(
        page.querySelectorAll("[data-parking-lane]")
    ).find((lane) => (
        lane.dataset.positionCode === positionCode
        && lane.dataset.laneNumber === String(laneNumber)
    ));
    const sourceSnapshot = (tail) => {
        const card = tailCard(tail);
        return {
            location: card?.dataset.parkingSourceLocation || "unassigned",
            version: card?.dataset.parkingSourceVersion || "missing",
        };
    };
    const targetSnapshot = (lane) => ({
        tail: normalizeTail(lane?.dataset.occupiedTail),
        version: lane?.dataset.parkingSlotVersion || "empty",
    });

    const showMessage = (message, state = "info") => {
        if (!messageElement) {
            return;
        }
        messageElement.textContent = message || "";
        messageElement.dataset.parkingMessageState = state;
        messageElement.hidden = !message;
    };

    const withConfirmation = (message) => {
        confirmationActive = true;
        page.dataset.parkingConfirmationActive = "true";
        try {
            return window.confirm(message);
        } finally {
            confirmationActive = false;
            page.removeAttribute("data-parking-confirmation-active");
        }
    };

    const setSelectedTail = (tail) => {
        selectedTail = normalizeTail(tail);
        page.classList.toggle("has-selected-tail", Boolean(selectedTail));
        page.querySelectorAll("[data-parking-tail]").forEach((card) => {
            card.classList.toggle(
                "is-selected",
                Boolean(selectedTail) && card.dataset.tailNumber === selectedTail
            );
        });
        const selectionStatus = page.querySelector("[data-parking-selection-status]");
        const selectedLabel = page.querySelector("[data-selected-tail-label]");
        const typedForm = page.querySelector("[data-parking-typed-assign-form]");
        const typedTail = page.querySelector("[data-typed-tail-number]");
        if (selectionStatus) {
            selectionStatus.hidden = !selectedTail;
        }
        if (selectedLabel) {
            selectedLabel.textContent = selectedTail;
        }
        if (typedForm) {
            typedForm.hidden = !selectedTail;
            if (!selectedTail) {
                typedForm.removeAttribute("data-parking-dirty");
            }
        }
        if (typedTail) {
            typedTail.value = selectedTail;
        }
        clearTypedError();
        closeDirectSlotSelectors();
    };

    const parkingEditIsActive = () => {
        if (dragContext || dropInFlight || confirmationActive) {
            return true;
        }
        if (page.querySelector("dialog[open]")) {
            return true;
        }
        if (page.querySelector("[data-parking-dirty='true']")) {
            return true;
        }
        const active = document.activeElement;
        return Boolean(active && page.contains(active) && active.closest(
            "[data-parking-typed-assign-form], .parking-mobile-assignment, "
            + ".parking-direct-slot-assign, [data-mobile-slot-tail-picker], "
            + "[data-tail-status-modal]"
        ));
    };

    const parseTailFragments = (html) => {
        const parser = new DOMParser();
        const parsed = parser.parseFromString(html || "", "text/html");
        return new Map(
            Array.from(parsed.querySelectorAll("[data-parking-tail]")).map((card) => [
                normalizeTail(card.dataset.tailNumber),
                card,
            ])
        );
    };

    const markUpdated = (element) => {
        if (!element) {
            return;
        }
        element.classList.remove("is-parking-live-updated");
        void element.offsetWidth;
        element.classList.add("is-parking-live-updated");
        window.setTimeout(() => element.classList.remove("is-parking-live-updated"), 1700);
    };

    const updateSummary = (summary) => {
        Object.entries(summary || {}).forEach(([key, value]) => {
            const target = page.querySelector(`[data-parking-summary="${CSS.escape(key)}"]`);
            if (target && target.textContent !== String(value)) {
                target.textContent = String(value);
                markUpdated(target.closest("article"));
            }
        });
    };

    const updatePickerOptions = (tails) => {
        pickerOptions.hydrate(page, pickerOptions.fromLiveTails(tails), {canEdit});
    };
    const updateMobileSlotTrigger = (lane) => {
        const trigger = lane?.querySelector("[data-mobile-slot-editor-trigger]");
        if (!trigger) return;
        const occupied = normalizeTail(lane.dataset.occupiedTail);
        const action = occupied ? "SWAP TAIL" : "CHANGE TAIL";
        trigger.textContent = action;
        trigger.setAttribute(
            "aria-label",
            `${occupied ? "Swap" : "Change"} tail for ${lane.dataset.positionCode} `
            + `Slot ${lane.dataset.slotNumber}`
        );
    };

    const updateSlot = (slot) => {
        const lane = page.querySelector(
            `[data-parking-slot-id="${CSS.escape(slot.id)}"]`
        );
        if (!lane) {
            return;
        }
        const changed = lane.dataset.parkingSlotVersion !== slot.version;
        lane.dataset.parkingSlotVersion = slot.version;
        lane.dataset.occupiedTail = slot.occupant_tail || "";
        lane.classList.toggle("is-occupied", Boolean(slot.occupant_tail));
        if (String(slot.lane_number) === "2") {
            if (slot.occupant_tail) {
                lane.classList.remove("is-collapsed-slot", "is-expanded-slot");
                lane.dataset.slotCollapsed = "0";
            } else if (!lane.classList.contains("is-expanded-slot")) {
                lane.classList.add("is-collapsed-slot");
                lane.dataset.slotCollapsed = "1";
            }
        }
        const mobileForm = lane.querySelector("[data-mobile-slot-tail-picker]");
        if (mobileForm) {
            const occupied = mobileForm.querySelector("input[name='expected_target_tail']");
            const version = mobileForm.querySelector("input[name='expected_target_version']");
            const replace = mobileForm.querySelector("input[name='replace_occupied']");
            if (occupied) occupied.value = slot.occupant_tail || "";
            if (version) version.value = slot.version;
            if (replace) replace.value = slot.occupant_tail ? "1" : "0";
            const label = mobileForm.querySelector("[data-mobile-slot-editor-label]");
            const submit = mobileForm.querySelector("[data-mobile-slot-editor-submit]");
            if (label) label.textContent = slot.occupant_tail ? "Swap Tail" : "Change Tail";
            if (submit) submit.textContent = slot.occupant_tail ? "Swap" : "Change";
        }
        updateMobileSlotTrigger(lane);
        if (changed) {
            markUpdated(lane);
        }
    };

    const destinationForTail = (tailState) => {
        const source = tailState.source || {};
        if (source.location === "unassigned") {
            return unassignedList;
        }
        return laneFor(source.position_code, source.lane_number)
            ?.querySelector("[data-parking-lane-tail]") || null;
    };

    const markOptimizerStale = (incomingRevision) => {
        const previewRevision = optimizerPanel?.dataset.optimizerPreviewRevision;
        if (!previewRevision || previewRevision === incomingRevision) {
            return;
        }
        optimizerPanel.classList.add("is-optimizer-preview-stale");
        const warning = optimizerPanel.querySelector("[data-parking-optimizer-stale]");
        if (warning) {
            warning.hidden = false;
        }
        if (optimizerApplyForm) {
            optimizerApplyForm.dataset.parkingPreviewStale = "true";
            optimizerApplyForm.querySelectorAll("button, input").forEach((control) => {
                control.disabled = true;
            });
        }
    };

    const applyParkingState = (payload, { force = false } = {}) => {
        if (!payload?.ok) {
            return false;
        }
        markOptimizerStale(payload.revision);
        if (!payload.changed) {
            currentRevision = payload.revision || currentRevision;
            page.dataset.parkingRevision = currentRevision;
            return true;
        }
        if (!force && parkingEditIsActive()) {
            deferredPayload = payload;
            page.dataset.parkingReconcileDeferred = "true";
            return false;
        }

        const incomingCards = parseTailFragments(payload.fragments?.tail_cards);
        const incomingTailByNumber = new Map(
            (payload.tails || []).map((row) => [row.tail_number, row])
        );
        const currentCards = Array.from(page.querySelectorAll("[data-parking-tail]"));
        const changedTails = new Set();
        currentCards.forEach((card) => {
            const tail = normalizeTail(card.dataset.tailNumber);
            const incoming = incomingTailByNumber.get(tail);
            if (!incoming) {
                card.remove();
                changedTails.add(tail);
                return;
            }
            if (card.dataset.parkingTailVersion !== incoming.version) {
                card.remove();
                changedTails.add(tail);
            }
        });
        (payload.tails || []).forEach((row) => {
            if (!tailCard(row.tail_number)) {
                changedTails.add(row.tail_number);
            }
        });

        (payload.slots || []).forEach(updateSlot);
        changedTails.forEach((tail) => {
            const state = incomingTailByNumber.get(tail);
            const replacement = incomingCards.get(tail);
            const destination = state ? destinationForTail(state) : null;
            if (!state || !replacement || !destination) {
                return;
            }
            destination.appendChild(replacement);
            markUpdated(replacement);
        });
        if (unassignedList) {
            const empty = unassignedList.querySelector(".muted");
            if (unassignedList.querySelector("[data-parking-tail]")) {
                empty?.remove();
            } else if (!empty) {
                const message = document.createElement("p");
                message.className = "muted";
                message.textContent = "All current-sort tails are assigned.";
                unassignedList.appendChild(message);
            }
        }
        updatePickerOptions(payload.tails);
        updateSummary(payload.summary);
        currentRevision = payload.revision;
        page.dataset.parkingRevision = currentRevision;
        deferredPayload = null;
        page.removeAttribute("data-parking-reconcile-deferred");
        bindTailCards();
        if (selectedTail && incomingTailByNumber.has(selectedTail)) {
            setSelectedTail(selectedTail);
        } else if (selectedTail) {
            setSelectedTail("");
        }
        return true;
    };

    const fetchParkingState = async ({ apply = true, force = false } = {}) => {
        if (!stateUrl) {
            return null;
        }
        const url = new URL(stateUrl, window.location.href);
        if (currentRevision) {
            url.searchParams.set("revision", currentRevision);
        }
        const response = await fetch(url, {
            cache: "no-store",
            credentials: "same-origin",
            headers: { "Accept": "application/json" },
        });
        if (!response.ok) {
            throw new Error("Parking Plan live refresh failed.");
        }
        const payload = await response.json();
        if (!payload.ok) {
            throw new Error(payload.error || "Parking Plan live refresh failed.");
        }
        controller?.setServerStatus(payload.refresh || {});
        if (apply) {
            applyParkingState(payload, { force });
        } else if (payload.changed) {
            deferredPayload = payload;
        }
        return payload;
    };

    const reconcileLatest = async () => {
        try {
            const payload = await fetchParkingState({ apply: true, force: true });
            return Boolean(payload);
        } catch (_error) {
            if (deferredPayload && !parkingEditIsActive()) {
                applyParkingState(deferredPayload, { force: true });
            }
            return false;
        }
    };

    const reconcileDeferred = () => {
        if (parkingEditIsActive()) {
            return;
        }
        if (deferredPayload) {
            applyParkingState(deferredPayload, { force: true });
        }
    };

    const currentPayloadMatchesSnapshot = (payload, tail, lane, source, target) => {
        const latestTail = (payload?.tails || []).find((row) => row.tail_number === tail);
        const latestSlot = (payload?.slots || []).find(
            (row) => row.id === lane.dataset.parkingSlotId
        );
        if (!latestTail || !latestSlot) {
            return false;
        }
        return latestTail.source?.location === source.location
            && latestTail.source?.version === source.version
            && (latestSlot.occupant_tail || "") === target.tail
            && latestSlot.version === target.version;
    };

    const appendSnapshot = (formData, source, target) => {
        formData.set("parking_snapshot", "1");
        formData.set("expected_source_location", source.location);
        formData.set("expected_source_version", source.version);
        formData.set("expected_target_tail", target.tail);
        formData.set("expected_target_version", target.version);
    };

    const handleWriteConflict = async (payload) => {
        const conflict = payload.conflict || payload;
        showMessage(
            conflict.message || "Parking changed while you were editing. Latest plan has been loaded.",
            "conflict"
        );
        dragContext = null;
        dropInFlight = false;
        page.removeAttribute("data-parking-drag-active");
        await reconcileLatest();
    };

    const sendAssignment = async (formData) => {
        const response = await fetch(page.dataset.assignUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
            body: formData,
        });
        const payload = await response.json().catch(() => ({}));
        return { response, payload };
    };

    const assignTailToLane = async (lane, rawTail, options = {}) => {
        const tail = normalizeTail(rawTail);
        if (!canEdit || !tail || !lane) {
            return false;
        }
        const reportError = options.errorTarget ? showTypedError : (message) => {
            showMessage(message, "error");
        };
        const source = dragContext?.tail === tail
            ? dragContext.source
            : sourceSnapshot(tail);
        const target = targetSnapshot(lane);
        let replaceOccupied = "0";
        if (target.tail && target.tail !== tail) {
            const confirmed = withConfirmation(
                `${target.tail} is already in ${lane.dataset.positionCode} Slot `
                + `${lane.dataset.slotNumber}. Replace it with ${tail}?`
            );
            if (!confirmed) {
                reportError(`${target.tail} remains in ${lane.dataset.positionCode} Slot ${lane.dataset.slotNumber}. Assignment cancelled.`);
                return false;
            }
            replaceOccupied = "1";
        }

        dropInFlight = Boolean(dragContext);
        try {
            const latest = await fetchParkingState({ apply: false });
            if (!currentPayloadMatchesSnapshot(latest, tail, lane, source, target)) {
                await handleWriteConflict({
                    message: "Parking changed while you were dragging or editing. Latest plan has been loaded.",
                });
                return false;
            }

            const formData = new URLSearchParams();
            formData.set("tail_number", tail);
            formData.set("ramp_code", lane.dataset.rampCode);
            formData.set("position_code", lane.dataset.positionCode);
            formData.set("lane_number", lane.dataset.laneNumber);
            formData.set("replace_occupied", replaceOccupied);
            appendSnapshot(formData, source, target);

            let { response, payload } = await sendAssignment(formData);
            if (response.status === 409 && payload.requires_confirmation) {
                const confirmed = withConfirmation(`${payload.message}\n\nOverride this Parking Rule?`);
                if (!confirmed) {
                    reportError("Parking assignment cancelled.");
                    return false;
                }
                formData.set("confirm_rule_override", "1");
                ({ response, payload } = await sendAssignment(formData));
            }
            if (response.status === 409 && payload.conflict) {
                await handleWriteConflict(payload);
                return false;
            }
            if (!response.ok || !payload.ok) {
                reportError(payload.message || "Parking assignment failed.");
                return false;
            }
            showMessage(payload.message || `${tail} parking saved.`, "success");
            setSelectedTail("");
            return true;
        } catch (_error) {
            reportError("Parking could not be verified or saved. No move was applied.");
            return false;
        } finally {
            dragContext = null;
            dropInFlight = false;
            page.removeAttribute("data-parking-drag-active");
            await reconcileLatest();
        }
    };

    const unassignTail = async (tail) => {
        if (!canEdit || !tail || !page.dataset.unassignUrl) {
            return false;
        }
        const source = dragContext?.tail === tail
            ? dragContext.source
            : sourceSnapshot(tail);
        dropInFlight = Boolean(dragContext);
        try {
            const latest = await fetchParkingState({ apply: false });
            const latestTail = (latest?.tails || []).find((row) => row.tail_number === tail);
            if (
                !latestTail
                || latestTail.source?.location !== source.location
                || latestTail.source?.version !== source.version
            ) {
                await handleWriteConflict({
                    message: `${tail} parking changed while you were dragging it. Latest plan has been loaded.`,
                });
                return false;
            }
            const formData = new URLSearchParams();
            formData.set("tail_number", tail);
            formData.set("parking_snapshot", "1");
            formData.set("expected_source_location", source.location);
            formData.set("expected_source_version", source.version);
            const response = await fetch(page.dataset.unassignUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
                body: formData,
            });
            const payload = await response.json().catch(() => ({}));
            if (response.status === 409 && payload.conflict) {
                await handleWriteConflict(payload);
                return false;
            }
            if (!response.ok || !payload.ok) {
                showMessage(payload.message || "Parking unassign failed.", "error");
                return false;
            }
            showMessage(payload.message || `${tail} unassigned.`, "success");
            return true;
        } catch (_error) {
            showMessage("Parking could not be verified or saved. No move was applied.", "error");
            return false;
        } finally {
            dragContext = null;
            dropInFlight = false;
            page.removeAttribute("data-parking-drag-active");
            await reconcileLatest();
        }
    };

    const typedAssignForm = page.querySelector("[data-parking-typed-assign-form]");
    const typedPositionInput = page.querySelector("[data-typed-position-input]");
    const typedSlotSelect = page.querySelector("[data-typed-slot-select]");
    const typedAssignError = page.querySelector("[data-typed-assign-error]");
    function showTypedError(message) {
        if (!typedAssignError) {
            showMessage(message, "error");
            return;
        }
        typedAssignError.textContent = message;
        typedAssignError.hidden = false;
    }
    function clearTypedError() {
        if (typedAssignError) {
            typedAssignError.textContent = "";
            typedAssignError.hidden = true;
        }
    }

    const directSlotInput = directSlotEditor?.querySelector("[data-direct-slot-input]");
    const directSlotSelect = directSlotEditor?.querySelector("[data-direct-slot-select]");
    const closeDirectSlotSelectors = (exceptLane = null) => {
        const lane = directSlotEditor?.closest("[data-parking-lane]");
        if (!lane || exceptLane === lane) return;
        lane.classList.remove("is-direct-selecting", "has-tail-picker-match");
        if (directSlotInput) {
            directSlotInput.value = "";
            directSlotInput.dataset.matchedTail = "";
        }
        if (directSlotSelect) directSlotSelect.value = "";
        directSlotEditor.removeAttribute("data-parking-dirty");
        directSlotEditor.hidden = true;
        reusableControlHost?.appendChild(directSlotEditor);
        reconcileDeferred();
    };
    const openDirectSlotSelector = (lane) => {
        if ((!directSlotInput && !directSlotSelect) || lane.dataset.occupiedTail || !canEdit) {
            return false;
        }
        closeDirectSlotSelectors(lane);
        directSlotEditor.setAttribute(
            "aria-label",
            `Assign unparked tail to ${lane.dataset.positionCode} Slot ${lane.dataset.slotNumber}`
        );
        lane.appendChild(directSlotEditor);
        directSlotEditor.hidden = false;
        lane.classList.add("is-direct-selecting");
        window.setTimeout(() => (directSlotInput || directSlotSelect).focus(), 0);
        return true;
    };
    const tailOptionsForDirectPicker = () => Array.from(
        directSlotSelect?.options || []
    ).map((option) => normalizeTail(option.value)).filter(Boolean);
    const matchDirectPickerTail = (rawValue) => {
        return pickerOptions.matchTail(tailOptionsForDirectPicker(), rawValue);
    };
    const updateDirectPickerMatch = (input) => {
        const lane = input.closest("[data-parking-lane]");
        if (!lane) return "";
        input.value = normalizeTail(input.value).replace(/\s+/g, "");
        const match = matchDirectPickerTail(input.value);
        input.dataset.matchedTail = match;
        if (directSlotSelect) directSlotSelect.value = match;
        lane.classList.toggle("has-tail-picker-match", Boolean(match));
        return match;
    };
    const assignDirectPickerTail = async (input) => {
        const lane = input.closest("[data-parking-lane]");
        const tail = lane ? updateDirectPickerMatch(input) : "";
        if (!lane || !tail) {
            showMessage("Type or select an available unparked tail.", "error");
            input.focus();
            return;
        }
        input.disabled = true;
        try {
            const saved = await assignTailToLane(lane, tail);
            if (saved) closeDirectSlotSelectors();
        } finally {
            input.disabled = false;
            input.closest(".parking-direct-slot-assign")?.removeAttribute("data-parking-dirty");
        }
    };

    directSlotInput?.addEventListener("click", (event) => event.stopPropagation());
    directSlotInput?.addEventListener("input", () => {
        directSlotEditor?.setAttribute("data-parking-dirty", "true");
        updateDirectPickerMatch(directSlotInput);
    });
    directSlotInput?.addEventListener("change", async () => {
        const exact = tailOptionsForDirectPicker().find(
            (tail) => tail === normalizeTail(directSlotInput.value)
        );
        updateDirectPickerMatch(directSlotInput);
        if (exact) await assignDirectPickerTail(directSlotInput);
    });
    directSlotInput?.addEventListener("keydown", async (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        await assignDirectPickerTail(directSlotInput);
    });
    directSlotSelect?.addEventListener("click", (event) => event.stopPropagation());
    directSlotSelect?.addEventListener("change", async () => {
        const lane = directSlotEditor?.closest("[data-parking-lane]");
        if (!lane || !directSlotSelect.value) return;
        directSlotSelect.disabled = true;
        try {
            const saved = await assignTailToLane(lane, directSlotSelect.value);
            if (saved) closeDirectSlotSelectors();
        } finally {
            directSlotSelect.disabled = false;
        }
    });

    let mobileEditorLane = null;
    const mobileEditorSelect = mobileSlotEditor?.querySelector("select[name='tail_number']");
    const prepareMobileSlotEditor = (lane, selectedTail = null) => {
        if (!mobileSlotEditor || !lane) return null;
        const tail = selectedTail === null
            ? normalizeTail(lane.dataset.occupiedTail)
            : normalizeTail(selectedTail);
        const source = tail ? sourceSnapshot(tail) : {};
        source.operationId = page.dataset.operationId || "";
        return reusableControls.populateMobileEditor(
            mobileSlotEditor,
            lane,
            tail,
            source
        );
    };
    const closeMobileSlotEditor = () => {
        if (!mobileSlotEditor) return;
        const lane = mobileEditorLane || mobileSlotEditor.closest("[data-parking-lane]");
        lane?.classList.remove("is-mobile-slot-editing");
        lane?.querySelector("[data-mobile-slot-editor-trigger]")
            ?.setAttribute("aria-expanded", "false");
        mobileSlotEditor.removeAttribute("data-parking-dirty");
        mobileSlotEditor.hidden = true;
        reusableControlHost?.appendChild(mobileSlotEditor);
        mobileEditorLane = null;
        reconcileDeferred();
    };
    const openMobileSlotEditor = (lane) => {
        if (!mobileSlotEditor || !lane || !canEdit) return false;
        closeDirectSlotSelectors();
        closeMobileSlotEditor();
        mobileEditorLane = lane;
        lane.appendChild(mobileSlotEditor);
        mobileSlotEditor.hidden = false;
        lane.classList.add("is-mobile-slot-editing");
        lane.querySelector("[data-mobile-slot-editor-trigger]")
            ?.setAttribute("aria-expanded", "true");
        prepareMobileSlotEditor(lane);
        window.setTimeout(() => mobileEditorSelect?.focus(), 0);
        return true;
    };

    mobileEditorSelect?.addEventListener("input", () => {
        mobileSlotEditor?.setAttribute("data-parking-dirty", "true");
        if (mobileEditorLane) {
            prepareMobileSlotEditor(mobileEditorLane, mobileEditorSelect.value);
        }
    });
    mobileSlotEditor?.querySelector("[data-mobile-slot-editor-cancel]")
        ?.addEventListener("click", async () => {
            closeMobileSlotEditor();
            await reconcileLatest();
        });
    mobileSlotEditor?.addEventListener("submit", async (event) => {
        if (!canEdit) return;
        event.preventDefault();
        const lane = mobileEditorLane || mobileSlotEditor.closest("[data-parking-lane]");
        const tail = normalizeTail(mobileEditorSelect?.value);
        if (!lane || !tail) return;
        prepareMobileSlotEditor(lane, tail);
        const saved = await assignTailToLane(lane, tail);
        if (saved) closeMobileSlotEditor();
    });

    const bindTailCards = () => {
        page.querySelectorAll("[data-parking-tail]").forEach((card) => {
            if (card.dataset.parkingBound === "true") return;
            card.dataset.parkingBound = "true";
            card.draggable = dragEnabled;
            card.addEventListener("click", (event) => {
                if (!canEdit || event.target.closest("[data-tail-action]")) return;
                if (card.dataset.parkingTailAssigned === "1") return;
                event.preventDefault();
                setSelectedTail(card.dataset.tailNumber || "");
            });
            card.addEventListener("dragstart", (event) => {
                if (!dragEnabled) {
                    event.preventDefault();
                    return;
                }
                const tail = normalizeTail(card.dataset.tailNumber);
                dragContext = {
                    tail,
                    source: sourceSnapshot(tail),
                    planRevision: currentRevision,
                };
                page.dataset.parkingDragActive = "true";
                event.dataTransfer.setData("text/plain", tail);
                event.dataTransfer.effectAllowed = "move";
            });
            card.addEventListener("dragend", () => {
                window.setTimeout(async () => {
                    if (dropInFlight) return;
                    dragContext = null;
                    page.removeAttribute("data-parking-drag-active");
                    await reconcileLatest();
                }, 0);
            });
            const open = card.querySelector("[data-tail-status-open]");
            const close = card.querySelector("[data-tail-status-close]");
            const modal = card.querySelector("[data-tail-status-modal]");
            open?.addEventListener("click", () => {
                if (typeof modal?.showModal === "function") modal.showModal();
                else modal?.setAttribute("open", "open");
            });
            close?.addEventListener("click", () => {
                if (typeof modal?.close === "function") modal.close();
                else modal?.removeAttribute("open");
                reconcileLatest();
            });
            modal?.addEventListener("click", (event) => {
                if (event.target === modal && typeof modal.close === "function") {
                    modal.close();
                    reconcileLatest();
                }
            });
        });
    };

    const bindLaneControls = () => {
        page.querySelectorAll("[data-parking-lane]").forEach((lane) => {
            lane.addEventListener("click", async (event) => {
                if (!canEdit) return;
                if (event.target.closest(
                    "[data-direct-slot-input], [data-direct-slot-select], "
                    + "[data-mobile-slot-editor-trigger], [data-mobile-slot-tail-picker]"
                )) return;
                if (event.target.closest("[data-parking-tail]")) return;
                if (
                    lane.dataset.slotCollapsed === "1"
                    && !selectedTail
                    && !lane.classList.contains("is-expanded-slot")
                ) {
                    lane.classList.add("is-expanded-slot");
                    return;
                }
                if (!selectedTail) {
                    openDirectSlotSelector(lane);
                    return;
                }
                closeDirectSlotSelectors();
                await assignTailToLane(lane, selectedTail);
            });
            lane.addEventListener("dragover", (event) => {
                if (!dragEnabled) return;
                event.preventDefault();
                closeDirectSlotSelectors();
                lane.classList.add("is-drag-over");
            });
            lane.addEventListener("dragleave", () => lane.classList.remove("is-drag-over"));
            lane.addEventListener("drop", async (event) => {
                if (!dragEnabled) return;
                event.preventDefault();
                lane.classList.remove("is-drag-over");
                dropInFlight = true;
                const tail = normalizeTail(event.dataTransfer.getData("text/plain"))
                    || dragContext?.tail || "";
                if (tail) await assignTailToLane(lane, tail);
            });
            lane.querySelector("[data-mobile-slot-editor-trigger]")
                ?.addEventListener("click", (event) => {
                    event.stopPropagation();
                    openMobileSlotEditor(lane);
                });
        });
    };

    const mobileForm = page.querySelector(".parking-mobile-assign-controls");
    if (mobileForm) {
        const positionSelect = mobileForm.querySelector("select[name='position_code']");
        const rampSelect = mobileForm.querySelector("select[name='ramp_code']");
        const slotSelect = mobileForm.querySelector("select[name='lane_number']");
        const updateChoices = () => {
            const selected = positionSelect?.options[positionSelect.selectedIndex];
            if (rampSelect && selected?.dataset.rampCode) {
                rampSelect.value = selected.dataset.rampCode;
            }
            const slot1 = laneFor(positionSelect?.value || "", "1")?.dataset.occupiedTail || "";
            const slot2 = laneFor(positionSelect?.value || "", "2")?.dataset.occupiedTail || "";
            const option1 = slotSelect?.querySelector("option[value='1']");
            const option2 = slotSelect?.querySelector("option[value='2']");
            if (option1) option1.textContent = slot1 ? `REPLACE SLOT 1 (${slot1})` : "SLOT 1";
            if (option2) option2.textContent = slot2 ? `REPLACE SLOT 2 (${slot2})` : "USE SLOT 2";
            if (!slot1 && slotSelect) slotSelect.value = "1";
            else if (!slot2 && slotSelect) slotSelect.value = "2";
            const lane = laneFor(positionSelect?.value || "", slotSelect?.value || "1");
            const replace = mobileForm.querySelector("input[name='replace_occupied']");
            if (replace) replace.value = lane?.dataset.occupiedTail ? "1" : "0";
        };
        positionSelect?.addEventListener("change", updateChoices);
        slotSelect?.addEventListener("change", updateChoices);
        mobileForm.addEventListener("submit", async (event) => {
            if (!canEdit) return;
            event.preventDefault();
            const lane = laneFor(positionSelect?.value || "", slotSelect?.value || "1");
            const tail = mobileForm.querySelector("select[name='tail_number']")?.value || "";
            if (lane && tail) await assignTailToLane(lane, tail);
        });
        updateChoices();
    }

    page.querySelector("[data-clear-selected-tail]")?.addEventListener("click", () => {
        setSelectedTail("");
        reconcileLatest();
    });
    typedAssignForm?.addEventListener("input", () => {
        typedAssignForm.dataset.parkingDirty = "true";
    });
    typedAssignForm?.addEventListener("submit", async (event) => {
        event.preventDefault();
        clearTypedError();
        if (!selectedTail) {
            showTypedError("Select an unparked tail before assigning parking.");
            return;
        }
        const position = normalizeTypedPosition(typedPositionInput?.value || "");
        if (typedPositionInput) typedPositionInput.value = position;
        const lane = laneFor(position, typedSlotSelect?.value || "1");
        if (!lane) {
            showTypedError("Enter a valid parking position.");
            return;
        }
        const succeeded = await assignTailToLane(lane, selectedTail, {
            errorTarget: typedAssignError,
        });
        if (succeeded) typedAssignForm.removeAttribute("data-parking-dirty");
    });

    const unassignDropTarget = page.querySelector("[data-parking-unassign-drop]");
    unassignDropTarget?.addEventListener("dragover", (event) => {
        if (!dragEnabled) return;
        event.preventDefault();
        unassignDropTarget.classList.add("is-drag-over");
    });
    unassignDropTarget?.addEventListener("dragleave", (event) => {
        if (!unassignDropTarget.contains(event.relatedTarget)) {
            unassignDropTarget.classList.remove("is-drag-over");
        }
    });
    unassignDropTarget?.addEventListener("drop", async (event) => {
        if (!dragEnabled) return;
        event.preventDefault();
        unassignDropTarget.classList.remove("is-drag-over");
        dropInFlight = true;
        const tail = normalizeTail(event.dataTransfer.getData("text/plain"))
            || dragContext?.tail || "";
        if (tail) await unassignTail(tail);
    });

    optimizerApplyForm?.addEventListener("submit", (event) => {
        if (optimizerApplyForm.dataset.parkingPreviewStale === "true") {
            event.preventDefault();
            showMessage(
                "Parking changed after this optimizer preview. Generate a fresh preview before applying.",
                "conflict"
            );
        }
    });
    page.addEventListener("focusout", () => window.setTimeout(reconcileDeferred, 0));
    document.addEventListener("click", (event) => {
        if (!page.contains(event.target) || !event.target.closest("[data-parking-lane]")) {
            closeDirectSlotSelectors();
        }
    });

    pickerOptions.hydrate(page, pickerOptions.readBootstrap(page), {canEdit});
    bindLaneControls();
    bindTailCards();

    if (stateUrl && window.NeoLiveUpdates) {
        controller = window.NeoLiveUpdates.create({
            intervalMs: Number(page.dataset.liveIntervalMs),
            statusElement,
            poll: () => fetchParkingState({ apply: true }),
        });
        controller.setServerStatus({
            auto_refresh_enabled: page.dataset.liveEnabled === "1",
            reason: page.dataset.liveReason || "outside_ops_window",
            live_status_label: page.dataset.liveLabel || "Live updates off - outside Ops window",
        });
    }
})();
