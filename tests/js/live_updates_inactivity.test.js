"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(
    path.join(__dirname, "..", "..", "app", "static", "js", "live_updates.js"),
    "utf8"
);

const settle = () => new Promise((resolve) => setImmediate(resolve));

const createHarness = () => {
    const listeners = new Map();
    const timers = new Map();
    let nextTimerId = 1;

    class FakeElement {
        constructor(tagName = "div") {
            this.attributes = new Map();
            this.children = [];
            this.className = "";
            this.dataset = {};
            this.disabled = false;
            this.listeners = new Map();
            this.parentElement = null;
            this.parentNode = null;
            this.tagName = tagName.toUpperCase();
            this.textContent = "";
        }

        addEventListener(name, callback) {
            this.listeners.set(name, callback);
        }

        append(...children) {
            children.forEach((child) => this.appendChild(child));
        }

        appendChild(child) {
            if (child.parentElement) {
                child.parentElement.children = child.parentElement.children.filter(
                    (item) => item !== child
                );
            }
            child.parentElement = this;
            child.parentNode = this;
            this.children.push(child);
            return child;
        }

        dispatch(name) {
            this.listeners.get(name)?.({isTrusted: true});
        }

        insertBefore(child, reference) {
            const referenceIndex = this.children.indexOf(reference);
            child.parentElement = this;
            child.parentNode = this;
            this.children.splice(referenceIndex < 0 ? this.children.length : referenceIndex, 0, child);
            return child;
        }

        matches(selector) {
            return selector === "[data-live-update-controls]"
                && this.dataset.liveUpdateControls === "true";
        }

        querySelector(selector) {
            const dataKey = selector === "[data-live-update-label]"
                ? "liveUpdateLabel"
                : null;
            if (dataKey && this.dataset[dataKey] === "true") {
                return this;
            }
            for (const child of this.children) {
                const match = child.querySelector(selector);
                if (match) {
                    return match;
                }
            }
            return null;
        }

        setAttribute(name, value) {
            this.attributes.set(name, String(value));
        }
    }

    const document = {
        activeElement: null,
        hidden: false,
        readyState: "complete",
        addEventListener(name, callback) {
            const callbacks = listeners.get(name) || [];
            callbacks.push(callback);
            listeners.set(name, callbacks);
        },
        removeEventListener(name, callback) {
            const callbacks = listeners.get(name) || [];
            listeners.set(name, callbacks.filter((item) => item !== callback));
        },
        querySelectorAll() {
            return [];
        },
        querySelector() {
            return null;
        },
        createElement(tagName) {
            return new FakeElement(tagName);
        },
        dispatch(name, event = {isTrusted: true}) {
            (listeners.get(name) || []).forEach((callback) => callback(event));
        },
    };
    const window = {
        clearTimeout(timerId) {
            timers.delete(timerId);
        },
        setTimeout(callback, delay) {
            const timerId = nextTimerId;
            nextTimerId += 1;
            timers.set(timerId, {callback, delay});
            return timerId;
        },
    };
    const sandbox = {
        CSS: {escape: String},
        DOMParser: class DOMParser {},
        Element: FakeElement,
        HTMLFormElement: class HTMLFormElement {},
        HTMLInputElement: class HTMLInputElement {},
        console,
        document,
        window,
    };
    vm.runInNewContext(source, sandbox, {filename: "live_updates.js"});

    return {
        document,
        liveUpdates: window.NeoLiveUpdates,
        timers,
        createStatusElement() {
            const parent = new FakeElement("section");
            const status = new FakeElement("div");
            status.textContent = "Live updates on";
            parent.appendChild(status);
            return status;
        },
        hasTimer(delay) {
            return Array.from(timers.values()).some((timer) => timer.delay === delay);
        },
        timerIdForDelay(delay) {
            return Array.from(timers.entries()).find(([, timer]) => timer.delay === delay)?.[0];
        },
        runTimer(delay) {
            const entry = Array.from(timers.entries()).find(([, timer]) => timer.delay === delay);
            assert.ok(entry, `Expected a ${delay}ms timer.`);
            const [timerId, timer] = entry;
            timers.delete(timerId);
            timer.callback();
        },
    };
};

test("monitor control is tab-local and exposes a clear pressed state", () => {
    const harness = createHarness();
    const statusElement = harness.createStatusElement();
    const controller = harness.liveUpdates.create({
        immediate: false,
        intervalMs: 5000,
        poll: async () => {},
        statusElement,
    });

    controller.setServerStatus({auto_refresh_enabled: true});
    const controls = statusElement.parentElement;
    const monitorButton = controls.children[1];
    assert.equal(controls.dataset.liveUpdateControls, "true");
    assert.equal(monitorButton.dataset.liveMonitorMode, "true");
    assert.equal(monitorButton.attributes.get("aria-pressed"), "false");
    assert.equal(monitorButton.children[0].textContent, "KEEP LIVE / MONITOR MODE");
    assert.equal(monitorButton.children[1].textContent, "OFF");

    monitorButton.dispatch("click");
    assert.equal(monitorButton.attributes.get("aria-pressed"), "true");
    assert.equal(monitorButton.children[1].textContent, "ON");
    assert.equal(harness.hasTimer(600000), false);
});

test("foreground inactivity stops polling until one immediate reconciliation", async () => {
    const harness = createHarness();
    let polls = 0;
    let controller = null;
    controller = harness.liveUpdates.create({
        immediate: false,
        intervalMs: 5000,
        poll: async () => {
            polls += 1;
            controller.setServerStatus({auto_refresh_enabled: true});
        },
    });

    controller.setServerStatus({auto_refresh_enabled: true});
    assert.equal(harness.liveUpdates.inactivityTimeoutMs, 600000);
    assert.equal(harness.hasTimer(5000), true);
    assert.equal(harness.hasTimer(600000), true);
    const initialInactivityTimer = harness.timerIdForDelay(600000);

    harness.runTimer(5000);
    await settle();
    await settle();
    assert.equal(polls, 1);
    assert.equal(harness.timerIdForDelay(600000), initialInactivityTimer);

    harness.runTimer(600000);
    assert.equal(harness.hasTimer(5000), false);
    assert.equal(polls, 1);

    harness.document.dispatch("pointerdown");
    harness.document.dispatch("keydown");
    await settle();
    await settle();
    assert.equal(polls, 2);
    assert.equal(harness.hasTimer(5000), true);
    assert.equal(harness.hasTimer(600000), true);
});

test("monitor mode prevents foreground inactivity without overriding hidden pause", async () => {
    const harness = createHarness();
    let polls = 0;
    const controller = harness.liveUpdates.create({
        immediate: false,
        intervalMs: 7000,
        poll: async () => {
            polls += 1;
        },
    });

    controller.setServerStatus({auto_refresh_enabled: true});
    controller.setMonitorMode(true);
    assert.equal(harness.hasTimer(600000), false);
    assert.equal(harness.hasTimer(7000), true);

    harness.document.hidden = true;
    harness.document.dispatch("visibilitychange", {isTrusted: true});
    assert.equal(harness.hasTimer(7000), false);
    assert.equal(harness.hasTimer(600000), false);

    harness.document.hidden = false;
    harness.document.dispatch("visibilitychange", {isTrusted: true});
    await settle();
    await settle();
    assert.equal(polls, 1);
    assert.equal(harness.hasTimer(7000), true);
    assert.equal(harness.hasTimer(600000), false);
});

test("continuous visible mode has no monitor control or inactivity pause", async () => {
    const harness = createHarness();
    const statusElement = harness.createStatusElement();
    let polls = 0;
    const controller = harness.liveUpdates.create({
        continuousWhileVisible: true,
        immediate: false,
        intervalMs: 5000,
        poll: async () => {
            polls += 1;
        },
        statusElement,
    });

    controller.setServerStatus({auto_refresh_enabled: true});
    assert.equal(statusElement.parentElement.children.length, 1);
    assert.equal(harness.hasTimer(5000), true);
    assert.equal(harness.hasTimer(600000), false);

    harness.document.hidden = true;
    harness.document.dispatch("visibilitychange", {isTrusted: true});
    assert.equal(harness.hasTimer(5000), false);

    harness.document.hidden = false;
    harness.document.dispatch("visibilitychange", {isTrusted: true});
    await settle();
    await settle();
    assert.equal(polls, 1);
    assert.equal(harness.hasTimer(5000), true);
    assert.equal(harness.hasTimer(600000), false);
});

test("server-disabled pages stay stopped even when monitor mode is selected", async () => {
    const harness = createHarness();
    let polls = 0;
    const controller = harness.liveUpdates.create({
        intervalMs: 5000,
        poll: async () => {
            polls += 1;
        },
    });

    controller.setServerStatus({auto_refresh_enabled: false});
    controller.setMonitorMode(true);
    harness.document.dispatch("pointerdown");
    await controller.refreshNow();

    assert.equal(polls, 0);
    assert.equal(harness.timers.size, 0);
});

test("sub-five-second intervals are clamped and refresh requests never overlap", async () => {
    const harness = createHarness();
    let releasePoll = null;
    let polls = 0;
    const controller = harness.liveUpdates.create({
        immediate: false,
        intervalMs: 1000,
        poll: () => {
            polls += 1;
            return new Promise((resolve) => {
                releasePoll = resolve;
            });
        },
    });

    controller.setServerStatus({auto_refresh_enabled: true});
    assert.equal(harness.hasTimer(5000), true);
    const first = controller.refreshNow({force: true});
    const overlapping = await controller.refreshNow({force: true});
    await settle();

    assert.equal(overlapping, false);
    assert.equal(polls, 1);
    releasePoll();
    assert.equal(await first, true);
});
