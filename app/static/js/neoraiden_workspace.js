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
    let selected = pref("list") === "departures" ? "departures" : "arrivals";
    let search = "", remembered = null, priorScroll = 0, resizeFrame = 0;
    function applyTheme(theme) {
        root.dataset.theme = theme;
        const button = one("[data-theme-toggle]");
        button.textContent = theme === "dark" ? "Light" : "Dark";
        button.setAttribute("aria-label", button.textContent + " theme");
        button.setAttribute("aria-pressed", String(theme === "light"));
    }
    function normalView() {
        root.dataset.list = selected;
        for (const button of all("[data-list-choice]"))
            button.setAttribute("aria-pressed", String(button.dataset.listChoice === selected));
        const input = one("[data-search]");
        if (input && input.value !== search) input.value = search;
        const term = search.trim().toLocaleLowerCase();
        for (const panel of all("[data-panel]")) {
            const rows = Array.from(panel.querySelectorAll("[data-mission]"));
            for (const row of rows)
                row.hidden = !!term && !row.querySelector("summary").textContent.toLocaleLowerCase().includes(term);
            panel.querySelector("[data-empty]").hidden = rows.some(row => !row.hidden);
        }
    }
    function capture() {
        return {
            open: all("details[data-mission][open]").map(row => row.dataset.mission),
            staffing: !!one(".rd-staff")?.open,
            scroll: all(".rd-scroll").map(el => el.scrollTop),
            focus: document.activeElement?.closest("[data-mission]")?.dataset.mission,
            searchFocus: document.activeElement === one("[data-search]"),
            caret: one("[data-search]")?.selectionStart,
        };
    }
    function restore(state) {
        normalView();
        if (!state) return;
        for (const row of all("[data-mission]")) row.open = state.open.includes(row.dataset.mission);
        if (one(".rd-staff")) one(".rd-staff").open = state.staffing;
        all(".rd-scroll").forEach((el, i) => { el.scrollTop = state.scroll[i] || 0; });
        const input = one("[data-search]");
        if (state.searchFocus && input) {
            input.focus({preventScroll:true});
            input.setSelectionRange(state.caret, state.caret);
        } else if (state.focus) {
            one('[data-mission="' + state.focus + '"] summary')?.focus({preventScroll:true});
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
        all(".rd-mission summary").forEach(el => el.removeAttribute("tabindex"));
        applyTheme(normalTheme);
        restore(remembered);
        window.scrollTo(0, priorScroll);
        tvButton.focus({preventScroll:true});
        notice(message);
    }
    function fitTV() {
        if (!tv || entering) return;
        for (const panel of all("[data-panel]")) {
            const rows = panel.querySelectorAll("[data-mission]");
            for (const row of rows) {
                row.hidden = false;
                row.open = false;
                row.querySelector("summary").tabIndex = -1;
            }
            panel.querySelector("[data-empty]").hidden = rows.length > 0;
        }
        // With no scrolling or pagination, use the normal workspace if this
        // display cannot fit all flights at the wallboard's minimum type size.
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
        notice(nativeFullscreen ? "" : "Browser fullscreen is unavailable. TV Mode is using this window.");
        entering = false;
        fitTV();
        tvButton.focus({preventScroll:true});
    }
    root.addEventListener("click", event => {
        const button = event.target.closest("button");
        if (tv && event.target.closest(".rd-mission summary")) event.preventDefault();
        if (!button) return;
        if (button.hasAttribute("data-tv")) changeMode();
        if (button.hasAttribute("data-theme-toggle")) {
            applyTheme(root.dataset.theme === "dark" ? "light" : "dark");
            if (!tv) normalTheme = root.dataset.theme;
            remember(tv ? "tv.theme" : "theme",root.dataset.theme);
        }
        if (button.dataset.listChoice) {
            selected = button.dataset.listChoice;
            remember("list",selected);
            normalView();
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
