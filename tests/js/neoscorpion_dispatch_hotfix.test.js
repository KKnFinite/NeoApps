const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../app/static/js/neoscorpion_fuel_dispatch_live.js"), "utf8");

test("intentional sort preserves offsets without restoring the obsolete mission anchor", () => {
    const start = source.indexOf('root.addEventListener("click", (event) => {');
    const end = source.indexOf("async function saveSortPreferences", start);
    const saved = {rowKey: "1-1", y: 80, tableScrollTop: 120, tableScrollLeft: 30, openDetails: ["details-1"]};
    let click, restored, applied = 0, savedMode;
    const storage = new Map();
    const context = {dispatchSortMode: "arrival",
        root: {dataset: {sortPreferenceKey: "gateway.1.user.2"}, addEventListener: (_name, callback) => {click = callback;}},
        dispatchScroll: {snapshot: () => saved, restoreSnapshot: value => {restored = value;}},
        saveSortPreferences: () => {savedMode = context.dispatchSortMode;},
        applyDispatchSort: () => {applied++;}, window: {localStorage: {setItem: (key, value) => storage.set(key, value)}}};
    vm.runInNewContext(source.slice(start, end), context);
    click({target: {closest: () => ({dataset: {dispatchSortMode: "etd"}, hasAttribute: () => true})}});
    assert.equal(context.dispatchSortMode, "etd");
    assert.equal(restored.rowKey, null);
    assert.equal(saved.rowKey, "1-1");
    assert.equal(restored.tableScrollTop, 120);
    assert.equal(restored.tableScrollLeft, 30);
    assert.equal(restored.y, 80);
    assert.deepEqual(restored.openDetails, saved.openDetails);
    assert.equal(applied, 1);
    assert.equal(savedMode, "etd");
});

test("fallback DOM moves retain mission, history and DETAILS nodes without copying inputs", () => {
    const make = (group, primary, rank) => ({dataset: {dispatchSortGroup: group, dispatchEtdRank: rank},
        classList: {contains: () => false}, matches: () => primary});
    const history = make("a", false), a = make("a", true, "1"), aDetail = make("a", false);
    const pending = make("a", false);
    const b = make("b", true, "0"), bDetail = make("b", false);
    a.unsavedInput = {value: "draft"};
    const tbody = {children: [history, a, pending, aDetail, b, bDetail], appendChild(row) {
        this.children.splice(this.children.indexOf(row), 1); this.children.push(row);
    }};
    const start = source.indexOf("function applyDispatchSort(scope)");
    const end = source.indexOf("function initializeSpearSplash", start);
    const apply = vm.runInNewContext(`(${source.slice(start, end).trim()})`,
        {dispatchSortMode: "etd", arrivedFirst: false, renderSortControls: () => {}, document: {activeElement: null}});
    apply({querySelectorAll: () => [], querySelector: () => tbody});
    assert.deepEqual(tbody.children, [b, bDetail, history, a, pending, aDetail]);
    assert.equal(a.unsavedInput.value, "draft");
});
