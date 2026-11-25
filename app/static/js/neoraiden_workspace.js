(() => {
    "use strict";
    const root = document.querySelector("[data-raiden]");
    if (!root) return;
    const one = selector => root.querySelector(selector);
    const all = selector => Array.from(root.querySelectorAll(selector));
    const pref = key => {
        try { return localStorage.getItem("neoapps.raiden." + key); } catch (_) { return null; }
    };
    const remember = (key, value) => {
        try { localStorage.setItem("neoapps.raiden." + key, value); } catch (_) { /* Device storage is optional. */ }
    };
    const marker = document.createComment("NeoRaiden workspace");
    const tvButton = one("[data-tv]");
    const fullscreenTarget = document.documentElement;
    let tv = false, nativeFullscreen = false, entering = false;
    let normalTheme = pref("theme") || "dark";
    let search = "", remembered = null, priorScroll = 0, resizeFrame = 0;
    function applyTheme(theme) {
        root.dataset.theme = theme;
        const button = one("[data-theme-toggle]");
        button.textContent = theme === "dark" ? "Light" : "Dark";
        button.setAttribute("aria-label", button.textContent + " theme");
        button.setAttribute("aria-pressed", String(theme === "light"));
    }
    function normalView() {
        const input = one("[data-search]");
        if (input && input.value !== search) input.value = search;
        const term = search.trim().toLocaleLowerCase();
        const rows = all("[data-mission]");
        for (const row of rows)
            row.hidden = !!term && !row.textContent.toLocaleLowerCase().includes(term);
        const empty = one("[data-empty]");
        if (empty) empty.hidden = rows.some(row => !row.hidden);
    }
    function capture() {
        return {
            staffing: !!one(".rd-staff")?.open,
            searchFocus: document.activeElement === one("[data-search]"),
            caret: one("[data-search]")?.selectionStart,
        };
    }
    function restore(state) {
        normalView();
        if (!state) return;
        if (one(".rd-staff")) one(".rd-staff").open = state.staffing;
        const input = one("[data-search]");
        if (state.searchFocus && input) {
            input.focus({preventScroll:true});
            input.setSelectionRange(state.caret, state.caret);
        }
    }
    function notice(message) {
        const status = one("[data-tv-notice]");
        status.textContent = message;
        status.hidden = !message;
    }
    function exitTV(message = "") {
        tv = false;
        if (nativeFullscreen && document.fullscreenElement === fullscreenTarget) {
            document.exitFullscreen().catch(() => {});
        }
        nativeFullscreen = false;
        document.body.classList.remove("rd-projecting");
        root.classList.remove("rd-tv");
        marker.replaceWith(root);
        tvButton.textContent = "TV Mode";
        tvButton.setAttribute("aria-pressed", "false");
        applyTheme(normalTheme);
        restore(remembered);
        window.scrollTo(0, priorScroll);
        tvButton.focus({preventScroll:true});
        notice(message);
    }
    function fitTV() {
        if (!tv || entering) return;
        const rows = all("[data-mission]");
        for (const row of rows) row.hidden = false;
        const empty = one("[data-empty]");
        if (empty) empty.hidden = rows.length > 0;
        if (rows.length) {
            // Budget for the column headings and one line per mission.
            const tableTop = one(".rd-missions").getBoundingClientRect().top;
            const available = root.clientHeight - tableTop - 4;
            const size = Math.min(window.innerHeight * .017, available / ((rows.length + 1) * 1.05));
            let font = Math.max(10, Math.floor(size * 4) / 4);
            root.style.setProperty("--rd-row-font", font + "px");
            while (font > 10 && root.scrollHeight > root.clientHeight) {
                font -= .25;
                root.style.setProperty("--rd-row-font", font + "px");
            }
        }
        if (root.scrollHeight > root.clientHeight + 1 || root.scrollWidth > root.clientWidth + 1) {
            exitTV("TV Mode cannot fit every flight legibly at this display size. All flights are available in God Mode.");
        }
    }
    function scheduleFit() {
        cancelAnimationFrame(resizeFrame);
        if (tv) resizeFrame = requestAnimationFrame(fitTV);
    }
    async function changeMode() {
        if (entering) return;
        if (tv) { exitTV(); return; }
        entering = true;
        try {
            await fullscreenTarget.requestFullscreen();
            nativeFullscreen = document.fullscreenElement === fullscreenTarget;
        } catch (_) {
            nativeFullscreen = false;
        }
        if (!nativeFullscreen) {
            entering = false;
            notice("TV Mode requires browser fullscreen. Allow fullscreen and try again.");
            return;
        }
        remembered = capture();
        priorScroll = window.scrollY;
        root.before(marker);
        document.body.append(root);
        tv = true;
        root.classList.add("rd-tv");
        document.body.classList.add("rd-projecting");
        tvButton.textContent = "Exit TV Mode";
        tvButton.setAttribute("aria-pressed", "true");
        applyTheme(pref("tv.theme") || root.dataset.theme);
        notice("");
        entering = false;
        fitTV();
        tvButton.focus({preventScroll:true});
    }
    root.addEventListener("click", event => {
        const button = event.target.closest("button");
        if (!button) return;
        if (button.hasAttribute("data-tv")) changeMode();
        if (button.hasAttribute("data-theme-toggle")) {
            applyTheme(root.dataset.theme === "dark" ? "light" : "dark");
            if (!tv) normalTheme = root.dataset.theme;
            remember(tv ? "tv.theme" : "theme",root.dataset.theme);
        }
    });
    root.addEventListener("input", event => {
        if (event.target.matches("[data-search]")) { search = event.target.value; normalView(); }
    });
    document.addEventListener("keydown", event => { if (tv && event.key === "Escape") exitTV(); });
    document.addEventListener("fullscreenchange", () => {
        if (tv && nativeFullscreen && document.fullscreenElement !== fullscreenTarget) exitTV();
    });
    const observer = new ResizeObserver(scheduleFit);
    observer.observe(root);
    observer.observe(one("[data-stale]"));
    root.addEventListener("raiden:before-update", () => { if (!tv) remembered = capture(); });
    root.addEventListener("raiden:updated", () => {
        if (tv) fitTV(); else restore(remembered);
    });
    applyTheme(normalTheme);
    normalView();
})();
