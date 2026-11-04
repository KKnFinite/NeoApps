const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/neoscorpion_fuel_dispatch_live.js', 'utf8');

function spearHarness(overrides = {}) {
    const payload = {ok:true, operation_id:1, revision:4, tail_revision:'tail',
        spear_fragments:{}, spear_fingerprints:{overview:'current'}, spear_plan_token:'fresh',
        spear_automation_enabled:false};
    const context = {reloading:false, criticalRefreshBusy:false, preferenceSaving:false,
        dispatcherCheckSaving:0, hasUnsavedControls:()=>false, stateGeneration:0,
        spearFingerprints:{}, spearRenderedAt:0, operationId:'1', revision:4, tailRevision:'tail',
        panelUrl:'/neoscorpion/fuel-dispatch/live-panel', currentResponse:()=>true,
        window:{location:{href:'https://example.test/neoscorpion/fuel-dispatch'}}, URL,
        fetch:async()=>({ok:true,json:async()=>payload}),
        root:{dataset:{spearPlanToken:'old'},querySelectorAll:()=>[],querySelector:()=>null},
        dispatchScroll:{snapshot:()=>({}),restoreSnapshot(){}},document:{activeElement:null},
        renderSortControls(){}, protectedControls:()=>[], initialControlValues:new WeakMap(),
        scheduleSpearAutomation(){}, setStatus(){}, pendingFuelDataRefresh:false, ...overrides};
    const start=source.indexOf('    const refreshSpear = async () => {');
    vm.runInNewContext(source.slice(start,source.indexOf('    const reloadPage = async',start))+
        '\nthis.pollSpear=refreshSpear;',context);
    return {context,payload};
}

test('timed SPEAR fetches only its endpoint and retains current operational revision', async()=>{
    let url;
    const h=spearHarness();h.context.fetch=async target=>{url=String(target);return {ok:true,json:async()=>h.payload};};
    assert.equal(await h.context.pollSpear(),true);
    assert.match(url,/spear_only=1/);assert.match(url,/operation_id=1/);
    assert.equal(h.context.root.dataset.spearPlanToken,'fresh');
    assert.equal(h.context.revision,4);
});

test('a dirty draft prevents a timed SPEAR request',async()=>{
    const h=spearHarness({hasUnsavedControls:()=>true,fetch:()=>{throw Error('must not fetch');}});
    assert.equal(await h.context.pollSpear(),false);
    assert.equal(h.context.root.dataset.spearPlanToken,'old');
});

test('typing during transit prevents SPEAR DOM and token replacement',async()=>{
    let dirty=false;
    const h=spearHarness({hasUnsavedControls:()=>dirty});
    h.context.fetch=async()=>{dirty=true;return {ok:true,json:async()=>h.payload};};
    assert.equal(await h.context.pollSpear(),false);
    assert.equal(h.context.root.dataset.spearPlanToken,'old');
});

test('an unapplied operational revision or stale response cannot become SPEAR authority',async()=>{
    for (const mode of ['newer','stale','tail']) {
        const h=spearHarness();
        if(mode==='newer')h.payload.revision=5;
        if(mode==='stale')h.context.currentResponse=()=>false;
        if(mode==='tail')h.payload.tail_revision='other';
        assert.equal(await h.context.pollSpear(),false);
        assert.equal(h.context.root.dataset.spearPlanToken,'old');
    }
});

test('provider or throttle error leaves existing SPEAR intact and permits a bounded next refresh',async()=>{
    const h=spearHarness({fetch:async()=>{throw Error('cooldown');}});
    assert.equal(await h.context.pollSpear(),false);
    assert.equal(h.context.reloading,false);assert.equal(h.context.pendingFuelDataRefresh,true);
    assert.equal(h.context.root.dataset.spearPlanToken,'old');
});

test('clean operational changes download one full panel; dirty/dialog changes use critical only',async()=>{
    const start=source.indexOf('    const handleChangedFingerprint = async');
    const expression=source.slice(start,source.indexOf('    const poll = async',start))+
        '\nthis.changed=handleChangedFingerprint;';
    for (const mode of ['clean','dirty','dialog','dirty-in-transit']) {
        let full=0,critical=0;
        const context={hasUnsavedControls:()=>mode==='dirty',
            window:{NeoScorpionFuelData:{isOpen:()=>mode==='dialog'}},
            dispatcherCheckSaving:0,preferenceSaving:false,pendingFuelDataRefresh:false,
            reloadPage:async()=>{full++;return mode!=='dirty-in-transit';},
            refreshCriticalCompletion:async()=>{critical++;return true;}};
        vm.runInNewContext(expression,context);await context.changed('1',4,'tail');
        assert.equal(full,mode==='dirty'||mode==='dialog'?0:1);
        assert.equal(critical,mode==='clean'?0:1);
    }
});
