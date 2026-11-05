/* Presentation only. Reuse the operational shell's Board View and DUCC DOM. */
(() => {
    "use strict";
    const root = document.querySelector("[data-subzero-ucc]");
    if (!root) return;
    const desktop = window.matchMedia("(min-width:901px)");
    let frame = 0;
    function fit() {
        frame = 0;
        if (!desktop.matches || !document.body.classList.contains("operational-board-view")) {
            root.style.removeProperty("width");
            root.style.removeProperty("transform");
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
