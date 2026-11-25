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
    const expanded = new Set();
    function showMissionDetails(row, open) {
        row.toggleAttribute("data-expanded", open);
        row.querySelector("[data-mission-details]").setAttribute("aria-expanded", String(open));
    }
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
    function normalView(followMatch = false) {
        const input = one("[data-search]");
        if (input && input.value !== search) input.value = search;
        const term = search.trim().toLocaleLowerCase();
        const rows = all("[data-mission]");
        for (const row of rows) {
            row.hidden = !!term && !row.textContent.toLocaleLowerCase().includes(term);
            showMissionDetails(row, expanded.has(row.dataset.mission));
        }
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
            staffing: !!one(".rd-staff")?.open,
            searchFocus: document.activeElement === one("[data-search]"),
            caret: one("[data-search]")?.selectionStart,
            missionFocus: document.activeElement?.matches("[data-mission-details]") &&
                document.activeElement.closest("[data-mission]").dataset.mission,
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
        if (state.missionFocus) {
            one(`#rd-mission-${state.missionFocus} [data-mission-details]`)?.focus({preventScroll:true});
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
        const tables = all(".rd-missions");
        const overflows = () => root.scrollHeight > root.clientHeight + 1 ||
            root.scrollWidth > root.clientWidth + 1 ||
            tables.some(table => table.getBoundingClientRect().width > table.parentElement.clientWidth + 1);
        if (tables.length) {
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
        }
        if (overflows()) {
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
        if (button.hasAttribute("data-show-missions")) showMobileView(button.dataset.showMissions);
        if (button.hasAttribute("data-mission-details")) {
            const row = button.closest("[data-mission]");
            const id = row.dataset.mission;
            const open = !expanded.delete(id);
            if (open) expanded.add(id);
            showMissionDetails(row, open);
        }
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
        if (tv) fitTV(); else restore(remembered);
    });
    applyTheme(normalTheme);
    normalView();
})();
