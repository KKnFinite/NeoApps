/* Declarative application actions; never evaluate markup as JavaScript. */
(() => {
    "use strict";
    document.addEventListener("change", (event) => {
        const select = event.target instanceof HTMLSelectElement ? event.target : null;
        if (select?.matches("[data-submit-on-change]") && select.form) {
            select.form.requestSubmit();
        }
    });
    document.addEventListener("click", (event) => {
        event.target.closest?.("[data-remove-vacation-entry]")?.closest("article")?.remove();
    });
    // Run before node-specific submission listeners. Cancellation must prevent
    // both native navigation and a later JavaScript mutation handler.
    document.addEventListener("submit", (event) => {
        if (event.defaultPrevented) return;
        const form = event.target instanceof HTMLFormElement ? event.target : null;
        if (!form) return;
        const message = event.submitter?.dataset.confirmMessage || form.dataset.confirmMessage;
        if (message && !window.confirm(message)) {
            event.preventDefault();
            event.stopImmediatePropagation();
            return;
        }
        if (event.submitter?.hasAttribute("data-confirm-capacity-override")) {
            let field = form.querySelector('input[name="capacity_override"]');
            if (!field) {
                field = document.createElement("input");
                field.type = "hidden";
                field.name = "capacity_override";
                form.append(field);
            }
            field.value = "1";
        }
    }, true);
})();
