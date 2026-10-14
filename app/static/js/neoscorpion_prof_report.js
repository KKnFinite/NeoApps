(() => {
    "use strict";
    document.querySelector("[data-prof-copy]")?.addEventListener("click", async () => {
        const status = document.querySelector("[data-prof-copy-status]");
        try {
            await navigator.clipboard.writeText(document.querySelector("[data-prof-copy-text]").value);
            status.textContent = "Copied";
        } catch (_) { status.textContent = "Copy unavailable. Use CSV download."; }
    });
})();
