(() => {
    "use strict";

    const composite = document.querySelector("[data-shift-flow-composite-board]");
    if (composite) {
        const feedback = composite.querySelector("[data-shift-flow-drag-feedback]");
        const attention = composite.querySelector("[data-shift-flow-needs-attention]");
        let dragged = null;
        let saving = false;
        const message = (text, isError = false) => {
            if (!feedback) return;
            feedback.textContent = text || "";
            feedback.classList.toggle("is-error", Boolean(isError));
        };
        const compositeCells = () => [
            ...composite.querySelectorAll("[data-shift-flow-composite-cell]"),
        ];
        const clearCells = () => compositeCells().forEach(
            (cell) => cell.classList.remove("is-drag-over"),
        );
        const adjustNumber = (selector, amount) => {
            const node = composite.querySelector(selector) || document.querySelector(selector);
            if (!node) return;
            const current = Number.parseInt(node.textContent, 10);
            if (Number.isFinite(current)) node.textContent = String(current + amount);
        };
        const normalizeBandRows = (band) => {
            const container = band?.querySelector(".neostaffing-shift-flow-composite-cells");
            if (!container) return;
            const existingCells = [
                ...container.querySelectorAll("[data-shift-flow-composite-cell]"),
            ];
            const doorIds = [];
            const prototypes = new Map();
            const peopleByDoor = new Map();
            existingCells.forEach((cell) => {
                const doorId = cell.dataset.finalDoorId;
                if (!prototypes.has(doorId)) {
                    doorIds.push(doorId);
                    prototypes.set(doorId, cell);
                    peopleByDoor.set(doorId, []);
                }
                peopleByDoor.get(doorId).push(
                    ...cell.querySelectorAll("[data-shift-flow-person-id]"),
                );
            });
            const occupiedRowCount = Math.max(
                0,
                ...doorIds.map((doorId) => peopleByDoor.get(doorId).length),
            );
            const fragment = document.createDocumentFragment();
            for (let rowIndex = 0; rowIndex <= occupiedRowCount; rowIndex += 1) {
                doorIds.forEach((doorId) => {
                    const cell = prototypes.get(doorId).cloneNode(false);
                    cell.classList.remove("is-drag-over");
                    cell.dataset.shiftFlowDisplayRow = String(rowIndex);
                    if (rowIndex === occupiedRowCount) {
                        cell.dataset.shiftFlowEmptyRow = "";
                    } else {
                        delete cell.dataset.shiftFlowEmptyRow;
                    }
                    cell.setAttribute(
                        "aria-label",
                        `${cell.dataset.doorLabel} ${cell.dataset.bandLabel} row ${rowIndex + 1}${rowIndex === occupiedRowCount ? ", empty drop row" : ""}`,
                    );
                    const list = document.createElement("ul");
                    const person = peopleByDoor.get(doorId)[rowIndex];
                    if (person) list.append(person);
                    cell.append(list);
                    fragment.append(cell);
                });
            }
            container.replaceChildren(fragment);
        };
        const normalizeBands = (...bands) => {
            [...new Set(bands.filter(Boolean))].forEach(normalizeBandRows);
        };
        const showAttentionEmptyState = () => {
            const list = attention?.querySelector("[data-shift-flow-attention-list]");
            if (!list || list.querySelector("[data-shift-flow-person-id]")) return;
            const empty = document.createElement("li");
            empty.className = "is-clear";
            empty.dataset.shiftFlowAttentionEmpty = "";
            empty.textContent = "No active Shift employees need attention.";
            list.append(empty);
        };
        composite.addEventListener("dragstart", (event) => {
            const row = event.target.closest("[data-shift-flow-person-id]");
            if (!row || saving) return;
            dragged = row;
            row.classList.add("is-dragging");
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", row.dataset.shiftFlowPersonId);
            message("");
        });
        composite.addEventListener("dragend", () => {
            dragged?.classList.remove("is-dragging");
            dragged = null;
            clearCells();
        });
        composite.addEventListener("dragover", (event) => {
            const cell = event.target.closest("[data-shift-flow-composite-cell]");
            if (!dragged || !cell || saving) return;
            event.preventDefault();
            clearCells();
            cell.classList.add("is-drag-over");
            event.dataTransfer.dropEffect = "move";
        });
        composite.addEventListener("drop", async (event) => {
            const targetCell = event.target.closest("[data-shift-flow-composite-cell]");
            if (!dragged || !targetCell || saving) return;
            event.preventDefault();
            clearCells();
            const row = dragged;
            const sourceCell = row.closest("[data-shift-flow-composite-cell]");
            const sourceBand = sourceCell?.closest("[data-shift-flow-composite-band]");
            const targetBand = targetCell.closest("[data-shift-flow-composite-band]");
            const fromAttention = Boolean(row.closest("[data-shift-flow-needs-attention]"));
            if (sourceCell === targetCell) {
                message("Already assigned to this Final Door cell.");
                return;
            }
            saving = true;
            row.classList.add("is-saving");
            try {
                const endpoint = composite.dataset.shiftFlowCompositeUrl.replace(
                    "/0/final-composite", `/${row.dataset.shiftFlowPersonId}/final-composite`,
                );
                const response = await window.fetch(endpoint, {
                    method: "POST", credentials: "same-origin",
                    headers: { "Content-Type": "application/json", "Accept": "application/json" },
                    body: JSON.stringify({
                        final_door_id: targetCell.dataset.finalDoorId,
                        band: targetCell.dataset.band,
                        expected_version: row.dataset.shiftFlowPlanVersion,
                    }),
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok || !payload.ok) {
                    throw new Error(payload.error || payload.conflict?.message || "Final Door move was not saved.");
                }
                if (payload.changed) {
                    targetCell.querySelector("ul")?.append(row);
                    normalizeBands(sourceBand, targetBand);
                    if (fromAttention) {
                        row.classList.remove("is-needs-attention-source");
                        row.querySelector("em")?.remove();
                        adjustNumber("[data-shift-flow-attention-count]", -1);
                        adjustNumber("[data-shift-flow-placed-count]", 1);
                        if (payload.created) {
                            adjustNumber("[data-shift-flow-planned-count]", 1);
                        }
                        showAttentionEmptyState();
                    }
                }
                row.dataset.shiftFlowPlanVersion = payload.plan_version;
                const setup = row.querySelector("[data-shift-flow-setup-assignment]");
                if (setup) setup.textContent = payload.setup_assignment || "NO SETUP";
                message(payload.created ? "Shift Flow plan created." : (payload.changed ? "Final Door flow updated." : "Already assigned to this Final Door cell."));
            } catch (error) {
                message(error.message || "Final Door move was not saved.", true);
            } finally {
                row.classList.remove("is-saving", "is-dragging");
                saving = false;
                dragged = null;
            }
        });
        return;
    }

    const board = document.querySelector("[data-shift-flow-drag-board]");
    if (!board) return;

    const phase = board.dataset.shiftFlowPhase;
    const feedback = board.querySelector("[data-shift-flow-drag-feedback]");
    const targetLanes = [...board.querySelectorAll("[data-shift-flow-destination-id]")];
    let dragged = null;
    let saving = false;

    const message = (text, isError = false) => {
        if (!feedback) return;
        feedback.textContent = text || "";
        feedback.classList.toggle("is-error", Boolean(isError));
    };
    const clearTargets = () => targetLanes.forEach((lane) => lane.classList.remove("is-drag-over"));
    const updateCount = (lane) => {
        const count = lane.querySelector("[data-shift-flow-count]");
        const list = lane.querySelector("ul");
        if (count && list) count.textContent = String(list.children.length);
    };
    const ballmatTransition = (row, target) => {
        if (phase !== "sort_start" || target.dataset.shiftFlowLaneType !== "Ballmat") return "";
        if (row.dataset.shiftFlowSourceType === "Ballmat") return "";
        const value = window.prompt("Ballmat transition: 1 = After 1st Wave, 2 = After 2nd Wave, 3 = After Ballmat Cleanup", "");
        if (value === null) return null;
        if (!/^[123]$/.test(value.trim())) {
            message("Choose Ballmat transition 1, 2, or 3.", true);
            return null;
        }
        return value.trim();
    };

    board.addEventListener("dragstart", (event) => {
        const row = event.target.closest("[data-shift-flow-person-id]");
        if (!row || saving) return;
        dragged = row;
        row.classList.add("is-dragging");
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", row.dataset.shiftFlowPersonId);
        message("");
    });
    board.addEventListener("dragend", () => {
        dragged?.classList.remove("is-dragging");
        dragged = null;
        clearTargets();
    });
    board.addEventListener("dragover", (event) => {
        const lane = event.target.closest("[data-shift-flow-destination-id]");
        if (!dragged || !lane || saving) return;
        event.preventDefault();
        clearTargets();
        lane.classList.add("is-drag-over");
        event.dataTransfer.dropEffect = "move";
    });
    board.addEventListener("dragleave", (event) => {
        const lane = event.target.closest("[data-shift-flow-destination-id]");
        if (lane && !lane.contains(event.relatedTarget)) lane.classList.remove("is-drag-over");
    });
    board.addEventListener("drop", async (event) => {
        const target = event.target.closest("[data-shift-flow-destination-id]");
        if (!dragged || !target || saving) return;
        event.preventDefault();
        clearTargets();
        const row = dragged;
        const source = row.closest("[data-shift-flow-destination-id]");
        if (!source || source === target) {
            message("Already assigned to this lane.");
            return;
        }
        const transition = ballmatTransition(row, target);
        if (transition === null) return;

        saving = true;
        row.classList.add("is-saving");
        try {
            const endpoint = board.dataset.shiftFlowMoveUrl.replace(
                "/0/lane", `/${row.dataset.shiftFlowPersonId}/lane`,
            );
            const response = await window.fetch(endpoint, {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json", "Accept": "application/json" },
                body: JSON.stringify({
                    phase,
                    destination_id: target.dataset.shiftFlowDestinationId,
                    ballmat_transition: transition,
                    expected_version: row.dataset.shiftFlowPlanVersion,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) {
                throw new Error(payload.error || payload.conflict?.message || "Shift Flow move was not saved.");
            }
            if (payload.changed) {
                target.querySelector("ul")?.append(row);
                updateCount(source);
                updateCount(target);
                row.dataset.shiftFlowSourceId = target.dataset.shiftFlowDestinationId;
                row.dataset.shiftFlowSourceType = target.dataset.shiftFlowLaneType;
            }
            row.dataset.shiftFlowPlanVersion = payload.plan_version;
            const shorthand = row.querySelector("[data-shift-flow-shorthand]");
            if (shorthand) shorthand.textContent = payload.shorthand || "";
            message(payload.changed ? "Shift Flow updated." : "Already assigned to this lane.");
        } catch (error) {
            message(error.message || "Shift Flow move was not saved.", true);
        } finally {
            row.classList.remove("is-saving", "is-dragging");
            saving = false;
            dragged = null;
        }
    });
})();
