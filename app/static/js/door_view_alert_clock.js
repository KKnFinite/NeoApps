(() => {
    "use strict";

    if (window.NeoErmacDoorAlertClock) {
        return;
    }

    const EMPTY_ALERT = Object.freeze({
        state: "",
        css_class: "",
        label: "",
        minutes: null,
    });

    const epochValue = (value) => {
        const parsed = Number(value);
        return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
    };

    const pullAlertState = (timing, nowMs = Date.now()) => {
        const row = timing || {};
        const now = Number(nowMs);
        const windowStart = epochValue(row.window_start_epoch_ms);
        const windowEnd = epochValue(row.window_end_epoch_ms);
        const dueSoon = epochValue(row.due_soon_epoch_ms);
        const dueNow = epochValue(row.due_now_epoch_ms);
        const late = epochValue(row.late_epoch_ms);

        if (
            row.accounted === true
            || !Number.isFinite(now)
            || !dueNow
            || (windowStart && now < windowStart)
            || (windowEnd && now >= windowEnd)
        ) {
            return EMPTY_ALERT;
        }
        if (late && now >= late) {
            return {
                state: "late",
                css_class: "is-pull-late",
                label: "LATE",
                minutes: Math.floor((now - dueNow) / 60000),
            };
        }
        if (now >= dueNow) {
            return {
                state: "due_now",
                css_class: "is-pull-due-now",
                label: "PULL NOW",
                minutes: Math.floor((now - dueNow) / 60000),
            };
        }
        if (dueSoon && now >= dueSoon) {
            return {
                state: "due_soon",
                css_class: "is-pull-due-soon",
                label: "DUE SOON",
                minutes: Math.floor((dueNow - now) / 60000),
            };
        }
        return EMPTY_ALERT;
    };

    const doorTabAlertState = (doorAlert, nowMs = Date.now()) => {
        const alert = doorAlert || {};
        if (!Array.isArray(alert.pulls)) {
            return alert.state === "late"
                ? "late"
                : (alert.state === "due_now" ? "due_now" : "");
        }

        let hasDueNow = false;
        for (const pull of alert.pulls) {
            const state = pullAlertState(pull, nowMs).state;
            if (state === "late") {
                return "late";
            }
            if (state === "due_now") {
                hasDueNow = true;
            }
        }
        return hasDueNow ? "due_now" : "";
    };

    const nextTransitionEpoch = (timing, nowMs = Date.now()) => {
        const row = timing || {};
        if (row.accounted === true) {
            return null;
        }
        const now = Number(nowMs);
        const transitions = [
            row.window_start_epoch_ms,
            row.due_soon_epoch_ms,
            row.due_now_epoch_ms,
            row.late_epoch_ms,
            row.window_end_epoch_ms,
        ]
            .map(epochValue)
            .filter((value) => value && value > now);
        return transitions.length ? Math.min(...transitions) : null;
    };

    const create = (options = {}) => {
        const getTimings = options.getTimings || (() => []);
        const render = options.render || (() => {});
        const now = options.now || (() => Date.now());
        const documentObject = options.documentObject || window.document;
        const windowObject = options.windowObject || window;
        const setTimer = options.setTimeoutFn || windowObject.setTimeout.bind(windowObject);
        const clearTimer = options.clearTimeoutFn || windowObject.clearTimeout.bind(windowObject);
        let timerId = null;
        let destroyed = false;

        const clearScheduled = () => {
            if (timerId !== null) {
                clearTimer(timerId);
                timerId = null;
            }
        };

        const schedule = (nowMs) => {
            clearScheduled();
            if (destroyed || documentObject?.hidden) {
                return;
            }
            const transitions = getTimings()
                .map((timing) => nextTransitionEpoch(timing, nowMs))
                .filter((value) => value !== null);
            if (!transitions.length) {
                return;
            }
            const nextTransition = Math.min(...transitions);
            const delay = Math.max(1, nextTransition - nowMs + 10);
            timerId = setTimer(refresh, delay);
        };

        const refresh = () => {
            if (destroyed) {
                return;
            }
            const nowMs = Number(now());
            render(nowMs);
            schedule(nowMs);
        };

        const handleVisibility = () => {
            if (documentObject?.hidden) {
                clearScheduled();
                return;
            }
            refresh();
        };

        const destroy = () => {
            if (destroyed) {
                return;
            }
            destroyed = true;
            clearScheduled();
            documentObject?.removeEventListener?.("visibilitychange", handleVisibility);
            windowObject?.removeEventListener?.("pagehide", destroy);
        };

        documentObject?.addEventListener?.("visibilitychange", handleVisibility);
        windowObject?.addEventListener?.("pagehide", destroy);
        refresh();

        return Object.freeze({
            destroy,
            refresh,
        });
    };

    window.NeoErmacDoorAlertClock = Object.freeze({
        create,
        doorTabAlertState,
        nextTransitionEpoch,
        pullAlertState,
    });
})();
