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
    const character = root.querySelector("[data-drawer-character]");
    const mobile = window.matchMedia("(max-width: 900px)");
    const header = document.querySelector("[data-operational-mobile-header], [data-gateway-mobile-header], [data-mobile-topbar]");
    const hiddenClass = body.hasAttribute("data-operational-shell") ? "operational-mobile-header-hidden" :
        body.hasAttribute("data-gateway-shell") ? "gateway-mobile-header-hidden" : "neo-mobile-header-hidden";
    let open = false, opener = null, savedY = 0, savedStyle = "", inactive = [], gesture = null;
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
        toggle.setAttribute("aria-expanded", "false"); character.setAttribute("aria-expanded", "false");
        toggle.querySelector("[data-drawer-toggle-label]").textContent = "Menu";
        body.classList.remove("neo-mobile-drawer-open");
        inactive.forEach(([el, inert]) => { el.inert = inert; }); inactive = [];
        body.style.cssText = savedStyle;
        settling = true;
        const scrollBehavior = document.documentElement.style.scrollBehavior;
        document.documentElement.style.scrollBehavior = "auto";
        window.scrollTo(0, savedY);
        document.documentElement.style.scrollBehavior = scrollBehavior;
        resetHeader();
        if (restoreFocus && opener?.isConnected) opener.focus({preventScroll:true});
        requestAnimationFrame(() => { resetHeader(); settling = false; });
    };
    const show = (source) => {
        if (open || !mobile.matches || body.classList.contains("operational-board-view")) return;
        opener = source; savedY = window.scrollY; savedStyle = body.style.cssText;
        open = true;
        /* Root is a direct body child; bottom CLOSE stays inside the active region. */
        inactive = Array.from(body.children).filter(el => el !== root && !["SCRIPT", "STYLE", "LINK"].includes(el.tagName)).map(el => [el, el.inert]);
        inactive.forEach(([el]) => { el.inert = true; });
        body.style.position = "fixed"; body.style.top = `-${savedY}px`; body.style.width = "100%";
        body.classList.add("neo-mobile-drawer-open");
        root.setAttribute("role", "dialog"); root.setAttribute("aria-modal", "true");
        panel.hidden = false; panel.inert = false; backdrop.hidden = false;
        toggle.setAttribute("aria-expanded", "true");
        character.setAttribute("aria-expanded", "true");
        toggle.querySelector("[data-drawer-toggle-label]").textContent = "Close";
        resetHeader();
        panel.querySelector("[data-drawer-close]").focus({preventScroll:true});
    };
    toggle.addEventListener("click", () => open ? close() : show(toggle));
    root.querySelector("[data-drawer-close]").addEventListener("click", () => close());
    backdrop.addEventListener("click", () => close());
    character.addEventListener("click", () => {
        show(character);
        const switcher = panel.querySelector("[data-character-switcher]");
        if (switcher) {
            switcher.open = true;
            switcher.querySelector("summary").focus({preventScroll:true});
            switcher.scrollIntoView({block:"nearest"});
        }
    });
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
