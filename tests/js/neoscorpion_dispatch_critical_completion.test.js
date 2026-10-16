const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../app/static/js/neoscorpion_fuel_dispatch_live.js"), "utf8");
const expression = source.slice(source.indexOf("    const applyCriticalCompletion = (nextPanel) => {"),
    source.indexOf("    const refreshCriticalCompletion = async", source.indexOf("    const applyCriticalCompletion = (nextPanel) => {")));
test("critical completion appears under a dirty Dispatch without replacing editable controls", () => {
    const makeStack = (initial) => {
        let button = initial;
        return {firstChild: null, querySelector(selector) {
            if (selector === '[data-dispatch-tail-change-warning]') return null;
            return button;
        },insertBefore(node) { button = node; },read() { return button; }};
    };
    const action = {formaction:"/neoscorpion/fuel-dispatch/complete",cloneNode:() => ({copied:true})};
    const original = makeStack(null), incoming = makeStack(action);
    const makeRow = stack => ({dataset:{dispatchMissionId:"45"}, querySelector(selector) {
        if (selector === ".neoscorpion-dispatch-action-stack") return stack;
        return {textContent:"N168UP"};
    }});
    const actual = makeRow(original), next = makeRow(incoming);
    const context = {root: {querySelectorAll: () => [actual],querySelector: () => null},
        document: {createElement: () => ({setAttribute(){}})}};
    vm.runInNewContext(expression + "\nthis.runPatch = applyCriticalCompletion;", context);
    context.runPatch({querySelectorAll:()=>[next],querySelector:()=>null});
    assert.equal(original.read().copied,true);
    assert.equal(actual.dataset.dispatchMissionId,"45");
});
