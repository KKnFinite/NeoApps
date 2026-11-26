(() => {
    "use strict";
    const formats = new Map();
    let timer;
    function show(element, visible) {
        if (element.hidden === visible) element.hidden = !visible;
    }

    function update() {
        clearTimeout(timer);
        if (document.hidden) return;
        const now = Date.now();
        let delay = 60000 - now % 60000;
        for (const clock of document.querySelectorAll("[data-weather-clock]")) {
            const zone = clock.dataset.weatherClock;
            if (!formats.has(zone)) {
                try {
                    formats.set(zone, new Intl.DateTimeFormat("en-GB", {
                        timeZone: zone, hourCycle: "h23", hour: "2-digit", minute: "2-digit",
                    }));
                } catch (error) {
                    if (!(error instanceof RangeError)) throw error;
                    formats.set(zone, null);
                }
            }
            const text = formats.get(zone)?.format(now) ?? "";
            if (clock.textContent !== text) clock.textContent = text;
        }
        for (const weather of document.querySelectorAll("[data-observation-expires]")) {
            const stale = Number(weather.dataset.observationStale);
            const expires = Number(weather.dataset.observationExpires);
            const expired = now >= expires;
            for (const value of weather.querySelectorAll("[data-observation-value]")) show(value, !expired);
            show(weather.querySelector("[data-observation-unavailable]"), expired);
            show(weather.querySelector("[data-observation-old]"), now >= stale);
            for (const deadline of [stale, expires]) {
                if (deadline > now) delay = Math.min(delay, deadline - now);
            }
        }
        // Display-only: one local instant for both clocks and observation age.
        timer = setTimeout(update, delay);
    }
    document.addEventListener("weather:updated", update);
    document.addEventListener("visibilitychange", update);
    window.addEventListener("pageshow", update);
    update();
})();
