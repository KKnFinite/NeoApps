(() => {
    "use strict";

    const body = document.body;
    if (!body?.matches("[data-gateway-shell]")) return;

    const header = document.querySelector("[data-gateway-mobile-header]");
    const drawer = document.querySelector("[data-gateway-mobile-drawer]");
    const toggles = document.querySelectorAll("[data-gateway-mobile-menu-toggle]");
    const closeButtons = document.querySelectorAll("[data-gateway-mobile-menu-close]");
    const setDrawerOpen = (open) => {
        if (!drawer) return;
        drawer.classList.toggle("is-open", open);
        drawer.setAttribute("aria-hidden", String(!open));
        toggles.forEach((toggle) => toggle.setAttribute("aria-expanded", String(open)));
        closeButtons.forEach((button) => { button.hidden = !open; });
        body.classList.toggle("gateway-mobile-menu-open", open);
        if (open) body.classList.remove("gateway-mobile-header-hidden");
    };

    toggles.forEach((toggle) => toggle.addEventListener("click", () => setDrawerOpen(!drawer?.classList.contains("is-open"))));
    closeButtons.forEach((button) => button.addEventListener("click", () => setDrawerOpen(false)));
    drawer?.addEventListener("click", (event) => {
        if (event.target.closest("a")) setDrawerOpen(false);
    });
    document.querySelector("[data-gateway-mobile-character-toggle]")?.addEventListener("click", () => {
        setDrawerOpen(true);
        const switcher = drawer?.querySelector("[data-character-switcher]");
        if (switcher) switcher.open = true;
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") setDrawerOpen(false);
    });

    if (!header || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const headerPopoverOpen = () => Boolean(header.querySelector("details[open]"));
    header.querySelectorAll("details").forEach((popover) => popover.addEventListener("toggle", () => {
        if (popover.open) body.classList.remove("gateway-mobile-header-hidden");
    }));
    let lastY = Math.max(0, window.scrollY);
    let downDistance = 0;
    let upDistance = 0;
    let ticking = false;
    const updateHeader = () => {
        ticking = false;
        if (window.innerWidth > 900 || body.classList.contains("gateway-mobile-menu-open") || headerPopoverOpen()) {
            body.classList.remove("gateway-mobile-header-hidden");
            return;
        }
        const currentY = Math.max(0, window.scrollY);
        if (currentY < 16) {
            body.classList.remove("gateway-mobile-header-hidden");
            downDistance = 0;
            upDistance = 0;
        } else {
            const delta = currentY - lastY;
            if (delta > 0) {
                downDistance += delta;
                upDistance = 0;
                if (downDistance >= 26) body.classList.add("gateway-mobile-header-hidden");
            } else if (delta < 0) {
                upDistance += Math.abs(delta);
                downDistance = 0;
                if (upDistance >= 12) body.classList.remove("gateway-mobile-header-hidden");
            }
        }
        lastY = currentY;
    };
    window.addEventListener("scroll", () => {
        if (!ticking) {
            ticking = true;
            window.requestAnimationFrame(updateHeader);
        }
    }, { passive: true });
})();
