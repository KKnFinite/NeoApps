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

    const content = (parent, selector) => parent?.querySelector(selector)?.textContent.trim() || "";
    function element(tag, className, ...children) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        node.append(...children.filter(child => child != null));
        return node;
    }
    const label = (value) => element("span", "ucc-tv-position", value);
    const heading = (value) => element("h2", "", value);

    function aircraft(source) {
        const iconSource = source.querySelector(".ucc-plane-icon");
        const direction = ["right", "left"].find(value => iconSource?.classList.contains(`facing-${value}`)) || "up";
        const state = Array.from(source.classList).find(value => value.startsWith("state-")) || "";
        const icon = element("span", `ucc-tv-plane-icon facing-${direction}`);
        icon.setAttribute("aria-hidden", "true");
        const route = [content(source, ".ucc-plane-flight"), source.dataset.uccTvDestination].filter(Boolean).join(" · ");
        const card = element("div", `ucc-tv-plane is-stacked ${state}`,
            element("strong", "", content(source, "strong")),
            icon,
            element("span", "ucc-tv-plane-route", route || "—"),
            element("span", "ucc-tv-plane-eta", `ETA ${source.dataset.uccTvEta || "—"}`));
        card.setAttribute("aria-label", source.getAttribute("aria-label") || "");
        return card;
    }

    function aircraftAt(source, position) {
        return element("div", "ucc-tv-stand",
            position ? label(position) : null,
            ...Array.from(source?.querySelectorAll(".ucc-plane") || [], aircraft));
    }

    function crew(ramp, position) {
        const summary = ramp?.querySelector(`.ucc-team.position-${position} .ucc-team-readonly`);
        const [driver, flyer] = summary?.querySelectorAll("span") || [];
        const number = content(summary, "strong").replace(new RegExp(`^${position} · | · ${position}$`, "g"), "");
        const unit = !number || number === "NO TRUCK" ? "NO TRUCK" : /^TRUCK\b/i.test(number) ? number : `TRUCK ${number}`;
        const right = position >= 3;
        const team = element("div", `ucc-tv-crew${right ? " is-right" : ""}`,
            element("strong", "", right ? `${unit} · ${position}` : `${position} · ${unit}`),
            element("span", "", driver?.textContent || "D · Unassigned"),
            element("span", "", flyer?.textContent || "F · Unassigned"));
        return team;
    }

    function rampView(name, source) {
        const pad = source?.querySelector("[data-ucc-deice-pad]");
        const padAircraft = pad?.querySelector(".ucc-plane");
        const padView = element("div", "ucc-tv-pad", padAircraft
            ? aircraft(padAircraft)
            : element("div", "ucc-tv-pad-idle", label("DEICE PAD"), element("span", "ucc-tv-plane-icon")));
        const stands = element("div", "ucc-tv-stands");
        for (let row = 1; row <= 4; row++) {
            const queue = source?.querySelector(`[data-ucc-queue-position="${row}"]`);
            const queueLabel = (row === 1 || row === 4) && queue?.querySelector(".ucc-plane")
                ? content(queue, ":scope > b") : "";
            const parking = (position) => {
                const code = `${name[0]}0${position}`;
                return aircraftAt(source?.querySelector(`[data-ucc-parking="${code}"]`), code);
            };
            stands.append(parking(row), aircraftAt(queue, queueLabel), parking(row + 4));
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
        const windows = Array.from(upcoming?.querySelectorAll("article") || [], window => {
            const [when, ...values] = window.querySelectorAll(":scope > span");
            return element("article", "", element("strong", "ucc-tv-hour-head", content(when, "b"), element("span", "", content(when, "small"))),
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
        const remoteCrews = [1, 2, 3, 4].filter(position => {
            const source = remote?.querySelector(`.ucc-team.position-${position} .ucc-team-readonly`);
            return source && (!content(source, "strong").includes("NO TRUCK")
                || Array.from(source.querySelectorAll("span")).some(role => !role.textContent.includes("Unassigned")));
        }).map(position => crew(remote, position));
        const remoteAircraft = Array.from(remote?.querySelectorAll(".ucc-parking-place:has(.ucc-plane)") || [],
            stand => aircraftAt(stand, stand.dataset.uccParking));
        const footer = element("footer", "ucc-tv-footer",
            element("section", "ucc-tv-remote", heading("Remote"),
                element("div", "ucc-tv-remote-data", ...remoteCrews, ...remoteAircraft,
                    !remoteCrews.length && !remoteAircraft.length ? element("p", "", "No current assignments or aircraft") : null)),
            element("section", "ucc-tv-early", heading("Early Out Crew"),
                element("p", "", "No assignment source in DUCC")));
        const exit = element("button", "ucc-tv-exit", "Exit TV Mode");
        exit.type = "button";
        exit.addEventListener("click", () => root.querySelector("[data-ucc-tv-exit]")?.click());
        const next = element("section", "ucc-tv-wallboard",
            element("header", "ucc-tv-header", element("h1", "", "RFD DUCC · TV MODE"), exit),
            weatherView(), element("div", "ucc-tv-ramps",
                ...["Alpha", "Bravo", "Charlie", "Delta", "Echo"].map(name => rampView(name, find(name)))),
            footer);
        if (wallboard) wallboard.replaceWith(next);
        else root.after(next);
        wallboard = next;
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
        const minimumStand = 108;
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
            tvSource = root;
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
