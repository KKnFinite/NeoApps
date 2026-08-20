(() => {
    "use strict";

    const displayAllowance = (lbs) => {
        if (lbs === null || lbs === undefined || lbs === "") {
            return "APU INCOMPLETE";
        }
        const thousands = Number(lbs) / 1000;
        const text = Number.isInteger(thousands * 10)
            ? thousands.toFixed(1)
            : thousands.toFixed(2);
        return `APU ${text}K`;
    };

    document.querySelectorAll("[data-apu-editor-form]").forEach((editorForm) => {
        const card = editorForm.closest("[data-fuel-assignment-id]");
        const details = editorForm.querySelector("[data-apu-editor]");
        const allowance = editorForm.querySelector("[data-apu-override-value]");
        const enabled = editorForm.querySelector("[data-apu-override-enabled]");
        const mainForm = card?.querySelector("[data-fuel-planning-form]");
        const reset = editorForm.querySelector("[data-apu-reset]");
        if (!card || !details || !allowance || !enabled || !mainForm) return;

        const syncAuthorityInputs = () => {
            const running = mainForm.querySelector("[data-apu-running]");
            const source = mainForm.querySelector("[data-apu-source]");
            editorForm.querySelector("[data-apu-editor-running]").value = running?.value || "not_confirmed";
            editorForm.querySelector("[data-apu-editor-source]").value = source?.value || "";
        };

        const remember = () => {
            details.dataset.originalEnabled = enabled.value;
            details.dataset.originalAllowance = allowance.value;
        };
        details.addEventListener("toggle", () => {
            if (details.open) remember();
        });
        editorForm.addEventListener("click", (event) => {
            if (event.target.closest("[data-apu-editor-cancel]")) {
                enabled.value = details.dataset.originalEnabled || enabled.value;
                allowance.value = details.dataset.originalAllowance || "";
                details.open = false;
            }
        });
        editorForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            const submitter = event.submitter;
            if (submitter?.matches("[data-apu-reset]")) {
                enabled.value = "0";
                allowance.value = "";
            } else {
                enabled.value = "1";
            }
            syncAuthorityInputs();
            submitter && (submitter.disabled = true);
            try {
                const response = await fetch(editorForm.action, {
                    method: "POST",
                    body: new FormData(editorForm),
                    cache: "no-store",
                    credentials: "same-origin",
                    headers: {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok || payload.ok !== true) {
                    throw new Error(payload.error || "APU allowance save failed.");
                }
                const effectiveLbs = payload.effective_apu_allowance_lbs;
                enabled.value = payload.apu_override_enabled ? "1" : "0";
                allowance.value = payload.apu_override_enabled
                    ? String(Number(payload.apu_override_allowance_lbs) / 1000)
                    : "";
                mainForm.dataset.persistedApuAllowanceLbs = effectiveLbs ?? "";
                mainForm.dataset.persistedAutomaticApuAllowanceLbs = payload.automatic_apu_allowance_lbs ?? "";
                mainForm.querySelector("[data-apu-override-persisted-value]").value = allowance.value;
                card.querySelector("[data-apu-allowance-output]").textContent = displayAllowance(effectiveLbs);
                details.open = false;
                details.querySelector("summary")?.focus();
                mainForm.dispatchEvent(new Event("input", {bubbles: true}));
            } catch (error) {
                details.open = true;
                allowance.setCustomValidity(error.message || "Unable to save APU allowance.");
                allowance.reportValidity();
            } finally {
                if (submitter) submitter.disabled = false;
            }
        });
    });
})();
