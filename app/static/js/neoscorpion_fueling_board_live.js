(() => {
    "use strict";

    const root = document.querySelector("[data-fueling-board-live]");
    if (!root || !window.NeoLiveUpdates) return;

    const revisionUrl = root.dataset.revisionUrl;
    const panelUrl = root.dataset.panelUrl;
    const intervalMs = Number(root.dataset.refreshIntervalMs || 0);
    const status = root.querySelector("[data-fueling-board-refresh-status]");
    let operationId = root.dataset.operationId || "none";
    let revision = Number(root.dataset.revision || 0);

    const fingerprint = (payload) => ({
        operationId: payload.operation_id == null ? "none" : String(payload.operation_id),
        revision: Number(payload.revision || 0),
    });

    const reconcile = (nextPanel) => {
        const currentPanel = root.querySelector("[data-fueling-board-panel]");
        const currentList = currentPanel?.querySelector("[data-board-list]");
        const nextList = nextPanel?.querySelector("[data-board-list]");
        if (!currentList || !nextList) throw new Error("Fueling Board panel is unavailable.");

        const scrollX = window.scrollX;
        const scrollY = window.scrollY;
        const currentHead = currentPanel.querySelector(".neoscorpion-panel-head");
        const nextHead = nextPanel.querySelector(".neoscorpion-panel-head");
        if (currentHead && nextHead && currentHead.innerHTML !== nextHead.innerHTML) {
            currentHead.replaceWith(nextHead);
        }

        const existing = new Map(Array.from(currentList.querySelectorAll("[data-board-assignment-id]"))
            .map((card) => [card.dataset.boardAssignmentId, card]));
        const desired = Array.from(nextList.querySelectorAll("[data-board-assignment-id]"));
        const nextIds = new Set(desired.map((card) => card.dataset.boardAssignmentId));
        existing.forEach((card, id) => { if (!nextIds.has(id)) card.remove(); });
        currentList.querySelector("[data-board-empty]")?.remove();

        desired.forEach((nextCard, index) => {
            const id = nextCard.dataset.boardAssignmentId;
            let card = existing.get(id);
            if (card && !card.isEqualNode(nextCard)) {
                card.replaceWith(nextCard);
                card = nextCard;
            } else if (!card) {
                card = nextCard;
            }
            if (currentList.children[index] !== card) {
                currentList.insertBefore(card, currentList.children[index] || null);
            }
        });
        if (!desired.length) {
            const empty = nextList.querySelector("[data-board-empty]");
            if (empty) currentList.appendChild(empty);
        }
        window.requestAnimationFrame(() => window.scrollTo(scrollX, scrollY));
    };

    const poll = async () => {
        const revisionResponse = await fetch(revisionUrl, {
            cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json"},
        });
        const latest = await revisionResponse.json().catch(() => ({}));
        if (!revisionResponse.ok || latest.ok !== true) throw new Error("Fueling Board status unavailable.");
        const observed = fingerprint(latest);
        if (observed.operationId === operationId && observed.revision === revision) return;

        const panelResponse = await fetch(panelUrl, {
            cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json"},
        });
        const payload = await panelResponse.json().catch(() => ({}));
        if (!panelResponse.ok || payload.ok !== true) throw new Error("Fueling Board update unavailable.");
        const incoming = fingerprint(payload);
        // A transaction may advance between the lightweight poll and the panel GET.
        // Never render an older same-sort panel over an already observed revision.
        if (incoming.operationId !== observed.operationId || incoming.revision < observed.revision) return;
        if (incoming.operationId === operationId && incoming.revision < revision) return;
        const template = document.createElement("template");
        template.innerHTML = String(payload.html || "").trim();
        reconcile(template.content.querySelector("[data-fueling-board-panel]"));
        operationId = incoming.operationId;
        revision = incoming.revision;
        root.dataset.operationId = operationId === "none" ? "" : operationId;
        root.dataset.revision = String(revision);
    };

    if (Number.isFinite(intervalMs) && intervalMs >= 5000) {
        const controller = window.NeoLiveUpdates.create({
            intervalMs,
            immediate: false,
            continuousWhileVisible: true,
            statusElement: status,
            poll,
        });
        controller.setServerStatus({auto_refresh_enabled: true});
    }
})();
