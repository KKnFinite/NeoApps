"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const template = fs.readFileSync(
    path.join(
        __dirname,
        "..",
        "..",
        "app",
        "templates",
        "neomotherbrain",
        "_flight_api_auto_poll_timer.html"
    ),
    "utf8"
);
const source = template.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
const settle = () => new Promise((resolve) => setImmediate(resolve));

const createHarness = (initialStatus, responsePayload = null) => {
    const listeners = new Map();
    const timers = new Map();
    const storedValues = new Map();
    let nextTimerId = 1;
    let fetchCount = 0;

    const stateField = {textContent: ""};
    const detailField = {textContent: ""};
    const successField = {textContent: ""};
    const widget = {
        dataset: {
            endpoint: "/motherbrain/flight-api-auto-poll/check",
            clientHeader: "X-Neo-Flight-Api-Auto-Poll-Client",
            clientVersion: "test-version",
            initialStatus: JSON.stringify(initialStatus),
        },
        querySelector(selector) {
            return {
                "[data-flight-api-auto-poll-state]": stateField,
                "[data-flight-api-auto-poll-detail]": detailField,
                "[data-flight-api-auto-poll-success]": successField,
            }[selector] || null;
        },
    };
    const document = {
        hidden: false,
        visibilityState: "visible",
        addEventListener(name, callback) {
            const callbacks = listeners.get(name) || [];
            callbacks.push(callback);
            listeners.set(name, callbacks);
        },
        dispatch(name, event = {isTrusted: true}) {
            (listeners.get(name) || []).forEach((callback) => callback(event));
        },
        querySelector(selector) {
            return selector === "[data-flight-api-auto-poll-timer]" ? widget : null;
        },
    };
    const window = {
        NeoLiveUpdates: {inactivityTimeoutMs: 10 * 60 * 1000},
        clearTimeout(timerId) {
            timers.delete(timerId);
        },
        localStorage: {
            getItem(key) {
                return storedValues.get(key) || null;
            },
            setItem(key, value) {
                storedValues.set(key, String(value));
            },
        },
        setTimeout(callback, delay) {
            const timerId = nextTimerId;
            nextTimerId += 1;
            timers.set(timerId, {callback, delay});
            return timerId;
        },
    };
    class FakeHeaders {
        constructor(values = {}) {
            this.values = new Map(Object.entries(values));
        }

        set(name, value) {
            this.values.set(name, value);
        }
    }
    const payload = responsePayload || {
        ok: true,
        eligible: false,
        skipped: true,
        reason: "provider disabled",
        poll_action: "stop",
        terminal: true,
        continue_polling: false,
    };
    const sandbox = {
        Headers: FakeHeaders,
        Math: {random: () => 0, floor: Math.floor, max: Math.max, min: Math.min},
        console,
        document,
        fetch: async () => {
            fetchCount += 1;
            return {
                status: 200,
                json: async () => payload,
            };
        },
        window,
    };
    vm.runInNewContext(source, sandbox, {filename: "flight_api_auto_poll.js"});

    return {
        detailField,
        document,
        stateField,
        timers,
        fetchCount: () => fetchCount,
        hasTimer(delay) {
            return Array.from(timers.values()).some((timer) => timer.delay === delay);
        },
        async runTimer(delay) {
            const entry = Array.from(timers.entries()).find(([, timer]) => timer.delay === delay);
            assert.ok(entry, `Expected a ${delay}ms timer.`);
            const [timerId, timer] = entry;
            timers.delete(timerId);
            await timer.callback();
            await settle();
        },
    };
};

const waitingStatus = (seconds = 60) => ({
    eligible: false,
    reason: "waiting for auto poll interval",
    poll_action: "wait",
    terminal: false,
    continue_polling: true,
    next_check_seconds: seconds,
});

test("server terminal contract stops the page driver", () => {
    const harness = createHarness({
        eligible: false,
        reason: "provider disabled",
        poll_action: "stop",
        terminal: true,
        continue_polling: false,
    });

    assert.equal(harness.stateField.textContent, "STOPPED");
    assert.equal(harness.timers.size, 0);
    assert.equal(harness.fetchCount(), 0);
});

test("server wait contract schedules the next useful eligibility time", () => {
    const harness = createHarness(waitingStatus(15 * 60));

    assert.equal(harness.hasTimer(15 * 60 * 1000), true);
    assert.equal(harness.fetchCount(), 0);
});

test("visible inactivity stops checks and first activity reconciles once", async () => {
    const harness = createHarness(waitingStatus());

    assert.equal(harness.hasTimer(60 * 1000), true);
    assert.equal(harness.hasTimer(10 * 60 * 1000), true);
    await harness.runTimer(10 * 60 * 1000);

    assert.equal(harness.stateField.textContent, "PAUSED");
    assert.equal(harness.detailField.textContent, "AUTO POLL PAUSED - INACTIVE");
    assert.equal(harness.hasTimer(60 * 1000), false);
    assert.equal(harness.fetchCount(), 0);

    harness.document.dispatch("pointerdown", {isTrusted: true});
    await harness.runTimer(0);

    assert.equal(harness.fetchCount(), 1);
    assert.equal(harness.stateField.textContent, "STOPPED");
    assert.equal(harness.timers.size, 0);
});

test("hidden tabs clear timers and make no server request", () => {
    const harness = createHarness(waitingStatus());

    harness.document.hidden = true;
    harness.document.visibilityState = "hidden";
    harness.document.dispatch("visibilitychange", {isTrusted: true});

    assert.equal(harness.timers.size, 0);
    assert.equal(harness.fetchCount(), 0);
    assert.match(harness.detailField.textContent, /Page hidden/);
});
