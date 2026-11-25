(() => {
    "use strict";
    const switchers = [...document.querySelectorAll("[data-character-switcher]")];
    if (!switchers.length) return;

    function fitPanel(switcher) {
        const panel = switcher.querySelector(".character-switcher-panel");
        if (!switcher.open || !panel.getClientRects().length) return;

        panel.style.removeProperty("--character-switcher-shift");
        const {left, width, top} = panel.getBoundingClientRect();
        const edge = 12;
        const viewport = document.documentElement;
        const clampedLeft = Math.max(edge, Math.min(left, viewport.clientWidth - width - edge));
        panel.style.setProperty("--character-switcher-shift", `${clampedLeft - left}px`);
        panel.style.setProperty("--character-switcher-height", `${Math.max(0, viewport.clientHeight - top - edge)}px`);
    }

    const popoverSelector = "details:is([data-character-switcher], .operational-account-menu, .gateway-shell-account-menu, [data-my-alerts-tray], [data-scope-picker])";
    const headers = new Set(switchers.map(switcher => switcher.closest("header")).filter(Boolean));
    for (const header of headers) {
        header.addEventListener("toggle", event => {
            const opened = event.target;
            if (!opened.matches(popoverSelector) || !opened.open) return;
            for (const peer of header.querySelectorAll(popoverSelector)) {
                if (peer !== opened) peer.open = false;
            }
        }, true);
    }

    for (const switcher of switchers) {
        const links = [...switcher.querySelectorAll(".character-switcher-panel a[href]")];
        switcher.addEventListener("toggle", () => {
            if (!switcher.open) return;
            for (const other of switchers) {
                if (other !== switcher) other.open = false;
            }
            fitPanel(switcher);
        });

        switcher.addEventListener("focusout", event => {
            if (event.relatedTarget && !switcher.contains(event.relatedTarget)) switcher.open = false;
        });
        switcher.addEventListener("keydown", event => {
            const key = event.key;
            if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(key)) return;
            if (!switcher.open && (key === "Home" || key === "End")) return;
            event.preventDefault();
            switcher.open = true;
            fitPanel(switcher);
            const current = links.indexOf(document.activeElement);
            const target = key === "Home" ? 0 : key === "End" ? links.length - 1 :
                key === "ArrowDown" ? (current + 1) % links.length :
                current <= 0 ? links.length - 1 : current - 1;
            links[target].focus();
        });
    }

    document.addEventListener("click", event => {
        for (const switcher of switchers) {
            if (!switcher.contains(event.target)) switcher.open = false;
        }
    });
    document.addEventListener("keydown", event => {
        if (event.key !== "Escape") return;
        const openSwitcher = switchers.find(switcher => switcher.open && switcher.getClientRects().length);
        if (!openSwitcher) return;
        event.preventDefault();
        openSwitcher.open = false;
        openSwitcher.querySelector("summary").focus();
    });
    window.addEventListener("resize", () => switchers.forEach(fitPanel));
})();
