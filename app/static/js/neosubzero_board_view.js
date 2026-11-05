/* Presentation only. Fit the existing DUCC TV/Board modes and shared DOM. */
(() => {
    "use strict";
    const root = document.querySelector("[data-subzero-ucc]");
    if (!root) return;
    const desktop = window.matchMedia("(min-width:901px)");
    let frame = 0;
    function alignRamps() {
        root.style.removeProperty("--ucc-staffing-row-height");
        root.style.removeProperty("--ucc-staffing-height");
        root.style.removeProperty("--ucc-parking-row-height");
        const main = '.ucc-ramp:not([data-ucc-ramp="Remote"])';
        const summaries = [...root.querySelectorAll(`${main} .ucc-team > summary`)];
        const stands = [...root.querySelectorAll(`${main} .ucc-parking-grid > div`)];
        root.style.setProperty("--ucc-staffing-row-height", `${Math.max(72, ...summaries.map(el => el.scrollHeight))}px`);
        const groups = [...root.querySelectorAll(`${main} .ucc-staffing-grid`)];
        root.style.setProperty("--ucc-staffing-height", `${Math.max(0, ...groups.map(el => el.scrollHeight))}px`);
        const minimumStand = document.body.classList.contains("neosubzero-ucc-tv-mode") ? 92 : 108;
        // scrollHeight rounds to whole CSS pixels; reserve the fractional line
        // box/border remainder so every ramp uses the exact same safe row size.
        root.style.setProperty("--ucc-parking-row-height", `${Math.ceil(Math.max(minimumStand, ...stands.map(el => el.scrollHeight))) + 2}px`);
    }
    function fit() {
        frame = 0;
        const board = document.body.classList.contains("operational-board-view");
        const tv = document.body.classList.contains("neosubzero-ucc-tv-mode");
        if (!desktop.matches) {
            root.style.removeProperty("width");
            root.style.removeProperty("transform");
            root.style.removeProperty("--ucc-staffing-row-height");
            root.style.removeProperty("--ucc-staffing-height");
            root.style.removeProperty("--ucc-parking-row-height");
            delete root.dataset.uccWeatherDensity;
            return;
        }
        root.style.transform = "none";
        if (!board && !tv) {
            root.style.removeProperty("width");
            delete root.dataset.uccWeatherDensity;
            alignRamps();
            return;
        }
        // Keep all five ramps, optional Remote, weather, staffing and warnings.
        // Measure after compact Board CSS, including opened details and drafts.
        const availableWidth = Math.max(1, window.innerWidth - 16);
        const availableHeight = Math.max(1, window.innerHeight - (tv ? 36 : 48));
        if (tv) {
            root.style.width = `${availableWidth}px`;
            root.dataset.uccWeatherDensity = "normal";
            alignRamps();
            let height = root.scrollHeight;
            for (const density of ["compact", "minimal"]) {
                if (height <= availableHeight) break;
                root.dataset.uccWeatherDensity = density;
                height = root.scrollHeight;
            }
            root.style.transform = `scale(${Math.min(1, availableHeight / Math.max(1, height))})`;
            return;
        }
        delete root.dataset.uccWeatherDensity;
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
    // Board entry exits TV presentation through its existing handler. Leaving
    // Board then restores normal navigation, never an overlapping TV mode.
    document.addEventListener("neo:board-enter", () => {
        if (document.body.classList.contains("neosubzero-ucc-tv-mode")) {
            root.querySelector("[data-ucc-tv-exit]")?.click();
        }
        schedule();
    });
    new MutationObserver(schedule).observe(document.body, {attributes:true, attributeFilter:["class"]});
    new MutationObserver(schedule).observe(root, {
        subtree:true, childList:true, characterData:true,
        attributes:true, attributeFilter:["open", "hidden"],
    });
    window.addEventListener("resize", schedule);
    desktop.addEventListener("change", schedule);
    if (document.fonts) document.fonts.ready.then(schedule);
    schedule();
})();
