(() => {
    "use strict";

    const board = document.querySelector("[data-shift-flow-final-board]");
    if (!board) return;

    const feedback = board.querySelector("[data-shift-flow-drag-feedback]");
    const targetLanes = [...board.querySelectorAll("[data-final-door-id]")];
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
        const lane = event.target.closest("[data-final-door-id]");
        if (!dragged || !lane || saving) return;
        event.preventDefault();
        clearTargets();
        lane.classList.add("is-drag-over");
        event.dataTransfer.dropEffect = "move";
    });
    board.addEventListener("dragleave", (event) => {
        const lane = event.target.closest("[data-final-door-id]");
        if (lane && !lane.contains(event.relatedTarget)) lane.classList.remove("is-drag-over");
    });
    board.addEventListener("drop", async (event) => {
        const target = event.target.closest("[data-final-door-id]");
        if (!dragged || !target || saving) return;
        event.preventDefault();
        clearTargets();
        const row = dragged;
        const source = row.closest("[data-final-door-id]");
        if (!source || source === target) {
            message("Already assigned to this Final Door.");
            return;
        }

        saving = true;
        row.classList.add("is-saving");
        try {
            const endpoint = board.dataset.finalDoorMoveUrl.replace(
                "/0/final-door", `/${row.dataset.shiftFlowPersonId}/final-door`,
            );
            const response = await window.fetch(endpoint, {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json", "Accept": "application/json" },
                body: JSON.stringify({
                    final_door_work_area_id: target.dataset.finalDoorId,
                    expected_version: row.dataset.shiftFlowPlanVersion,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) {
                throw new Error(payload.error || payload.conflict?.message || "Final Door move was not saved.");
            }
            if (payload.changed) {
                target.querySelector("ul")?.append(row);
                updateCount(source);
                updateCount(target);
                row.dataset.shiftFlowSourceDoorId = target.dataset.finalDoorId;
            }
            row.dataset.shiftFlowPlanVersion = payload.plan_version;
            const shorthand = row.querySelector("[data-shift-flow-shorthand]");
            if (shorthand) shorthand.textContent = payload.shorthand || "";
            message(payload.changed ? "Final Door updated." : "Already assigned to this Final Door.");
        } catch (error) {
            message(error.message || "Final Door move was not saved.", true);
        } finally {
            row.classList.remove("is-saving", "is-dragging");
            saving = false;
            dragged = null;
        }
    });
})();
