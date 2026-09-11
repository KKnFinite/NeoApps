const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness(canEdit=true) {
    const listeners={}, orderListeners={}, elements=[];
    const key=s=>s.replace(/^\[data-|\]$/g,'').split('=')[0].replace(/-([a-z])/g,(_,c)=>c.toUpperCase());
    const match=(e,s)=>key(s) in e.dataset && (!s.includes('=') || e.dataset[key(s)]===s.split('"')[1]);
    const node=dataset=>{
        const e={dataset,checked:false,value:'4',disabled:!canEdit,attrs:{},
            matches:s=>match(e,s), closest:s=>match(e,s)?e:null,
            setAttribute(k,v){this.attrs[k]=v;}, focus(){},setPointerCapture(){}};
        elements.push(e); return e;
    };
    const cut=node({dischargeCut:''});
    const back=node({dischargeBack:'Bay 1',side:'east'});
    const slider=node({dischargeStatus:'Bay 1',side:'east'});
    const statusLabel={textContent:'Overflowing'};
    slider.closest=s=>s==='[data-tunnel-bay]'?{querySelector:()=>statusLabel}:null;
    const orderHost={children:[],addEventListener:(name,fn)=>{orderListeners[name]=fn;},
        querySelector:s=>orderHost.children.find(e=>match(e,s)),
        querySelectorAll:s=>orderHost.children.filter(e=>match(e,s)),
        append(e){this.insertBefore(e,null);},
        insertBefore(e,target){this.children=this.children.filter(n=>n!==e);const i=this.children.indexOf(target);this.children.splice(i<0?this.children.length:i,0,e);}};
    for(const n of [5,4,3,2,1]) {
        const e=node({priorityBay:'Bay '+n}); orderHost.children.push(e);
        Object.defineProperty(e,'nextSibling',{get:()=>orderHost.children[orderHost.children.indexOf(e)+1] || null});
    }
    let state={routing:{cut_discharge:false,bay_priority_order:orderHost.children.map(e=>e.dataset.priorityBay)},
        sides:{east:{bays:[{bay_name:'Bay 1',status:'Overflowing',back_pickup:false}]},west:{bays:[]}}};
    const root={querySelector:s=>s==='[data-discharge-order]'?orderHost:elements.find(e=>match(e,s)),
        addEventListener:(name,fn)=>{listeners[name]=fn;}};
    const calls=[],pending=[],errors=[];
    const context={window:{},document:{elementFromPoint:()=>orderHost.children[4]}};
    vm.runInNewContext(fs.readFileSync('app/static/js/neosektor_discharge_controls.js','utf8'),context);
    const api=context.window.NeoSektorDischargeControls.create(root,{state,canEdit,
        onError:e=>errors.push(e),send:command=>new Promise((resolve,reject)=>{calls.push(command);pending.push({resolve,reject});})});
    const apply=next=>{state=next;api.apply(next);};
    const finish=async(next,fail=false)=>{apply(next);const p=pending.shift();fail?p.reject(new Error('conflict')):p.resolve();await tick();};
    return {api,cut,back,slider,statusLabel,orderHost,calls,errors,listeners,orderListeners,apply,finish,get state(){return state;}};
}

test('cut pending state survives a poll; acknowledgement reconciles with no global disabling', async()=>{
    const h=harness();h.cut.checked=true;h.listeners.change({target:h.cut});
    assert.equal(h.calls.length,1);assert.equal(h.cut.disabled,true);
    assert.equal(h.back.disabled,false);assert.equal(h.slider.disabled,false);
    h.apply(structuredClone(h.state));assert.equal(h.cut.checked,true);
    await h.finish({...h.state,routing:{...h.state.routing,cut_discharge:true}});
    assert.equal(h.cut.checked,true);assert.equal(h.cut.disabled,false);
    h.cut.checked=false;h.listeners.change({target:h.cut});
    assert.equal(h.calls[1].expected_cut,true);
    await h.finish(h.state,true); // Adapter supplied current canonical conflict state.
    assert.equal(h.cut.checked,true);assert.equal(h.calls.length,2);assert.deepEqual(h.errors,['conflict']);
});

test('priority keyboard reorder sends expected saved order and preserves pending layout',async()=>{
    const h=harness(), original=[...h.state.routing.bay_priority_order];
    h.orderListeners.keydown({target:h.orderHost.children[0],key:'ArrowRight',preventDefault(){}});
    const desired=['Bay 4','Bay 5','Bay 3','Bay 2','Bay 1'];
    assert.deepEqual([...h.calls[0].order],desired);assert.deepEqual([...h.calls[0].expected_order],original);
    h.apply(structuredClone(h.state));assert.deepEqual(h.orderHost.children.map(e=>e.dataset.priorityBay),desired);
    await h.finish({...h.state,routing:{...h.state.routing,bay_priority_order:desired}});
    assert.equal(h.orderHost.children[0].attrs['aria-label'].startsWith('Bay 4, position 1.'),true);
});

test('touch drag uses same priority transaction',async()=>{
    const h=harness();
    h.orderListeners.pointerdown({target:h.orderHost.children[0],pointerType:'touch',pointerId:7});
    h.orderListeners.pointermove({pointerType:'touch',clientX:300,clientY:50});
    h.orderListeners.pointerup({pointerType:'touch'});
    assert.deepEqual([...h.calls[0].order],['Bay 4','Bay 3','Bay 2','Bay 1','Bay 5']);
    await h.finish({...h.state,routing:{...h.state.routing,bay_priority_order:[...h.calls[0].order]}});
    assert.equal(h.calls.length,1);
});

test('native desktop drag survives mouse pointer cancellation and saves once',async()=>{
    const h=harness();
    h.orderListeners.dragstart({target:h.orderHost.children[0],dataTransfer:{setData(){}},preventDefault(){}});
    h.orderListeners.pointercancel({pointerType:'mouse'});
    h.orderListeners.pointerup({pointerType:'mouse'});
    h.orderListeners.drop({target:h.orderHost.children[2],preventDefault(){}});
    h.orderListeners.dragend();
    assert.deepEqual([...h.calls[0].order],['Bay 4','Bay 3','Bay 5','Bay 2','Bay 1']);
    await h.finish({...h.state,routing:{...h.state.routing,bay_priority_order:[...h.calls[0].order]}});
    assert.equal(h.calls.length,1);
});

test('bay release saves latest desired status after in-flight save; shared Back Pickup reconciles',async()=>{
    const h=harness();
    for(const value of ['3','1','4']) { h.slider.value=value; h.listeners.change({target:h.slider}); }
    assert.equal(h.calls.length,1);assert.equal(h.slider.disabled,false);
    const full={...h.state,sides:{...h.state.sides,east:{bays:[{bay_name:'Bay 1',status:'Full',back_pickup:false}]}}};
    await h.finish(full);
    assert.equal(h.calls.length,2);assert.equal(h.calls[1].bay_statuses['Bay 1'],'Overflowing');
    assert.equal(h.slider.value,'4');
    assert.equal(h.statusLabel.textContent,'Overflowing');
    await h.finish({...full,sides:{...full.sides,east:{bays:[{bay_name:'Bay 1',status:'Overflowing',back_pickup:true}]}}});
    assert.equal(h.back.checked,true);assert.equal(h.back.disabled,false);
    h.apply(full);assert.equal(h.back.checked,false);assert.equal(h.back.disabled,true);
});

test('view-only controls cannot submit or reorder',()=>{
    const h=harness(false);
    h.cut.checked=true;h.listeners.change({target:h.cut});
    h.orderListeners.keydown({target:h.orderHost.children[0],key:'ArrowRight',preventDefault(){}});
    assert.equal(h.calls.length,0);assert.equal(h.cut.disabled,true);assert.equal(h.back.disabled,true);
});
