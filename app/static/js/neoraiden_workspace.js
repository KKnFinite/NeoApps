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
    const board = one("[data-board]");
    let tv = false, page = 0, pages = [], pageCount = 1, paused = false, rotation = 0;
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
    function showPage(index) {
        page = (index + pageCount) % pageCount;
        for (const group of pages) {
            const visible = group.slices[Math.min(page, group.slices.length - 1)] || [];
            group.rows.forEach(row => { row.hidden = !visible.includes(row); });
        }
        one("[data-page]").textContent = "Page " + (page + 1) + " / " + pageCount;
        for (const key of ["prev","next","pause"]) one("[data-" + key + "]").disabled = pageCount < 2;
    }
    function paginate() {
        if (!tv) return;
        pages = all("[data-panel]").map(panel => {
            const rows = Array.from(panel.querySelectorAll("[data-mission]"));
            rows.forEach(row => { row.hidden = false; row.open = false; row.querySelector("summary").tabIndex = -1; });
            panel.querySelector("[data-empty]").hidden = rows.length > 0;
            const height = panel.querySelector(".rd-scroll").clientHeight - panel.querySelector(".rd-columns").getBoundingClientRect().height - 1;
            const slices = [];
            let used = 0, slice = [];
            for (const row of rows) {
                const h = row.getBoundingClientRect().height;
                if (slice.length && used + h > height) {
                    slices.push(slice);
                    slice = []; used = 0;
                }
                slice.push(row);
                used += h;
            }
            if (slice.length) slices.push(slice);
            return {rows,slices};
        });
        pageCount = Math.max(1, ...pages.map(group => group.slices.length));
        showPage(Math.min(page,pageCount - 1));
    }
    function changeMode() {
        const enter = one("[data-tv]");
        if (!tv) {
            remembered = capture();
            priorScroll = window.scrollY;
            root.before(marker);
            document.body.append(root);
            tv = true;
            page = 0;
            root.classList.add("rd-tv");
            document.body.classList.add("rd-projecting");
            enter.textContent = "Exit TV Mode";
            one(".rd-pager").hidden = false;
            applyTheme(pref("tv.theme") || root.dataset.theme);
            paginate();
            rotation = window.setInterval(() => {
                if (!paused && !document.hidden && pageCount > 1) showPage(page + 1);
            }, 10000);
        } else {
            tv = false;
            clearInterval(rotation);
            document.body.classList.remove("rd-projecting");
            root.classList.remove("rd-tv");
            marker.replaceWith(root);
            one(".rd-pager").hidden = true;
            enter.textContent = "TV Mode";
            all(".rd-mission summary").forEach(el => el.removeAttribute("tabindex"));
            applyTheme(pref("theme") || "dark");
            restore(remembered);
            window.scrollTo(0,priorScroll);
        }
        enter.focus({preventScroll:true});
    }
    root.addEventListener("click", event => {
        const button = event.target.closest("button");
        if (tv && event.target.closest(".rd-mission summary")) event.preventDefault();
        if (!button) return;
        if (button.hasAttribute("data-tv")) changeMode();
        if (button.hasAttribute("data-theme-toggle")) {
            applyTheme(root.dataset.theme === "dark" ? "light" : "dark");
            remember(tv ? "tv.theme" : "theme",root.dataset.theme);
        }
        if (button.dataset.listChoice) {
            selected = button.dataset.listChoice;
            remember("list",selected);
            normalView();
        }
        if (button.hasAttribute("data-prev")) showPage(page - 1);
        if (button.hasAttribute("data-next")) showPage(page + 1);
        if (button.hasAttribute("data-pause")) {
            paused = !paused;
            button.textContent = paused ? "Resume" : "Pause";
            button.setAttribute("aria-pressed",String(paused));
        }
    });
    root.addEventListener("input", event => {
        if (event.target.matches("[data-search]")) { search = event.target.value; normalView(); }
    });
    document.addEventListener("keydown", event => { if (tv && event.key === "Escape") changeMode(); });
    const observer = new ResizeObserver(() => {
        cancelAnimationFrame(resizeFrame);
        if (tv) resizeFrame = requestAnimationFrame(paginate);
    });
    observer.observe(root);
    new ResizeObserver(() => { if (tv) paginate(); }).observe(one("[data-stale]"));
    root.addEventListener("raiden:before-update", () => { if (!tv) remembered = capture(); });
    root.addEventListener("raiden:updated", () => {
        if (tv) paginate(); else restore(remembered);
        if (!board.childElementCount) { remembered = null; pages = []; }
    });
    applyTheme(pref("theme") || "dark");
    normalView();
})();
