(() => {
    "use strict";

    const toggle = document.querySelector("[data-neoscorpion-menu-toggle]");
    const scrim = document.querySelector("[data-neoscorpion-menu-scrim]");
    const menu = document.querySelector("[data-node-desktop-side-nav][data-node-desktop-shell='scorpion']");
    if (!toggle || !scrim || !menu) return;

    menu.id = "neoscorpion-overlay-menu";
    const close = () => {
        document.body.classList.remove("neoscorpion-menu-open");
        toggle.setAttribute("aria-expanded", "false");
        scrim.hidden = true;
    };
    const open = () => {
        document.body.classList.add("neoscorpion-menu-open");
        toggle.setAttribute("aria-expanded", "true");
        scrim.hidden = false;
    };

    toggle.addEventListener("click", () => {
        if (document.body.classList.contains("neoscorpion-menu-open")) close();
        else open();
    });
    scrim.addEventListener("click", close);
    menu.addEventListener("click", (event) => {
        if (event.target.closest("a")) close();
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") close();
    });
})();
