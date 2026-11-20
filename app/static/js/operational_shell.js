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
    const mobile = window.matchMedia("(max-width: 900px)");
    const boardKey = `neoapps.operational-shell.board.v1:${window.location.pathname}`;
    const boardButtons = Array.from(document.querySelectorAll("[data-operational-board-toggle]"));
    const boardExit = document.querySelector(".operational-board-exit");
    let readingPosition;
    const setBoardView = (enabled) => {
        if (!boardSupported) return;
        enabled = enabled && !mobile.matches;
        const changed = body.classList.contains("operational-board-view") !== enabled;
        if (changed && enabled) readingPosition = [window.scrollX, window.scrollY];
        if (enabled) document.dispatchEvent(new Event("neo:board-enter"));
        body.classList.toggle("operational-board-view", enabled);
        if (changed) requestAnimationFrame(() => {
            window.dispatchEvent(new Event("resize"));
            if (!enabled && readingPosition) window.scrollTo(...readingPosition);
        });
        if (boardExit) boardExit.hidden = !enabled;
        boardButtons.forEach((button) => {
            button.setAttribute("aria-expanded", String(enabled));
            const label = button.querySelector("strong") || button;
            if (label && !button.classList.contains("operational-board-exit")) {
                label.textContent = enabled ? "Exit Board" : "Board View";
            }
        });
    };
    if (boardSupported && boardButtons.length) {
        setBoardView(storage.get(boardKey) === "on");
        // Breakpoint changes affect presentation, never the desktop preference.
        mobile.addEventListener("change", () => setBoardView(storage.get(boardKey) === "on"));
        boardButtons.forEach((button) => button.addEventListener("click", () => {
            if (mobile.matches) return;
            const enabled = !body.classList.contains("operational-board-view");
            setBoardView(enabled);
            storage.set(boardKey, enabled ? "on" : "off");
            (enabled ? boardExit : sidebar?.querySelector("[data-operational-board-toggle]"))?.focus({preventScroll: true});
        }));
    }
    // The <head> boot class prevents Board View chrome from flashing before
    // this deferred shell script can restore the persisted preference.
    document.documentElement.classList.remove("operational-board-view-boot");

})();
