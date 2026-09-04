(() => {
    "use strict";

    const body = document.body;
    if (!body?.matches("[data-operational-shell]")) return;

    const storage = {
        get(key) {
            try { return window.localStorage.getItem(key); } catch (_error) { return null; }
        },
        set(key, value) {
            try { window.localStorage.setItem(key, value); } catch (_error) { /* optional preference */ }
        },
    };

    const sidebar = document.querySelector("[data-operational-sidebar]");
    const sidebarToggle = document.querySelector("[data-operational-sidebar-toggle]");
    const sidebarKey = "neoapps.operational-shell.sidebar.v1";
    const setSidebarCollapsed = (collapsed) => {
        body.classList.toggle("operational-sidebar-collapsed", collapsed);
        sidebarToggle?.setAttribute("aria-expanded", String(!collapsed));
        if (sidebarToggle) {
            sidebarToggle.setAttribute("aria-label", `${collapsed ? "Expand" : "Collapse"} node menu`);
        }
    };
    if (sidebar && sidebarToggle) {
        setSidebarCollapsed(storage.get(sidebarKey) === "collapsed");
        sidebarToggle.addEventListener("click", () => {
            const collapsed = !body.classList.contains("operational-sidebar-collapsed");
            setSidebarCollapsed(collapsed);
            storage.set(sidebarKey, collapsed ? "collapsed" : "expanded");
        });
    }

    const boardSupported = body.dataset.operationalBoardSupported === "true";
    const boardKey = `neoapps.operational-shell.board.v1:${window.location.pathname}`;
    const boardButtons = Array.from(document.querySelectorAll("[data-operational-board-toggle]"));
    const boardExit = document.querySelector(".operational-board-exit");
    const setBoardView = (enabled) => {
        if (!boardSupported) return;
        body.classList.toggle("operational-board-view", enabled);
        if (boardExit) boardExit.hidden = !enabled;
        boardButtons.forEach((button) => {
            button.setAttribute("aria-expanded", String(enabled));
            const label = button.querySelector("strong") || button;
            if (label && !button.classList.contains("operational-board-exit")) {
                label.textContent = enabled ? "Exit Board" : "Board View";
            }
        });
        storage.set(boardKey, enabled ? "on" : "off");
    };
    if (boardSupported && boardButtons.length) {
        setBoardView(storage.get(boardKey) === "on");
        boardButtons.forEach((button) => button.addEventListener("click", () => {
            setBoardView(!body.classList.contains("operational-board-view"));
        }));
    }

    const header = document.querySelector("[data-operational-mobile-header]");
    const drawer = document.querySelector("[data-operational-mobile-drawer]");
    const drawerToggles = document.querySelectorAll("[data-operational-mobile-menu-toggle]");
    const drawerClose = document.querySelector("[data-operational-mobile-menu-close]");
    const setDrawerOpen = (open) => {
        if (!drawer) return;
        drawer.classList.toggle("is-open", open);
        drawer.setAttribute("aria-hidden", String(!open));
        drawerToggles.forEach((toggle) => toggle.setAttribute("aria-expanded", String(open)));
        if (drawerClose) drawerClose.hidden = !open;
        body.classList.toggle("operational-mobile-menu-open", open);
        if (open) body.classList.remove("operational-mobile-header-hidden");
    };
    drawerToggles.forEach((toggle) => toggle.addEventListener("click", () => setDrawerOpen(!drawer?.classList.contains("is-open"))));
    drawerClose?.addEventListener("click", () => setDrawerOpen(false));
    drawer?.addEventListener("click", (event) => {
        if (event.target.closest("a")) setDrawerOpen(false);
    });
    document.querySelector("[data-operational-mobile-character-toggle]")?.addEventListener("click", () => {
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
        if (popover.open) body.classList.remove("operational-mobile-header-hidden");
    }));
    let lastY = Math.max(0, window.scrollY);
    let downDistance = 0;
    let upDistance = 0;
    let ticking = false;
    const updateHeader = () => {
        ticking = false;
        if (body.classList.contains("operational-mobile-menu-open") || headerPopoverOpen() || window.innerWidth > 900) {
            body.classList.remove("operational-mobile-header-hidden");
            return;
        }
        const currentY = Math.max(0, window.scrollY);
        if (currentY < 16) {
            body.classList.remove("operational-mobile-header-hidden");
            downDistance = 0;
            upDistance = 0;
            lastY = currentY;
            return;
        }
        const delta = currentY - lastY;
        if (delta > 0) {
            downDistance += delta;
            upDistance = 0;
            if (downDistance >= 26) body.classList.add("operational-mobile-header-hidden");
        } else if (delta < 0) {
            upDistance += Math.abs(delta);
            downDistance = 0;
            if (upDistance >= 12) body.classList.remove("operational-mobile-header-hidden");
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
