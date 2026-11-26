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
    let search = "", mobileView = "arrivals", remembered = null, priorScroll = 0, resizeFrame = 0;
    function applyTheme(theme) {
        root.dataset.theme = theme;
        const button = one("[data-theme-toggle]");
        button.textContent = theme === "dark" ? "Light" : "Dark";
        button.setAttribute("aria-label", button.textContent + " theme");
        button.setAttribute("aria-pressed", String(theme === "light"));
    }
    function showMobileView(direction) {
        const groups = one(".rd-mission-groups");
        if (!groups) return;
        mobileView = direction;
        groups.dataset.mobileView = direction;
        for (const button of all("[data-show-missions]"))
            button.setAttribute("aria-pressed", String(button.dataset.showMissions === direction));
    }
    function updateContext() {
        const sort = one("[data-sort-context]")?.dataset.sortContext;
        const context = one("[data-tv-context]");
        context.textContent = context.dataset.gateway + (sort ? " · " + sort : "");
        one("[data-current-sort]").textContent = "Current Sort" + (sort && one("[data-current-sort]").selected ? " · " + sort : "");
        one("[data-search]").hidden = !sort;
    }
    function normalView(followMatch = false) {
        const input = one("[data-search]");
        if (input && input.value !== search) input.value = search;
        const term = search.trim().toLocaleLowerCase();
        const rows = all("[data-mission]");
        for (const row of rows) {
            row.hidden = !!term && !row.textContent.toLocaleLowerCase().includes(term);
        }
        const visible = new Set(rows.filter(row => !row.hidden).map(row => row.dataset.mission));
        for (const row of all("[data-paired-mission]")) row.hidden = !visible.has(row.dataset.pairedMission);
        const matches = [];
        for (const group of all("[data-mission-group]")) {
            const hasMatch = !!group.querySelector("[data-mission]:not([hidden])");
            group.querySelector("[data-empty]").hidden = hasMatch;
            if (hasMatch) matches.push(group.dataset.missionGroup);
        }
        showMobileView(followMatch && matches.length === 1 ? matches[0] : mobileView);
    }
    function capture() {
        return {
            searchFocus: document.activeElement === one("[data-search]"),
            caret: one("[data-search]")?.selectionStart,
        };
    }
    function restore(state) {
        normalView();
        if (!state) return;
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
        for (const group of all("[data-mission-group]")) {
            const rows = group.querySelectorAll("[data-mission]");
            for (const row of rows) row.hidden = false;
            group.querySelector("[data-empty]").hidden = rows.length > 0;
        }
        const tables = all(".rd-missions:not(.rd-paired)");
        const overflows = () => root.scrollHeight > root.clientHeight + 1 ||
            root.scrollWidth > root.clientWidth + 1 ||
            tables.some(table => table.getBoundingClientRect().width > table.parentElement.clientWidth + 1);
        if (tables.length) {
            // Keep the existing font fit. Extra spacing can affect capacity,
            // but must not cause the mission text to shrink.
            root.classList.add("rd-measuring-tv");
            try {
                const rowCount = Math.max(...tables.map(table => table.rows.length));
                const available = root.clientHeight - tables[0].getBoundingClientRect().top - 4;
                const preferred = Math.min(24, window.innerHeight * .016);
                const size = Math.min(preferred, available / (rowCount * 1.25));
                let font = Math.max(10, Math.floor(size * 4) / 4);
                root.style.setProperty("--rd-row-font", font + "px");
                while (font > 10 && overflows()) {
                    font -= .25;
                    root.style.setProperty("--rd-row-font", font + "px");
                }
            } finally {
                root.classList.remove("rd-measuring-tv");
            }
        }
        if (overflows()) {
            exitTV("TV Mode cannot fit every flight legibly at this display size. All flights are available in the workspace.");
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
        if (button.hasAttribute("data-show-missions")) showMobileView(button.dataset.showMissions);
        if (button.hasAttribute("data-theme-toggle")) {
            applyTheme(root.dataset.theme === "dark" ? "light" : "dark");
            if (!tv) normalTheme = root.dataset.theme;
            remember(tv ? "tv.theme" : "theme",root.dataset.theme);
        }
    });
    root.addEventListener("input", event => {
        if (event.target.matches("[data-search]")) { search = event.target.value; normalView(!!search.trim()); }
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
        updateContext();
        if (tv) fitTV(); else restore(remembered);
    });
    applyTheme(normalTheme);
    updateContext();
    normalView();
})();
