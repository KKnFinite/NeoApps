"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(
    path.join(__dirname, "..", "..", "app", "static", "js", "door_view_alert_clock.js"),
    "utf8"
);

const createHarness = () => {
    const documentListeners = new Map();
    const windowListeners = new Map();
    const timers = new Map();
    let nextTimerId = 1;
    let fetchCount = 0;

    const addListener = (listeners, name, callback) => {
        const callbacks = listeners.get(name) || [];
        callbacks.push(callback);
        listeners.set(name, callbacks);
    };
    const removeListener = (listeners, name, callback) => {
        const callbacks = listeners.get(name) || [];
        listeners.set(name, callbacks.filter((item) => item !== callback));
    };
    const document = {
        hidden: false,
        addEventListener(name, callback) {
            addListener(documentListeners, name, callback);
        },
        removeEventListener(name, callback) {
            removeListener(documentListeners, name, callback);
        },
        dispatch(name) {
            (documentListeners.get(name) || []).forEach((callback) => callback());
        },
    };
    const window = {
        document,
        clearTimeout(timerId) {
            timers.delete(timerId);
        },
        setTimeout(callback, delay) {
            const timerId = nextTimerId;
            nextTimerId += 1;
            timers.set(timerId, {callback, delay});
            return timerId;
        },
        addEventListener(name, callback) {
            addListener(windowListeners, name, callback);
        },
        removeEventListener(name, callback) {
            removeListener(windowListeners, name, callback);
        },
        dispatch(name) {
            (windowListeners.get(name) || []).forEach((callback) => callback());
        },
    };
    const sandbox = {
        console,
        Date,
        document,
        fetch() {
            fetchCount += 1;
            throw new Error("The alert clock must not make network requests.");
        },
        window,
    };
    vm.runInNewContext(source, sandbox, {filename: "door_view_alert_clock.js"});

    return {
        alertClock: window.NeoErmacDoorAlertClock,
        document,
        timers,
        window,
        get fetchCount() {
            return fetchCount;
        },
        runNextTimer() {
            const entry = Array.from(timers.entries())[0];
            assert.ok(entry, "Expected an alert transition timer.");
            const [timerId, timer] = entry;
            timers.delete(timerId);
            timer.callback();
            return timer.delay;
        },
    };
};

const timing = (overrides = {}) => ({
    accounted: false,
    window_start_epoch_ms: 1_000,
    due_soon_epoch_ms: 10_000,
    due_now_epoch_ms: 310_000,
    late_epoch_ms: 610_000,
    window_end_epoch_ms: 900_000,
    ...overrides,
});

test("pull alert thresholds preserve yellow, green, and red boundaries", () => {
    const {alertClock} = createHarness();
    const row = timing();

    assert.equal(alertClock.pullAlertState(row, 9_999).state, "");
    assert.equal(alertClock.pullAlertState(row, 10_000).state, "due_soon");
    assert.equal(alertClock.pullAlertState(row, 309_999).state, "due_soon");
    assert.equal(alertClock.pullAlertState(row, 310_000).state, "due_now");
    assert.equal(alertClock.pullAlertState(row, 609_999).state, "due_now");
    assert.equal(alertClock.pullAlertState(row, 610_000).state, "late");
    assert.equal(alertClock.pullAlertState(row, 900_000).state, "");
});

test("completed or no-pull timing clears every alert", () => {
    const {alertClock} = createHarness();

    assert.equal(
        alertClock.pullAlertState(timing({accounted: true}), 700_000).state,
        ""
    );
});

test("inactive door tabs ignore yellow and prefer red over green", () => {
    const {alertClock} = createHarness();
    const yellow = timing({due_now_epoch_ms: 310_000, late_epoch_ms: 610_000});
    const green = timing({due_now_epoch_ms: 100_000, late_epoch_ms: 400_000});
    const red = timing({due_now_epoch_ms: 50_000, late_epoch_ms: 150_000});

    assert.equal(alertClock.doorTabAlertState({pulls: [yellow]}, 200_000), "");
    assert.equal(alertClock.doorTabAlertState({pulls: [green]}, 200_000), "due_now");
    assert.equal(
        alertClock.doorTabAlertState({pulls: [green, red]}, 200_000),
        "late"
    );
});

test("absolute epoch metadata is independent of device timezone parsing", () => {
    const {alertClock} = createHarness();
    const epochStrings = timing({
        due_soon_epoch_ms: "1781247300000",
        due_now_epoch_ms: "1781247600000",
        late_epoch_ms: "1781247900000",
        window_start_epoch_ms: "1781233200000",
        window_end_epoch_ms: "1781254800000",
    });

    assert.equal(
        alertClock.pullAlertState(epochStrings, 1781247600000).state,
        "due_now"
    );
    assert.equal(
        alertClock.pullAlertState(epochStrings, 1781247900000).state,
        "late"
    );
});

test("clock crosses thresholds locally without any network request", () => {
    const harness = createHarness();
    let nowMs = 9_999;
    const states = [];
    const controller = harness.alertClock.create({
        documentObject: harness.document,
        windowObject: harness.window,
        getTimings: () => [timing()],
        now: () => nowMs,
        render: (currentNow) => {
            states.push(harness.alertClock.pullAlertState(timing(), currentNow).state);
        },
    });

    assert.deepEqual(states, [""]);
    assert.equal(harness.fetchCount, 0);
    nowMs = 10_010;
    harness.runNextTimer();
    assert.deepEqual(states, ["", "due_soon"]);
    assert.equal(harness.fetchCount, 0);

    harness.document.hidden = true;
    harness.document.dispatch("visibilitychange");
    assert.equal(harness.timers.size, 0);

    nowMs = 310_000;
    harness.document.hidden = false;
    harness.document.dispatch("visibilitychange");
    assert.equal(states.at(-1), "due_now");
    assert.equal(harness.fetchCount, 0);

    harness.window.dispatch("pagehide");
    assert.equal(harness.timers.size, 0);
    controller.refresh();
    assert.equal(states.at(-1), "due_now");
});
