(() => {
    "use strict";

    const copyFallback = (value) => {
        const input = document.createElement("textarea");
        input.value = value;
        input.setAttribute("readonly", "");
        input.style.position = "fixed";
        input.style.opacity = "0";
        document.body.appendChild(input);
        input.select();
        document.execCommand("copy");
        input.remove();
    };

    document.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-copy-neo-fuel]");
        if (!button) return;

        const value = button.dataset.copyNeoFuel || "";
        if (!value) return;
        try {
            if (navigator.clipboard?.writeText) {
                await navigator.clipboard.writeText(value);
            } else {
                copyFallback(value);
            }
        } catch (_error) {
            try {
                copyFallback(value);
            } catch (_fallbackError) {
                return;
            }
        }
        button.textContent = "COPIED";
    });
})();
