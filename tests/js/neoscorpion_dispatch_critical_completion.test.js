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
    const action = {dataset:{completionIdentity:"visit-1"},cloneNode:() => ({copied:true})};
    const original = makeStack(null), incoming = makeStack(action);
    original.querySelector = selector => selector === '[data-dispatch-completion-slot]' ? {
        dataset:{completionIdentity:"visit-1"},replaceWith(node){original.insertBefore(node);}
    } : null;
    incoming.querySelector = selector => selector === '[data-dispatch-completion-slot]' ? action : null;
    const makeRow = stack => ({dataset:{dispatchMissionId:"45"}, querySelector(selector) {
        if (selector === ".neoscorpion-dispatch-action-stack") return stack;
        if (selector === '[data-neoscorpion-dispatch-details]') return null;
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

test('older, cross-sort and out-of-order panel responses are rejected',()=>{
    const start=source.indexOf('    let stateGeneration = 0;');
    const end=source.indexOf('    let tailNotesRevision',start);
    const context={operationId:'1',revision:10,tailRevision:'tail-a'};
    vm.runInNewContext(source.slice(start,end)+'\nthis.observe=observeLiveState;this.accept=currentResponse;this.generation=()=>stateGeneration;',context);
    const first=context.generation();assert.equal(context.accept({operation_id:1,revision:10},first),true);
    context.observe('1',11,'tail-b');assert.equal(context.accept({operation_id:1,revision:10},first),false);
    assert.equal(context.observe('1',9,'tail-a'),false);
    const newer=context.generation();assert.equal(context.accept({operation_id:1,revision:11},newer),true);
    context.observe('2',0,'tail-new-sort');assert.equal(context.accept({operation_id:1,revision:99},newer),false);
});
