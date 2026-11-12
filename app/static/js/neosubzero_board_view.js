/* Read-only TV projection and existing interactive Board View fitting. */
(() => {
    "use strict";
    const root = document.querySelector("[data-subzero-ucc]");
    if (!root) return;
    const desktop = window.matchMedia("(min-width:901px)");
    let frame = 0;
    let wallboard;
    let tvDirty = true;
    let tvSource = root;
    const refreshStatus = root.querySelector("[data-ucc-tv-refresh-status]");
    const themeKey = "neoapps.neosubzero.ucc.tv-theme";
    let tvTheme = "dark";
    try {
        if (window.localStorage.getItem(themeKey) === "light") tvTheme = "light";
    } catch (_error) {
        // Keep the current page's preference if browser storage is blocked.
    }

    const content = (parent, selector) => parent?.querySelector(selector)?.textContent.trim() || "";
    function element(tag, className, ...children) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        node.append(...children.filter(child => child != null));
        return node;
    }
    const label = (value) => element("span", "ucc-tv-position", value);
    const heading = (value) => element("h2", "", value);

    function aircraft(source, inThroat = false) {
        const iconSource = source.querySelector(".ucc-plane-icon");
        const direction = ["right", "left"].find(value => iconSource?.classList.contains(`facing-${value}`)) || "up";
        const state = Array.from(source.classList).find(value => value.startsWith("state-")) || "";
        const icon = element("span", `ucc-tv-plane-icon facing-${direction}`);
        icon.setAttribute("aria-hidden", "true");
        const route = [content(source, ".ucc-plane-flight"), source.dataset.uccTvDestination].filter(Boolean).join(" · ");
        const etd = source.dataset.uccTvEtd;
        const card = element("div", `ucc-tv-plane is-stacked ${state}${inThroat ? " is-in-throat" : ""}`,
            element("strong", "", content(source, "strong")),
            icon,
            element("span", "ucc-tv-plane-route", route || "—"),
            !inThroat && etd !== undefined ? element("span", "ucc-tv-plane-etd", `ETD ${etd || "—"}`) : null);
        card.setAttribute("aria-label", source.getAttribute("aria-label") || "");
        return card;
    }

    function aircraftAt(source, position, inThroat = false) {
        return element("div", "ucc-tv-stand",
            position ? label(position) : null,
            ...Array.from(source?.querySelectorAll(".ucc-plane") || [], plane => aircraft(plane, inThroat)));
    }

    function crew(ramp, position) {
        const summary = ramp?.querySelector(`.ucc-team.position-${position} .ucc-team-readonly`);
        const assigned = Array.from(summary?.querySelectorAll("[data-ucc-tv-crew-field]") || []);
        if (!assigned.length) return null;
        const right = position >= 3;
        const team = element("div", `ucc-tv-crew${right ? " is-right" : ""}`, ...assigned.map(field => {
            let value = field.textContent.trim();
            if (field.dataset.uccTvCrewField === "truck") {
                const number = value.replace(new RegExp(`^${position} · | · ${position}$`, "g"), "");
                const truck = /^TRUCK\b/i.test(number) ? number : `TRUCK ${number}`;
                value = right ? `${truck} · ${position}` : `${position} · ${truck}`;
            }
            const line = element(field.localName, field.className, value);
            line.title = value;
            line.dataset.uccTvCrewField = field.dataset.uccTvCrewField;
            return line;
        }));
        team.dataset.uccTvPosition = position;
        return team;
    }

    function rampView(name, source) {
        const pad = source?.querySelector("[data-ucc-deice-pad]");
        const padAircraft = pad?.querySelector(".ucc-plane");
        const padReview = content(pad, "[data-ucc-pad-review]");
        const padView = element("div", "ucc-tv-pad", padAircraft
            ? aircraft(padAircraft, true)
            : element("div", "ucc-tv-pad-idle", label(padReview || "DEICE PAD"),
                padReview ? null : element("span", "ucc-tv-plane-icon")));
        const stands = element("div", "ucc-tv-stands");
        for (let row = 1; row <= 4; row++) {
            const queue = source?.querySelector(`[data-ucc-queue-position="${row}"]`);
            const queueLabel = (row === 1 || row === 4) && queue?.querySelector(".ucc-plane")
                ? content(queue, ":scope > b") : "";
            const parking = (column) => {
                const stand = source?.querySelector(`.ucc-grid-row-${row}.ucc-grid-column-${column}[data-ucc-parking]`);
                return aircraftAt(stand, stand?.dataset.uccParking);
            };
            stands.append(parking(1), aircraftAt(queue, queueLabel, true), parking(3));
        }
        return element("section", "ucc-tv-ramp", heading(name),
            element("div", "ucc-tv-staging",
                element("div", "ucc-tv-crew-column", crew(source, 1), crew(source, 2)),
                padView, element("div", "ucc-tv-crew-column", crew(source, 4), crew(source, 3))),
            stands);
    }

    function weatherView() {
        const weather = tvSource.querySelector(".neosubzero-weather");
        const current = weather?.querySelector(".neosubzero-weather-current");
        const primary = current?.querySelector(".neosubzero-weather-primary");
        const facts = Array.from(current?.querySelectorAll("dl > div") || [], fact =>
            element("div", "", element("dt", "", content(fact, "dt")), element("dd", "", content(fact, "dd"))));
        const observation = element("section", "ucc-tv-observation", heading("KRFD · CURRENT WEATHER"),
            element("div", "ucc-tv-temperature", element("strong", "", content(primary, "strong") || "—"),
                element("div", "", element("b", "", content(primary, "span")), element("small", "", content(primary, "small")))),
            element("dl", "", ...facts),
            element("p", "ucc-tv-source", content(weather, ".neosubzero-weather-source > span")),
            content(weather, ":scope > .neosubzero-weather-note"));

        const hours = Array.from(weather?.querySelectorAll(".neosubzero-weather-current-sort > article") || [], hour => {
            const priority = hour.querySelectorAll(".neosubzero-weather-hour-priority > span");
            const secondary = hour.querySelectorAll(".neosubzero-weather-hour-secondary > span");
            return element("article", "", element("strong", "ucc-tv-hour-head",
                content(hour, "time"), element("span", "", content(priority[1], "b"))),
                element("span", "", [content(priority[0], "b"), content(priority[0], "em") || secondary[4]?.textContent].filter(Boolean).join(" · ")),
                element("span", "ucc-tv-muted", Array.from(secondary).slice(0, 3).map(part => part.textContent).join(" · ")),
                element("span", "ucc-tv-muted", secondary[3]?.textContent || ""));
        });
        const hourly = element("section", "ucc-tv-hourly", heading("CURRENT SORT · HOURLY"),
            element("p", "ucc-tv-source", content(weather, ".neosubzero-weather-forecast-head > span")));
        const hourGrid = element("div", "ucc-tv-hours", ...hours);
        hourGrid.style.setProperty("--ucc-tv-hour-columns", Math.max(1, Math.ceil(hours.length / 2)));
        hourly.append(hours.length ? hourGrid : element("p", "", content(weather, ".neosubzero-weather-current-sort .neosubzero-weather-note") || "Hourly forecast unavailable"));

        const upcoming = tvSource.querySelector("[data-ucc-upcoming]");
        const upcomingRows = upcoming?.querySelectorAll("[data-ucc-tv-window]") || [];
        const extraRows = upcoming?.querySelector("[data-ucc-tv-forecast-extra]")?.content.querySelectorAll("article") || [];
        const windows = [...upcomingRows, ...extraRows].slice(0, 4).map(window => {
            const [when, ...values] = window.querySelectorAll(":scope > span");
            const title = element("strong", "ucc-tv-hour-head", content(when, "b"), element("span", "", content(when, "small")));
            if (window.hasAttribute("data-ucc-forecast-unavailable")) {
                return element("article", "", title, element("span", "ucc-tv-muted", "Forecast unavailable"));
            }
            return element("article", "", title,
                element("span", "ucc-tv-muted", values.map(value => `${content(value, "small")} ${content(value, "b")}`).join(" · ")),
                element("span", "", content(window, "em")));
        });
        const future = element("section", "ucc-tv-future", heading("UPCOMING SORT WINDOWS"),
            element("p", "ucc-tv-source", content(upcoming, ".neosubzero-weather-forecast-head > span")),
            windows.length ? element("div", "ucc-tv-windows", ...windows)
                : element("p", "", content(upcoming, ".neosubzero-weather-note") || "Upcoming forecast unavailable"));
        return element("div", "ucc-tv-weather", observation, hourly, future);
    }

    function renderTv() {
        if (wallboard && !tvDirty) return;
        tvDirty = false;
        const ramps = Array.from(tvSource.querySelectorAll("[data-ucc-ramp]"));
        const find = (name) => ramps.find(ramp => ramp.dataset.uccRamp === name);
        const remote = find("Remote");
        const staffedCrews = section => [1, 2, 3, 4].map(position => crew(section, position)).filter(Boolean);
        const remoteCrews = staffedCrews(remote);
        const earlyCrews = staffedCrews(tvSource.querySelector("[data-ucc-early-out]"));
        const remoteAircraft = Array.from(remote?.querySelectorAll(".ucc-parking-place:has(.ucc-plane)") || [],
            stand => aircraftAt(stand, stand.dataset.uccParking));
        const footer = element("footer", "ucc-tv-footer",
            element("section", "ucc-tv-remote", heading("Remote"),
                element("div", "ucc-tv-remote-data", ...remoteCrews, ...remoteAircraft)),
            element("section", "ucc-tv-early", heading("Early Out Crew"),
                element("div", "ucc-tv-remote-data", ...earlyCrews)));
        const exit = element("button", "ucc-tv-exit", "Exit TV Mode");
        exit.type = "button";
        exit.addEventListener("click", () => root.querySelector("[data-ucc-tv-exit]")?.click());
        const theme = element("button", "ucc-tv-theme",
            element("span", "ucc-tv-theme-light", "Light"), " / ",
            element("span", "ucc-tv-theme-dark", "Dark"));
        theme.type = "button";
        theme.setAttribute("aria-label", "Light appearance");
        const next = element("section", "ucc-tv-wallboard",
            element("header", "ucc-tv-header", refreshStatus, element("h1", "", "RFD DUCC · TV MODE"), theme, exit),
            weatherView(), element("div", "ucc-tv-ramps",
                ...["Alpha", "Bravo", "Charlie", "Delta", "Echo"].map(name => rampView(name, find(name)))),
            footer);
        const applyTheme = () => {
            next.dataset.uccTvTheme = tvTheme;
            theme.setAttribute("aria-pressed", String(tvTheme === "light"));
        };
        theme.addEventListener("click", () => {
            tvTheme = tvTheme === "light" ? "dark" : "light";
            applyTheme();
            try { window.localStorage.setItem(themeKey, tvTheme); } catch (_error) { /* page preference remains */ }
        });
        applyTheme();
        const restoreThemeFocus = wallboard?.querySelector(".ucc-tv-theme") === document.activeElement;
        if (wallboard) wallboard.replaceWith(next);
        else root.after(next);
        wallboard = next;
        if (restoreThemeFocus) theme.focus({preventScroll:true});
    }

    function alignRamps() {
        root.style.removeProperty("--ucc-staffing-row-height");
        root.style.removeProperty("--ucc-staffing-height");
        root.style.removeProperty("--ucc-parking-row-height");
        const main = '.ucc-ramp:not([data-ucc-ramp="Remote"])';
        const summaries = [...root.querySelectorAll(`${main} .ucc-team`)];
        const stands = [...root.querySelectorAll(`${main} .ucc-parking-grid > div`)];
        root.style.setProperty("--ucc-staffing-row-height", `${Math.max(72, ...summaries.map(el => el.scrollHeight))}px`);
        const groups = [...root.querySelectorAll(`${main} .ucc-staffing-grid`)];
        root.style.setProperty("--ucc-staffing-height", `${Math.max(0, ...groups.map(el => el.scrollHeight))}px`);
        const minimumStand = document.body.classList.contains("operational-board-view") ? 108 : 36;
        // scrollHeight rounds to whole CSS pixels; reserve the fractional line
        // box/border remainder so every ramp uses the exact same safe row size.
        root.style.setProperty("--ucc-parking-row-height", `${Math.ceil(Math.max(minimumStand, ...stands.map(el => el.scrollHeight))) + 2}px`);
    }
    function fit() {
        frame = 0;
        const board = document.body.classList.contains("operational-board-view");
        const tv = document.body.classList.contains("neosubzero-ucc-tv-mode");
        if (tv && desktop.matches) {
            renderTv();
            return;
        }
        if (wallboard) {
            wallboard.remove();
            wallboard = null;
        }
        if (!desktop.matches) {
            root.style.removeProperty("width");
            root.style.removeProperty("transform");
            root.style.removeProperty("--ucc-staffing-row-height");
            root.style.removeProperty("--ucc-staffing-height");
            root.style.removeProperty("--ucc-parking-row-height");
            return;
        }
        root.style.transform = "none";
        if (!board) {
            root.style.removeProperty("width");
            alignRamps();
            return;
        }
        // Keep all five ramps, optional Remote, weather, staffing and warnings.
        // Measure after compact Board CSS, including opened details and drafts.
        const availableWidth = Math.max(1, window.innerWidth - 16);
        const availableHeight = Math.max(1, window.innerHeight - 48);
        let width = Math.max(1500, availableWidth);
        root.style.transform = "none";
        let height;
        // Widen the virtual canvas when height-limited. This lets responsive
        // weather/staffing grids use spare horizontal space before scaling,
        // rather than leaving a narrow board beside a large empty gutter.
        for (let pass = 0; pass < 5; pass++) {
            root.style.width = `${width}px`;
            alignRamps();
            height = Math.max(root.scrollHeight, root.getBoundingClientRect().height, 1);
            const ratio = height * availableWidth / width / availableHeight;
            if (ratio <= 1.01 || pass === 4) break;
            width *= Math.min(1.5, ratio);
        }
        const scale = Math.min(1, availableWidth / width, availableHeight / height);
        root.style.transform = `scale(${scale})`;
    }
    function schedule() {
        if (!frame) frame = window.requestAnimationFrame(fit);
    }
    root.addEventListener("ucc:tv-source", event => {
        tvSource = event.detail || root;
        tvDirty = true;
        schedule();
    });
    // Board entry exits TV presentation through its existing handler. Leaving
    // Board then restores normal navigation, never an overlapping TV mode.
    document.addEventListener("neo:board-enter", () => {
        if (document.body.classList.contains("neosubzero-ucc-tv-mode")) {
            root.querySelector("[data-ucc-tv-exit]")?.click();
        }
        schedule();
    });
    new MutationObserver(schedule).observe(document.body, {attributes:true, attributeFilter:["class"]});
    new MutationObserver(() => { tvDirty = true; schedule(); }).observe(root, {
        subtree:true, childList:true, characterData:true,
        attributes:true, attributeFilter:["open", "hidden"],
    });
    window.addEventListener("resize", schedule);
    desktop.addEventListener("change", schedule);
    if (document.fonts) document.fonts.ready.then(schedule);
    schedule();
})();
