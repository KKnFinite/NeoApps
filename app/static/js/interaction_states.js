/* Presentation only: callers retain request, CSRF, validation and conflict ownership. */
(() => {
    "use strict";
    if (window.NeoInteraction) return;
    const active = new Map();
    const restore = (element, name, value) => value === null
        ? element.removeAttribute(name) : element.setAttribute(name, value);
    const begin = (element, submitter = null) => {
        if (!element || active.has(element)) return null;
        const controls = Array.from(element.elements || []).filter(control =>
            /^(submit|image)$/.test(control.type));
        if (submitter && !controls.includes(submitter)) controls.push(submitter);
        const previous = controls.map(control => [control, control.getAttribute("aria-disabled")]);
        const busy = element.getAttribute("aria-busy");
        element.dataset.interactionState = "pending";
        element.setAttribute("aria-busy", "true");
        // Do not set disabled: successful submitter name/value and form data
        // must remain in the browser's native submission. The event guard below
        // blocks duplicate actions while preserving focus and control geometry.
        controls.forEach(control => control.setAttribute("aria-disabled", "true"));
        const finish = (state) => {
            if (active.get(element) !== finish) return;
            active.delete(element);
            restore(element, "aria-busy", busy);
            previous.forEach(([control, value]) => restore(control, "aria-disabled", value));
            if (state) element.dataset.interactionState = state;
            else delete element.dataset.interactionState;
        };
        active.set(element, finish);
        return Object.freeze({
            confirmed: () => finish("confirmed"),
            failed: () => finish("failed"),
            reset: () => finish(null),
        });
    };
    window.NeoInteraction = Object.freeze({begin, isPending: element => active.has(element)});
    document.addEventListener("submit", event => {
        if (active.has(event.target)) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);
    document.addEventListener("click", event => {
        const control = event.target.closest?.("button, input[type=submit], input[type=image]");
        if (control?.form && /^(submit|image)$/.test(control.type) && active.has(control.form)) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);
    document.addEventListener("submit", event => {
        if (!event.defaultPrevented && event.target.matches?.("form[data-interaction-form]")) {
            begin(event.target, event.submitter);
        }
    });
    // A new server-rendered document supplies authoritative validation/success.
    // Restored BFCache documents must not retain a previous navigation's lock.
    window.addEventListener("pageshow", () => [...active.values()].forEach(finish => finish(null)));
})();
