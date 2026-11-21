(() => {
    const board = document.querySelector("[data-neorain-inbound-live], [data-neorain-outbound-live]");
    if (!board) return;

    board.addEventListener("input", event => {
        const input = event.target;
        const form = input.closest("[data-mission-note-form]");
        if (form && input.name === "text") {
            form.dataset.missionNoteDirty = String(input.value !== input.defaultValue);
        }
    });

    board.addEventListener("submit", async event => {
        const form = event.target.closest("[data-mission-note-form]");
        if (!form) return;
        event.preventDefault();
        const panel = form.closest("[data-mission-notes]");
        const entries = panel.querySelector("[data-mission-note-entries]");
        if (entries.querySelector("[data-mission-note-saving='true']")) return;

        const errorOutput = form.querySelector("[data-mission-note-error]");
        const otherDraft = [...entries.querySelectorAll("[data-mission-note-dirty='true']")].some(other => other !== form);
        if (otherDraft) {
            errorOutput.textContent = "Save or clear the other note draft first.";
            return;
        }

        form.dataset.missionNoteSaving = "true";
        const fields = new FormData(form);
        const controls = [...entries.querySelectorAll("button, textarea")];
        controls.forEach(control => { control.disabled = true; });
        errorOutput.textContent = "";
        try {
            const response = await fetch(form.getAttribute("action"), {
                method: "POST", credentials: "same-origin", body: fields,
                headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok || !result.ok) throw new Error(result.error || "Unable to save. Your draft is retained.");
            entries.innerHTML = result.html;
            panel.querySelector("[data-mission-note-status]").textContent =
                fields.get("action") === "delete" ? "Note deleted." : "Note saved.";
            const missionId = panel.dataset.missionNotes;
            for (const other of board.querySelectorAll(`[data-mission-notes="${missionId}"]`)) {
                if (other === panel || other.querySelector("[data-mission-note-dirty='true'], [data-mission-note-saving='true']")) continue;
                other.querySelector("[data-mission-note-entries]").innerHTML = result.html;
            }
        } catch (error) {
            errorOutput.textContent = error.message;
        } finally {
            delete form.dataset.missionNoteSaving;
            controls.forEach(control => { control.disabled = false; });
        }
    });
})();
