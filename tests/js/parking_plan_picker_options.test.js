"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(
    path.join(__dirname, "..", "..", "app", "static", "js", "parking_plan_live.js"),
    "utf8"
);

class FakeElement {
    constructor(tagName, documentRef) {
        this.tagName = tagName.toUpperCase();
        this.ownerDocument = documentRef;
        this.children = [];
        this.dataset = {};
        this.disabled = false;
        this.placeholder = "";
        this.textContent = "";
        this.value = "";
        this.lane = null;
    }

    closest(selector) {
        return selector === "[data-parking-lane]" ? this.lane : null;
    }

    replaceChildren(...children) {
        this.children = children;
    }
}

const createHarness = (bootstrapState) => {
    const document = {
        createElement(tagName) {
            return new FakeElement(tagName, document);
        },
        querySelector() {
            return null;
        },
    };
    const bootstrap = new FakeElement("script", document);
    bootstrap.textContent = JSON.stringify(bootstrapState);
    const datalist = new FakeElement("datalist", document);
    const directInputs = [
        new FakeElement("input", document),
        new FakeElement("input", document),
    ];
    const directSelects = [
        new FakeElement("select", document),
        new FakeElement("select", document),
    ];
    const occupiedLane = new FakeElement("div", document);
    occupiedLane.dataset.occupiedTail = "N100UP";
    const emptyLane = new FakeElement("div", document);
    emptyLane.dataset.occupiedTail = "";
    const mobileSelects = [
        new FakeElement("select", document),
        new FakeElement("select", document),
    ];
    mobileSelects[0].lane = occupiedLane;
    mobileSelects[1].lane = emptyLane;
    const root = {
        ownerDocument: document,
        querySelector(selector) {
            return selector === "[data-parking-picker-bootstrap]" ? bootstrap : null;
        },
        querySelectorAll(selector) {
            return {
                "[data-direct-slot-options]": [datalist],
                "[data-direct-slot-input]": directInputs,
                "[data-direct-slot-select]": directSelects,
                "[data-mobile-slot-tail-picker] select[name='tail_number']": mobileSelects,
            }[selector] || [];
        },
    };
    const window = {document};
    vm.runInNewContext(source, {
        console,
        document,
        JSON,
        Set,
        window,
    }, {filename: "parking_plan_live.js"});
    return {
        api: window.NeoParkingPickerOptions,
        datalist,
        directInputs,
        directSelects,
        mobileSelects,
        root,
    };
};

const values = (element) => element.children.map((child) => child.value);

test("initial hydration shares options and preserves occupied and empty slots", () => {
    const harness = createHarness({
        all_tails: ["n100up", "N200UP", "N200UP"],
        unassigned_tails: ["N200UP"],
    });
    const state = harness.api.readBootstrap(harness.root);

    harness.api.hydrate(harness.root, state, {canEdit: true});

    assert.deepEqual(values(harness.datalist), ["N200UP"]);
    assert.deepEqual(values(harness.directSelects[0]), ["", "N200UP"]);
    assert.equal(harness.directInputs[0].disabled, false);
    assert.equal(harness.mobileSelects[0].value, "N100UP");
    assert.equal(harness.mobileSelects[1].value, "");
    assert.deepEqual(values(harness.mobileSelects[1]), ["", "N100UP", "N200UP"]);
});

test("live-state hydration replaces changed picker data without a second implementation", () => {
    const harness = createHarness({
        all_tails: ["N100UP", "N200UP"],
        unassigned_tails: ["N200UP"],
    });
    harness.api.hydrate(harness.root, harness.api.readBootstrap(harness.root));
    harness.directSelects[0].value = "N200UP";
    harness.mobileSelects[1].value = "N200UP";

    const changed = harness.api.fromLiveTails([
        {tail_number: "N100UP", source: {location: "unassigned"}},
        {tail_number: "N300UP", source: {location: "A01:1"}},
    ]);
    harness.api.hydrate(harness.root, changed, {canEdit: true});

    assert.deepEqual(values(harness.datalist), ["N100UP"]);
    assert.deepEqual(values(harness.directSelects[0]), ["", "N100UP"]);
    assert.equal(harness.directSelects[0].value, "");
    assert.deepEqual(values(harness.mobileSelects[1]), ["", "N100UP", "N300UP"]);
    assert.equal(harness.mobileSelects[1].value, "");
});

test("direct-slot matching remains normalized and rejects ambiguous partial tails", () => {
    const harness = createHarness({all_tails: [], unassigned_tails: []});
    const tails = ["N100UP", "N101UP", "N200UP"];

    assert.equal(harness.api.matchTail(tails, "n200up"), "N200UP");
    assert.equal(harness.api.matchTail(tails, " n 2 "), "N200UP");
    assert.equal(harness.api.matchTail(tails, "N10"), "");
    assert.equal(harness.api.matchTail(tails, ""), "");
});

test("all-assigned and read-only states keep unavailable controls disabled", () => {
    const harness = createHarness({
        all_tails: ["N100UP"],
        unassigned_tails: [],
    });

    harness.api.hydrate(
        harness.root,
        harness.api.readBootstrap(harness.root),
        {canEdit: false}
    );

    assert.deepEqual(values(harness.datalist), []);
    assert.equal(harness.directInputs[0].placeholder, "NO UNPARKED TAILS");
    assert.equal(harness.directInputs[0].disabled, true);
    assert.equal(harness.directSelects[0].disabled, true);
    assert.equal(harness.mobileSelects[0].disabled, true);
    assert.equal(harness.mobileSelects[0].value, "N100UP");
});
