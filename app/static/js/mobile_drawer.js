/* One mobile navigation lifecycle for Portal, Gateway, and operational nodes. */
(() => {
    "use strict";
    const isDismissSwipe = (dx, dy, elapsed) => dx >= 80 && Math.abs(dy) <= 30 && dx > Math.abs(dy) * 3 && elapsed < 900;
    if (typeof module !== "undefined") module.exports = { isDismissSwipe };
    if (typeof document === "undefined") return;
    const root = document.querySelector("[data-mobile-navigation]");
    if (!root) return;
    const body = document.body;
    const panel = root.querySelector("[data-mobile-drawer]");
    const backdrop = root.querySelector("[data-drawer-backdrop]");
    const toggle = root.querySelector("[data-drawer-toggle]");
    const nodes = root.querySelector("[data-drawer-nodes]");
    const views = Array.from(panel.querySelectorAll("[data-drawer-view]"));
    const mobile = window.matchMedia("(max-width: 900px)");
    const header = document.querySelector("[data-operational-mobile-header], [data-gateway-mobile-header], [data-mobile-topbar]");
    const hiddenClass = body.hasAttribute("data-operational-shell") ? "operational-mobile-header-hidden" :
        body.hasAttribute("data-gateway-shell") ? "gateway-mobile-header-hidden" : "neo-mobile-header-hidden";
    let open = false, opener = null, savedY = 0, inactive = [], gesture = null, touchY = null;
    let mode = "closed";
    const setMode = (next) => {
        mode = next;
        views.forEach(view => { view.hidden = view.dataset.drawerView !== mode; view.inert = view.hidden; });
        toggle.setAttribute("aria-expanded", String(mode === "menu"));
        nodes.setAttribute("aria-expanded", String(mode === "nodes"));
        toggle.querySelector("[data-drawer-toggle-label]").textContent = mode === "menu" ? "Close" : "Menu";
        nodes.querySelector("[data-drawer-nodes-label]").textContent = mode === "nodes" ? "Close" : "Nodes";
        root.setAttribute("aria-label", mode === "nodes" ? "Nodes" : root.dataset.menuTitle);
        panel.setAttribute("aria-label", root.getAttribute("aria-label"));
        panel.scrollTop = 0;
    };
    let lastY = window.scrollY, down = 0, up = 0, ticking = false, settling = false;
    const focusable = () => Array.from(root.querySelectorAll('a[href], button, input, select, textarea, summary, [tabindex="0"]'))
        .filter(el => !el.disabled && !el.closest("[inert]") && el.getClientRects().length);
    const resetHeader = () => {
        lastY = Math.max(0, window.scrollY); down = 0; up = 0;
        body.classList.remove(hiddenClass);
    };
    const close = (restoreFocus = true) => {
        if (!open) return;
        open = false; gesture = null;
        panel.hidden = true; panel.inert = true; backdrop.hidden = true;
        root.removeAttribute("role"); root.removeAttribute("aria-modal");
        setMode("closed");
        body.classList.remove("neo-mobile-drawer-open");
        inactive.forEach(([el, inert]) => { el.inert = inert; }); inactive = [];
        document.documentElement.classList.remove("neo-drawer-scroll-locked");
        settling = true;
        const scrollBehavior = document.documentElement.style.scrollBehavior;
        document.documentElement.style.scrollBehavior = "auto";
        window.scrollTo(0, savedY);
        document.documentElement.style.scrollBehavior = scrollBehavior;
        resetHeader();
        if (restoreFocus && opener?.isConnected) opener.focus({preventScroll:true});
        requestAnimationFrame(() => { resetHeader(); settling = false; });
    };
    const show = (source, next) => {
        if (!mobile.matches || body.classList.contains("operational-board-view")) return;
        if (open) {
            if (mode === next) { close(); return; }
            opener = source;
            setMode(next);
            panel.querySelector("[data-drawer-close]").focus({preventScroll:true});
            return;
        }
        opener = source; savedY = window.scrollY;
        open = true;
        /* Root is a direct body child; bottom CLOSE stays inside the active region. */
        inactive = Array.from(body.children).filter(el => el !== root && !["SCRIPT", "STYLE", "LINK"].includes(el.tagName)).map(el => [el, el.inert]);
        inactive.forEach(([el]) => { el.inert = true; });
        document.documentElement.classList.add("neo-drawer-scroll-locked");
        body.classList.add("neo-mobile-drawer-open");
        root.setAttribute("role", "dialog"); root.setAttribute("aria-modal", "true");
        panel.hidden = false; panel.inert = false; backdrop.hidden = false;
        setMode(next);
        resetHeader();
        panel.querySelector("[data-drawer-close]").focus({preventScroll:true});
    };
    toggle.addEventListener("click", () => show(toggle, "menu"));
    root.querySelector("[data-drawer-close]").addEventListener("click", () => close());
    backdrop.addEventListener("click", () => close());
    nodes.addEventListener("click", () => show(nodes, "nodes"));
    /* Lock only while this drawer is active. Never move the body/fixed dock.
       Boundary guards also prevent rubber-band scroll chaining on iOS. */
    document.addEventListener("touchstart", event => {
        touchY = event.touches.length === 1 ? event.touches[0].clientY : null;
    }, {passive:true});
    document.addEventListener("touchmove", event => {
        if (!open || event.touches.length !== 1 || touchY === null) return;
        const dy = event.touches[0].clientY - touchY;
        touchY = event.touches[0].clientY;
        if (!panel.contains(event.target) ||
            (dy > 0 && panel.scrollTop <= 0) ||
            (dy < 0 && panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 1)) event.preventDefault();
    }, {passive:false});
    document.addEventListener("wheel", event => {
        if (open && !panel.contains(event.target)) event.preventDefault();
    }, {passive:false});
    document.addEventListener("keydown", event => {
        if (!open) return;
        if (event.key === "Escape") { event.preventDefault(); close(); }
        if (event.key === "Tab") {
            const items = focusable(), first = items[0], last = items[items.length - 1];
            if (event.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) {
                event.preventDefault(); last?.focus();
            } else if (!event.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) {
                event.preventDefault(); first?.focus();
            }
        }
    });
    document.addEventListener("focusin", event => {
        if (open && !root.contains(event.target)) focusable()[0]?.focus({preventScroll:true});
    });
    const interactive = 'a, button, input, select, textarea, summary, [contenteditable="true"]';
    panel.addEventListener("pointerdown", event => {
        gesture = null;
        if (event.pointerType !== "touch" || !event.isPrimary || event.target.closest(interactive) || window.getSelection()?.toString()) return;
        gesture = {x:event.clientX, y:event.clientY, time:event.timeStamp, id:event.pointerId};
    }, {passive:true});
    panel.addEventListener("pointermove", event => {
        if (gesture && Math.abs(event.clientY - gesture.y) > 12) gesture = null;
    }, {passive:true});
    panel.addEventListener("pointercancel", () => { gesture = null; }, {passive:true});
    panel.addEventListener("pointerup", event => {
        if (gesture && gesture.id === event.pointerId &&
            !window.getSelection()?.toString() &&
            isDismissSwipe(event.clientX - gesture.x, event.clientY - gesture.y, event.timeStamp - gesture.time)) close();
        gesture = null;
    }, {passive:true});
    root.addEventListener("click", event => {
        if (event.target.closest("a[href]")) close(false);
    });
    root.addEventListener("submit", () => close(false));
    window.addEventListener("pagehide", () => close(false));
    window.addEventListener("pageshow", resetHeader);
    mobile.addEventListener("change", () => { if (!mobile.matches) close(); resetHeader(); });
    document.addEventListener("neo:board-enter", () => close(false));
    /* The shared drawer owns mobile header visibility; no parallel shell handlers. */
    const headerPopoverOpen = () => Boolean(header?.querySelector("details[open]"));
    header?.querySelectorAll("details").forEach(el => el.addEventListener("toggle", () => { if (el.open) resetHeader(); }));
    const updateHeader = () => {
        ticking = false;
        if (open || settling || !mobile.matches || headerPopoverOpen()) { resetHeader(); return; }
        const y = Math.max(0, window.scrollY), delta = y - lastY;
        if (y < 16) resetHeader();
        else if (delta > 0) { down += delta; up = 0; if (down >= 26) body.classList.add(hiddenClass); }
        else if (delta < 0) { up -= delta; down = 0; if (up >= 12) body.classList.remove(hiddenClass); }
        lastY = y;
    };
    window.addEventListener("scroll", () => {
        if (!ticking) { ticking = true; requestAnimationFrame(updateHeader); }
    }, {passive:true});
    root.querySelector("[data-drawer-copy-link]")?.addEventListener("click", async event => {
        const url = event.currentTarget.dataset.shareUrl;
        const status = root.querySelector("[data-drawer-copy-status]");
        try { await navigator.clipboard.writeText(url); status.textContent = "Link copied"; }
        catch (_error) { status.textContent = url; }
    });
})();
