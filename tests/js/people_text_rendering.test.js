"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const root = path.join(__dirname, "..", "..");

// Minimal DOM seam, as in the existing live-updates tests. Only the old fixed
// picker shell is allowed; data-bearing HTML fails. This also reproduces the
// vulnerable baseline rather than failing on its unrelated static summary.
class Element {
    constructor(tagName = "div") {
        this.tagName = tagName;
        this.children = [];
        this.dataset = {};
        this.listeners = {};
        this.textContent = "";
    }
    set innerHTML(value) {
        if (value === "") { this.children = []; return; }
        if (value === '<summary data-people-destination-label>Select Work Area</summary>') {
            const label = new Element("summary");
            label.dataset.peopleDestinationLabel = "";
            label.textContent = "Select Work Area";
            this.children = [label];
            return;
        }
        throw new Error("Unexpected data-bearing HTML parsing");
    }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    addEventListener(type, listener) { this.listeners[type] = listener; }
    querySelectorAll() { return []; }
    querySelector(selector) {
        return this.children.find((child) => selector === "[data-people-destination-label]"
            && Object.hasOwn(child.dataset, "peopleDestinationLabel")) || null;
    }
    after(node) { this.following = node; }
}

function documentStub() {
    return {
        readyState: "complete",
        addEventListener() {},
        querySelectorAll() { return []; },
        querySelector() { return null; },
        createElement: (tag) => new Element(tag),
    };
}

function pickerFor(label) {
    const destination = new Element("select");
    destination.options = [{value: ""}, {value: "17", textContent: label}];
    destination.selectedOptions = [destination.options[1]];
    const document = documentStub();
    document.querySelector = (selector) => {
        if (selector === ".neostaffing-people-console") return new Element();
        if (selector === '#people-selection-form select[name="work_area_unit_id"]') return destination;
        return null;
    };
    vm.runInNewContext(fs.readFileSync(path.join(root, "app/static/js/staffing_people.js"), "utf8"), {
        document, window: {}, sessionStorage: {getItem: () => null},
    });
    return destination;
}

function descendants(node) {
    return [node, ...node.children.flatMap(descendants)];
}

test("work-area group labels are inert text and selection keeps the real option value", () => {
    const injected = '<img src=x onerror="alert(1)">';
    const destination = pickerFor(`${injected} / Work Area`);
    const picker = destination.following;
    const nodes = descendants(picker);
    assert.ok(nodes.some((node) => node.tagName === "summary" && node.textContent === injected));
    assert.equal(nodes.some((node) => node.tagName === "img"), false);
    const button = nodes.find((node) => node.tagName === "button");
    button.listeners.click();
    assert.equal(destination.value, "17");
    assert.equal(picker.children[0].textContent, `${injected} / Work Area`);
    assert.equal(picker.open, false);
});

test("prototype-property names are ordinary hierarchy keys, not inherited objects", () => {
    const destination = pickerFor("__proto__ / constructor / Work Area");
    const labels = descendants(destination.following).map((node) => node.textContent);
    assert.ok(labels.includes("__proto__"));
    assert.ok(labels.includes("constructor"));
    assert.ok(labels.includes("Work Area"));
});

function bulkPreview() {
    const elements = Object.fromEntries(["input", "preview", "errors", "submit"].map(
        (name) => [name, new Element()]
    ));
    elements.input.value = "";
    const form = {querySelector: (selector) => elements[selector.match(/bulk-(\w+)/)[1]]};
    const document = documentStub();
    document.querySelectorAll = (selector) => selector === ".neostaffing-people-bulk-create-form" ? [form] : [];
    const template = fs.readFileSync(path.join(root, "app/templates/neostaffing/people.html"), "utf8");
    const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];
    vm.runInNewContext(script, {document});
    return elements;
}

test("bulk preview renders pasted HTML literally and preserves all seven columns", () => {
    const elements = bulkPreview();
    const cells = ['100', '<svg onload="alert(1)">', 'A & B', '01/02/2020', '555', 'Worker', 'Active'];
    elements.input.value = cells.join("\t");
    elements.input.oninput();
    const row = elements.preview.children[0];
    assert.equal(row.children[0].tagName, "th");
    assert.equal(row.children[0].textContent, "1");
    assert.deepEqual(row.children.slice(1).map((cell) => cell.textContent), cells);
    assert.ok(row.children.slice(1).every((cell) => cell.tagName === "td"));
    assert.equal(elements.submit.disabled, false);
    assert.equal(elements.submit.textContent, "ADD 1 EMPLOYEE");
});

test("bulk preview preserves invalid-row feedback and clears stale rows", () => {
    const elements = bulkPreview();
    elements.input.value = "Only\tTwo";
    elements.input.oninput();
    assert.equal(elements.preview.children[0].className, "is-invalid");
    assert.equal(elements.submit.disabled, true);
    assert.equal(elements.errors.textContent, "Each row needs 7 tab-separated fields.");
    elements.input.value = "";
    elements.input.oninput();
    assert.equal(elements.preview.children.length, 0);
    assert.equal(elements.submit.disabled, true);
    assert.equal(elements.submit.textContent, "ADD 0 EMPLOYEES");
});
