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
    const context = {updateAssignmentAlerts(){}, root: {querySelectorAll: s => s.includes('data-dispatch-mission-id') ? [actual] : [],querySelector: () => null},
        document: {createElement: () => ({setAttribute(){}})}};
    vm.runInNewContext(expression + "\nthis.runPatch = applyCriticalCompletion;", context);
    context.runPatch({querySelectorAll:s=>s.includes('data-dispatch-mission-id')?[next]:[],querySelector:()=>null});
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

test('same-stage status notes and READY pulse refresh without touching drafts',()=>{
    let replaced, alerts=0;
    const oldStatus={dataset:{fuelStatus:'assigned'},outerHTML:'ASSIGNED WAITING FOR TRUCK',replaceWith:n=>replaced=n};
    const newStatus={dataset:{fuelStatus:'assigned'},outerHTML:'ASSIGNED MISSION DATA MISSING',cloneNode(){return this;}};
    const classes=new Set(['is-ready-to-assign','is-assignment-alert-red']);
    const slot={dataset:{completionIdentity:'1:2:3:1:TAIL:4:5:6'},replaceWith(){},cloneNode(){return this;}};
    const stack={querySelector:s=>s==='[data-dispatch-completion-slot]'?slot:null};
    const draft={value:'unsaved 42'};
    const makeRow=status=>({dataset:{dispatchMissionId:'2'},classList:{contains:()=>false,
        toggle(name,on){if(on)classes.add(name);else classes.delete(name);}},querySelector(s){
            if(s==='.neoscorpion-dispatch-action-stack')return stack;
            if(s==='[data-fuel-status]')return status;
            return null;
        }});
    const actual=makeRow(oldStatus),next=makeRow(newStatus);next.dataset.etdUtc='2026-10-18T07:00:00Z';
    const context={root:{querySelectorAll:s=>s.includes('data-dispatch-mission-id')?[actual]:[],querySelector:()=>null},updateAssignmentAlerts(){alerts++;}};
    vm.runInNewContext(expression+'\nthis.runPatch=applyCriticalCompletion;',context);
    context.runPatch({querySelectorAll:s=>s.includes('data-dispatch-mission-id')?[next]:[],querySelector:()=>null});
    assert.equal(replaced,newStatus);
    assert.equal(classes.has('is-ready-to-assign'),false);
    assert.equal(classes.has('is-assignment-alert-red'),false);
    assert.equal(actual.dataset.etdUtc,next.dataset.etdUtc);
    assert.equal(draft.value,'unsaved 42');
    assert.equal(alerts,1);
});

test('an adopted autosave revision still reconciles a missed critical GET while dirty',async()=>{
    const start=source.indexOf('    const poll = async () => {');
    const end=source.indexOf('    const autosaveField = async',start);
    let reconciled=0;
    const context={revisionUrl:'/revision',operationId:'1',revision:8,tailRevision:'tail',tailNotesRevision:0,
        pendingFuelDataRefresh:true,criticalRefreshKey:'1:7:tail',window:{},root:{dataset:{}},
        fetch:async()=>({ok:true,json:async()=>({operation_id:1,revision:8,tail_revision:'tail'})}),
        observeLiveState:()=>true,hasUnsavedControls:()=>true,
        handleChangedFingerprint:async()=>{reconciled++;},reloadPage:async()=>{throw Error('must preserve draft');}};
    vm.runInNewContext(source.slice(start,end)+'\nthis.runPoll=poll;',context);
    await context.runPoll();assert.equal(reconciled,1);
    context.criticalRefreshKey='1:8:tail';await context.runPoll();assert.equal(reconciled,1);
});
