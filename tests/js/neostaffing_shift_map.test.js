const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness() {
    const requests = [], events = {}, dragEvents = {};
    let reloads = 0;
    const target = {dataset: {door:'24', band:'bm2', preview:'NO SETUP → West Ballmat → Door 24'},
        addEventListener(name, fn) { events[name] = fn; }, classList: {add(){}, remove(){}}};
    const picker = {open:false}, feedback = {}, preview = {};
    const select = {value:'42', selectedOptions:[{dataset:{version:'home:flow:7'}}]};
    const setup = {value:'door', selectedOptions:[{textContent:'Final Door'}]};
    const card = {dataset:{routePerson:'42'}, addEventListener(name,fn){dragEvents[name]=fn;}};
    const root = {dataset:{routeUrl:'/neostaffing/shift-flow/0/final-composite'},
        querySelector(selector) { return {'[data-route-picker]':picker, '[data-route-employee]':select,
            '[data-route-feedback]':feedback, '[data-route-preview]':preview, '[data-route-setup]':setup,
            '[data-flow-lines]':{replaceChildren(){}}}[selector]; },
        querySelectorAll(selector) { return selector === '[data-route-target]' ? [target] : selector === '[data-route-person]' ? [card] : []; },
        getBoundingClientRect(){return {left:0,top:0};}};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../app/static/js/neostaffing_shift_map.js'),'utf8'), {
        document:{querySelector(selector){return selector==='[data-shift-map]' ? root : selector === '[data-phase-editor]' ? null : {content:'csrf'};}},
        window:{innerWidth:390, addEventListener(){}, location:{reload(){reloads++;}}},
        fetch(url,options){return new Promise(resolve=>requests.push({url,options,resolve}));}
    });
    return {events, dragEvents, picker, requests, feedback, preview, select, reloads:()=>reloads};
}

test('touch standard route is one atomic versioned request, no duplicate while pending', async()=>{
    const h=harness(); h.events.focus();
    assert.match(h.preview.textContent,/SETUP: Final Door/);
    h.events.click(); h.events.click();
    assert.equal(h.requests.length,1);
    const request=h.requests[0];
    assert.match(request.url,/\/42\//);
    assert.deepEqual(JSON.parse(request.options.body),{final_door_id:'24',band:'bm2',expected_version:'home:flow:7',complete_route:true,setup_mode:'door'});
    assert.equal(request.options.headers['X-CSRF-Token'],'csrf');
    request.resolve({ok:true,json:async()=>({changed:true})}); await tick();
    assert.equal(h.reloads(),1);
});

test('stale route shows conflict without replay or optimistic local authority', async()=>{
    const h=harness(); h.events.click();
    h.requests[0].resolve({ok:false,json:async()=>({conflict:{message:'Home changed. Reload.'}})}); await tick();
    assert.equal(h.requests.length,1); assert.equal(h.reloads(),0);
    assert.equal(h.feedback.textContent,'Home changed. Reload.');
});

test('drag reveals complete targets and dropping sends the selected employee once',()=>{
    const h=harness(), data={}; h.select.value='';
    h.dragEvents.dragstart({dataTransfer:{setData(type,value){data[type]=value;}}});
    assert.equal(h.picker.open,true); assert.equal(h.select.value,'42');
    assert.equal(data['text/plain'],'42');
    h.events.drop({preventDefault(){}});
    assert.equal(h.requests.length,1);
    assert.equal(JSON.parse(h.requests[0].options.body).complete_route,true);
});

function phaseHarness() {
    const requests=[], events={}, status={}; let reloads=0;
    const phase={value:'after_w1',addEventListener:(name,fn)=>events[name]=fn};
    const options=[{value:'NO SETUP',dataset:{kind:'No Setup'}},{value:'34',dataset:{kind:'Door'}},{value:'20',dataset:{kind:'Discharge'}}];
    const location={value:'34',options,get selectedOptions(){return options.filter(o=>o.value===this.value);}};
    const editor={dataset:{url:'/neostaffing/shift-flow/42/lane',version:'home:flow:7'},querySelector(s){return {'[data-phase-choice]':phase,'[data-phase-location]':location,'[data-phase-feedback]':status,'[data-phase-transition]':{value:''},'[data-phase-save]':{addEventListener:(_,fn)=>events.save=fn}}[s];}};
    const root={querySelector:()=>null,querySelectorAll:()=>[]};
    vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_shift_map.js','utf8'),{
        document:{querySelector:s=>s==='[data-shift-map]'?root:s==='[data-phase-editor]'?editor:{content:'csrf'}},
        window:{location:{reload:()=>reloads++}},fetch:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}))});
    return {requests,events,status,options,reloads:()=>reloads};
}
test('phase movement retains version, allowed lanes and atomic save',async()=>{
    const h=phaseHarness(); assert.equal(h.options[0].disabled,true); assert.equal(h.options[2].disabled,true);
    h.events.save(); h.events.save(); assert.equal(h.requests.length,1);
    assert.deepEqual(JSON.parse(h.requests[0].options.body),{phase:'after_w1',destination_id:'34',ballmat_transition:'',expected_version:'home:flow:7'});
    h.requests[0].resolve({ok:true,json:async()=>({ok:true})}); await tick(); assert.equal(h.reloads(),1);
});
test('stale phase movement does not replay or reload',async()=>{
    const h=phaseHarness(); h.events.save();
    h.requests[0].resolve({ok:false,json:async()=>({conflict:{message:'Data changed. Reload.'}})});
    await tick(); assert.equal(h.reloads(),0);assert.equal(h.requests.length,1);assert.match(h.status.textContent,/Data changed/);
});

test('side controls filter the same employee rows and keep shared employees visible',()=>{
    const rows=['west','east','shared'].map(side=>({dataset:{journeyHomeSide:side},hidden:false}));
    const buttons=['all','west','east'].map(side=>({dataset:{journeySide:side},setAttribute(name,value){this[name]=value;},addEventListener(_,fn){this.click=fn;}}));
    const root={querySelector:()=>null,querySelectorAll:s=>s==='[data-journey-side]'?buttons:s==='[data-journey-home-side]'?rows:[]};
    vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_shift_map.js','utf8'),{document:{querySelector:s=>s==='[data-shift-map]'?root:null}});
    buttons[2].click(); assert.deepEqual(rows.map(r=>r.hidden),[true,false,false]); assert.equal(buttons[2]['aria-pressed'],'true');
    buttons[1].click(); assert.deepEqual(rows.map(r=>r.hidden),[false,true,false]);
    buttons[0].click(); assert.deepEqual(rows.map(r=>r.hidden),[false,false,false]);
});
