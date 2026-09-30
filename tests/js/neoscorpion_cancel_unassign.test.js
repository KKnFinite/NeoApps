const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'../../app/static/js/neoscorpion_fuel_dispatch_live.js'),'utf8');

function actionHarness({confirmed=true, ok=true, changed=true}={}) {
    const messages=[], requests=[], refreshes=[];
    let finish;
    const context={
        lifecycleSaving:false, pendingFuelDataRefresh:false,
        window:{confirm:()=>confirmed}, FormData:class { constructor(form) { this.form=form; } },
        syncDirtyState:()=>{}, setStatus:(_element,message)=>messages.push(message),
        reloadPage:async()=>refreshes.push('partial'),
        fetch:async(url,options)=>{requests.push({url,options}); return new Promise(resolve=>{
            finish=()=>resolve({ok,json:async()=>({ok,changed,error:'REVIEW REQUIRED'})});
        });},
    };
    const start=source.indexOf('    const submitLifecycleAction = async');
    const end=source.indexOf('    const submitTruckCardAction',start);
    vm.createContext(context);
    vm.runInContext(source.slice(start,end)+'\nglobalThis.submit = submitLifecycleAction;',context);
    const button={disabled:false};
    const form={dataset:{confirm:'Confirm action?'},querySelector:()=>({}),getAttribute:()=>'/cancel-uplift'};
    return {context,button,form,messages,requests,refreshes,finish:()=>finish()};
}

test('cancelled confirmation never submits; rapid submits are guarded until mutation completes',async()=>{
    const cancelled=actionHarness({confirmed:false});
    await cancelled.context.submit(cancelled.form,cancelled.button);
    assert.equal(cancelled.requests.length,0);
    const h=actionHarness();
    const first=h.context.submit(h.form,h.button);
    await h.context.submit(h.form,h.button);
    assert.equal(h.requests.length,1);
    assert.equal(h.context.lifecycleSaving,true);
    assert.equal(h.button.disabled,true);
    h.finish(); await first;
    assert.equal(h.context.lifecycleSaving,false);
    assert.equal(h.button.disabled,false);
});

test('successful lifecycle action uses the existing partial refresh and resumes from canonical state',async()=>{
    const h=actionHarness();
    const pending=h.context.submit(h.form,h.button); h.finish(); await pending;
    assert.deepEqual(h.refreshes,['partial']);
    assert.equal(h.context.pendingFuelDataRefresh,true);
    assert.equal(h.requests[0].options.method,'POST');
    assert.equal(h.requests[0].options.headers['X-Requested-With'],'XMLHttpRequest');
    assert.match(h.messages.join(' '),/Saved/);
    const handler=source.slice(source.indexOf('const submitLifecycleAction'),source.indexOf('const submitTruckCardAction'));
    assert.doesNotMatch(handler,/location\.reload|location\.href/);
});

test('rejected stale or ambiguous action shows error without refresh, retry, or optimistic state changes',async()=>{
    const h=actionHarness({ok:false});
    const pending=h.context.submit(h.form,h.button); h.finish(); await pending;
    assert.equal(h.requests.length,1);
    assert.deepEqual(h.refreshes,[]);
    assert.match(h.messages.join(' '),/Save Failed: REVIEW REQUIRED/);
    assert.equal(h.context.lifecycleSaving,false);
});

test('refresh continues to protect dirty fields, open modal, details, and both scroll axes',()=>{
    assert.match(source,/lifecycleSaving \|\|/);
    assert.match(source,/NeoScorpionFuelData\?\.isOpen\(\)/);
    assert.match(source,/if \(hasUnsavedControls\(\)\) \{/);
    assert.match(source,/currentPanel\.replaceWith\(nextPanel\)/);
    assert.match(source,/dispatchScroll\.restoreSnapshot\(saved\)/);
    assert.match(source,/openDetails/);
    assert.match(source,/tableScrollTop/);
    assert.match(source,/tableScrollLeft/);
    assert.doesNotMatch(source,/window\.location\.reload/);
});

test('cancel refresh restores details and scroll when the active cycle key changes',()=>{
    const start=source.indexOf('function initializeDispatchScroll');
    const end=source.indexOf('function initializeDispatchSelects');
    const stored=new Map();
    const wrap={scrollTop:120,scrollLeft:90,getBoundingClientRect:()=>({top:100,bottom:500})};
    const detail={hidden:false,setAttribute(){}};
    let key='7-2';
    const toggle={getAttribute:()=>`neoscorpion-dispatch-detail-${key}`,setAttribute(){}};
    const row={dataset:{dispatchRowKey:key},getBoundingClientRect:()=>({top:110,bottom:150}),querySelector:()=>toggle};
    const scope={
        querySelector(selector){
            if(selector==='.neoscorpion-table-wrap--sticky')return wrap;
            if(selector.includes('aria-controls'))return selector.includes(`detail-${key}`)?toggle:null;
            return selector.includes(`"${key}"`)?row:null;
        },
        querySelectorAll:selector=>selector.includes('aria-expanded')?[toggle]:[row],
    };
    const window={location:{pathname:'/neoscorpion/fuel-dispatch'},scrollX:4,scrollY:60,
        scrollTo({top,left}){this.scrollY=top;this.scrollX=left;},requestAnimationFrame:fn=>fn(),
        sessionStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)}};
    const initialize=vm.runInNewContext(`(${source.slice(start,end).trim()})`,{
        window,document:{getElementById:id=>id.endsWith(key)?detail:null},
    });
    const scroll=initialize(scope),saved=scroll.snapshot();
    key='7-1'; row.dataset.dispatchRowKey=key; detail.hidden=true;
    wrap.scrollTop=0;wrap.scrollLeft=0;
    scroll.restoreSnapshot(saved);
    assert.equal(detail.hidden,false);
    assert.equal(wrap.scrollTop,120);
    assert.equal(wrap.scrollLeft,90);
    assert.equal(window.scrollY,60);
});
