const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/neoscorpion_fuel_dispatch_live.js','utf8');
function harness(action='mark_topping_off') {
    let finish, expire, requests=0, polls=0;
    const messages=[], input={name:'current_gallons',value:'7000'};
    const form={dataset:{},elements:[input],querySelector:()=>({}),getAttribute:()=>'/assets'};
    const button={disabled:false};
    const context={AbortController, pendingFuelDataRefresh:false,criticalRefreshKey:'old',stateGeneration:0,
        initialControlValues:new WeakMap(),controlValue:c=>c.value,syncDirtyState:()=>{},
        window:{setTimeout:fn=>{expire=fn;return 1;},clearTimeout:()=>{}},
        FormData:class {constructor(){this.values=new Map([['action',action],['current_gallons',input.value]]);}get(k){return this.values.get(k);}},
        setStatus:(_el,msg)=>messages.push(msg),poll:async()=>{polls++;return false;},
        fetch:(_url,options)=>{requests++;return new Promise((resolve,reject)=>{
            finish=payload=>resolve({ok:payload.ok,json:async()=>payload});
            options.signal.addEventListener('abort',()=>reject(new Error('timeout')));
        });}};
    vm.createContext(context);
    vm.runInContext(source.slice(source.indexOf('    const submitTruckCardAction ='),source.indexOf('    const focusNextAutosaveField ='))+'\nglobalThis.submit=submitTruckCardAction;',context);
    return {context,form,button,input,messages,finish:p=>finish(p),expire:()=>expire(),get requests(){return requests;},get polls(){return polls;}};
}
for(const action of ['mark_topping_off','complete_top_off']) {
    test(`${action}: success clears Saving even when refresh is deferred; repeated clicks never resend`,async()=>{
        const h=harness(action),pending=h.context.submit(h.form,h.button);
        await h.context.submit(h.form,h.button);assert.equal(h.requests,1);
        assert.equal(h.messages.at(-1),'Saving...');
        h.finish({ok:true,changed:true});await pending;
        assert.match(h.messages.at(-1),/saved · (TOPPING OFF|AVAILABLE)/);
        assert.equal(h.form.dataset.truckCardBusy,'false');
        assert.equal(h.button.disabled,true);assert.equal(h.polls,1);
        await h.context.submit(h.form,h.button);assert.equal(h.requests,1);
        assert.equal(h.context.initialControlValues.get(h.input),'7000');
    });
    test(`${action}: timeout clears Saving, reports uncertainty, never retries`,async()=>{
        const h=harness(action),pending=h.context.submit(h.form,h.button);
        h.expire();await pending;
        assert.match(h.messages.at(-1),/RESULT UNKNOWN/);
        assert.equal(h.form.dataset.truckCardBusy,'false');
        assert.equal(h.button.disabled,true);
        await h.context.submit(h.form,h.button);assert.equal(h.requests,1);
    });
    test(`${action}: explicit server rejection enables correction, keeps draft`,async()=>{
        const h=harness(action),pending=h.context.submit(h.form,h.button);
        h.input.value='8000';h.finish({ok:false,error:'Trip changed'});await pending;
        assert.equal(h.messages.at(-1),'Save Failed: Trip changed');
        assert.equal(h.button.disabled,false);assert.equal(h.input.value,'8000');
        assert.equal(h.context.initialControlValues.has(h.input),false);
    });
}
test('accepted request never marks later typing saved',async()=>{
    const h=harness('complete_top_off'),pending=h.context.submit(h.form,h.button);
    h.input.value='8000';h.finish({ok:true});await pending;
    assert.equal(h.context.initialControlValues.has(h.input),false);
});
