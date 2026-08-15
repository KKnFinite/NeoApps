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
        this.attributes = {};
    }

    closest(selector) {
        return selector === "[data-parking-lane]" ? this.lane : null;
    }

    replaceChildren(...children) {
        this.children = children;
    }

    querySelector() {
        return null;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }
}

class FakeForm extends FakeElement {
    constructor(documentRef) {
        super("form", documentRef);
        this.controls = new Map();
        [
            "operation_id",
            "ramp_code",
            "position_code",
            "lane_number",
            "replace_occupied",
            "parking_snapshot",
            "expected_source_location",
            "expected_source_version",
            "expected_target_tail",
            "expected_target_version",
            "tail_number",
        ].forEach((name) => this.controls.set(name, new FakeElement("input", documentRef)));
        this.label = new FakeElement("span", documentRef);
        this.submit = new FakeElement("button", documentRef);
    }

    querySelector(selector) {
        const nameMatch = selector.match(/^\[name='([^']+)'\]$/);
        if (nameMatch) return this.controls.get(nameMatch[1]) || null;
        if (selector === "[data-mobile-slot-editor-label]") return this.label;
        if (selector === "[data-mobile-slot-editor-submit]") return this.submit;
        return null;
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
        reusableApi: window.NeoParkingReusableControls,
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

test("reusable mobile editor reads current lane and snapshot data each time it opens", () => {
    const harness = createHarness({all_tails: [], unassigned_tails: []});
    const form = new FakeForm(harness.root.ownerDocument);
    const lane = new FakeElement("div", harness.root.ownerDocument);
    lane.dataset.rampCode = "A";
    lane.dataset.positionCode = "A01";
    lane.dataset.laneNumber = "1";
    lane.dataset.occupiedTail = "N100UP";
    lane.dataset.parkingSlotVersion = "slot-v1";

    harness.reusableApi.populateMobileEditor(
        form,
        lane,
        null,
        {location: "A01:1", version: "source-v1", operationId: "41"}
    );

    assert.equal(form.controls.get("operation_id").value, "41");
    assert.equal(form.controls.get("ramp_code").value, "A");
    assert.equal(form.controls.get("position_code").value, "A01");
    assert.equal(form.controls.get("lane_number").value, "1");
    assert.equal(form.controls.get("tail_number").value, "N100UP");
    assert.equal(form.controls.get("replace_occupied").value, "1");
    assert.equal(form.controls.get("expected_source_location").value, "A01:1");
    assert.equal(form.controls.get("expected_source_version").value, "source-v1");
    assert.equal(form.controls.get("expected_target_tail").value, "N100UP");
    assert.equal(form.controls.get("expected_target_version").value, "slot-v1");
    assert.equal(form.label.textContent, "Swap Tail");
    assert.equal(form.submit.textContent, "Swap");

    lane.dataset.rampCode = "B";
    lane.dataset.positionCode = "B02";
    lane.dataset.laneNumber = "2";
    lane.dataset.occupiedTail = "N200UP";
    lane.dataset.parkingSlotVersion = "slot-v2";
    harness.reusableApi.populateMobileEditor(
        form,
        lane,
        "N300UP",
        {location: "unassigned", version: "missing", operationId: "42"}
    );

    assert.equal(form.controls.get("operation_id").value, "42");
    assert.equal(form.controls.get("ramp_code").value, "B");
    assert.equal(form.controls.get("position_code").value, "B02");
    assert.equal(form.controls.get("lane_number").value, "2");
    assert.equal(form.controls.get("tail_number").value, "N300UP");
    assert.equal(form.controls.get("expected_source_location").value, "unassigned");
    assert.equal(form.controls.get("expected_source_version").value, "missing");
    assert.equal(form.controls.get("expected_target_tail").value, "N200UP");
    assert.equal(form.controls.get("expected_target_version").value, "slot-v2");
    assert.equal(form.attributes["aria-label"], "Swap Tail for B02 Slot 2");
});
