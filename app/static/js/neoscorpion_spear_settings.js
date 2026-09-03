(() => {
    "use strict";
    const form = document.querySelector("[data-spear-settings-form]");
    const list = form?.querySelector("[data-spear-priority-list]");
    const order = form?.querySelector("[data-spear-priority-order]");
    if (!form || !list || !order) return;

    const sync = () => {
        order.value = Array.from(list.querySelectorAll("[data-priority-key]"))
            .map((item) => item.dataset.priorityKey).join(",");
    };
    let dragged = null;
    list.addEventListener("dragstart", (event) => {
        dragged = event.target.closest("[data-priority-key]");
        if (dragged) event.dataTransfer.effectAllowed = "move";
    });
    list.addEventListener("dragover", (event) => {
        const target = event.target.closest("[data-priority-key]");
        if (!dragged || !target || target === dragged) return;
        event.preventDefault();
        const box = target.getBoundingClientRect();
        list.insertBefore(dragged, event.clientY < box.top + box.height / 2 ? target : target.nextSibling);
    });
    list.addEventListener("drop", (event) => { event.preventDefault(); sync(); });
    list.addEventListener("dragend", () => { dragged = null; sync(); });
    form.addEventListener("submit", (event) => {
        sync();
        const toggle = form.querySelector("[data-spear-automation-toggle]");
        if (toggle?.checked && !toggle.defaultChecked && !window.confirm("Enable SPEAR Automation for the active sort?")) {
            event.preventDefault();
        }
    });
})();
