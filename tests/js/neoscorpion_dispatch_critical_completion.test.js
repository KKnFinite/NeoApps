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
        if (selector === '[data-neoscorpion-dispatch-details]' || selector === '[data-dispatch-action-count]' || selector === '[data-mobile-dispatch-action-count]') return null;
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

test('shared READY pulse switch updates during critical refresh without replacing drafts',()=>{
    const preferences={dataset:{readyAssignmentPulse:'1',missionRowHeightPercent:'100'}};
    const nextPreferences={dataset:{readyAssignmentPulse:'0'}};
    const draft={value:'unsaved'};
    const context={root:{querySelectorAll:()=>[],querySelector:s=>s==='[data-dispatch-preferences]'?preferences:null},updateAssignmentAlerts(){}};
    vm.runInNewContext(expression+'\nthis.patch=applyCriticalCompletion;',context);
    const panel={querySelectorAll:()=>[],querySelector:s=>s==='[data-dispatch-preferences]'?nextPreferences:null};
    context.patch(panel);assert.equal(preferences.dataset.readyAssignmentPulse,'0');
    nextPreferences.dataset.readyAssignmentPulse='1';context.patch(panel);
    assert.equal(preferences.dataset.readyAssignmentPulse,'1');assert.equal(draft.value,'unsaved');
    // The real live fragment IS the preferences section, not its parent.
    panel.matches=()=>true;panel.dataset={readyAssignmentPulse:'0'};
    context.patch(panel);assert.equal(preferences.dataset.readyAssignmentPulse,'0');
});

test('shared row height applies to the fragment root and restores working scroll without touching drafts',()=>{
    const styles={};let snapshots=0,restores=0;
    const preferences={dataset:{readyAssignmentPulse:'0',missionRowHeightPercent:'100'},
        style:{setProperty:(key,value)=>styles[key]=value}};
    const panel={dataset:{readyAssignmentPulse:'0',missionRowHeightPercent:'130'},matches:()=>true,querySelectorAll:()=>[],querySelector:()=>null};
    const draft={value:'unsaved'};
    const context={root:{querySelectorAll:()=>[],querySelector:s=>s==='[data-dispatch-preferences]'?preferences:null},
        dispatchScroll:{snapshot(){snapshots++;return {};},prepareRefresh:s=>s,restoreSnapshot(){restores++;}},updateAssignmentAlerts(){}};
    vm.runInNewContext(expression+'\nthis.patch=applyCriticalCompletion;',context);
    context.patch(panel);
    assert.equal(styles['--dispatch-row-height-scale'],'1.3');assert.equal(preferences.dataset.readyAssignmentPulse,'0');
    assert.equal(preferences.dataset.missionRowHeightPercent,'130');assert.equal(draft.value,'unsaved');assert.equal(restores,1);
    context.patch(panel);assert.equal(restores,1);
    panel.dataset.missionRowHeightPercent='100';context.patch(panel);
    assert.equal(styles['--dispatch-row-height-scale'],'1');assert.equal(restores,2);assert.equal(snapshots,3);
});

test('a changed visit never leaves the old READY accent on a protected row',()=>{
    const classes=new Set(['is-ready-to-assign','is-assignment-alert-red']);
    const oldSlot={dataset:{completionIdentity:'1:2:3:1:TAIL:4:5:6'},querySelector:()=>null};
    const newSlot={dataset:{completionIdentity:'1:2:3:1:TAIL:4:5:7'}};
    const makeRow=slot=>({dataset:{dispatchMissionId:'2'},classList:{toggle:(n,on)=>on?classes.add(n):classes.delete(n)},
        querySelector:s=>s==='.neoscorpion-dispatch-action-stack'?{querySelector:s=>s==='[data-dispatch-completion-slot]'?slot:null}:null});
    const row=makeRow(oldSlot),next=makeRow(newSlot);
    const context={root:{querySelectorAll:()=>[row],querySelector:()=>null},updateAssignmentAlerts(){}};
    vm.runInNewContext(expression+'\nthis.patch=applyCriticalCompletion;',context);
    context.patch({querySelectorAll:()=>[next],querySelector:()=>null});
    assert.equal(classes.has('is-ready-to-assign'),false);
    assert.equal(oldSlot.textContent,'VISIT CHANGED');
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
