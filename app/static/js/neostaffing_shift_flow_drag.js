(() => {
    "use strict";

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
