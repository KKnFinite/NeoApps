const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('app/static/js/neostaffing_shift_roster.js', 'utf8');
function board(mobile = false, stored = null) {
    const element = (dataset = {}) => ({dataset, hidden:false, disabled:false, handlers:{},
        addEventListener(key, fn) {this.handlers[key] = fn;},
        setAttribute(key, value) {this[key] = value;}, click() {this.handlers.click();}});
    const labels = ['D34','D32','D29','D26','D24','D21','D17','D13','D9','D6','D4','D1'];
    const columns = [0,1].flatMap(() => labels.map((label, index) => element({rosterColumn:String(index), rosterColumnSide:index < 6 ? 'west':'east', doorLabel:label})));
    const discharge = element({doorLabel:'DISCHARGE'});
    const sides = ['all','west','east'].map(side => element({rosterSide:side}));
    const paging = element(), previous = element(), next = element(), range = element();
    const media = element(); media.matches = mobile;
    const root = {style:{setProperty(){}}, dataset:{}, querySelectorAll(selector) {return selector === '[data-roster-column]' ? columns:selector === '[data-roster-side]' ? sides:[];},
        querySelector(selector) {return {'[data-roster-discharge]':discharge,'[data-roster-paging]':paging,'[data-roster-previous]':previous,'[data-roster-next]':next,'[data-roster-range]':range}[selector];}};
    let saved = stored;
    vm.runInNewContext(code, {document:{querySelector:() => root}, window:{matchMedia:() => media}, sessionStorage:{getItem:() => saved, setItem:(_,value) => {saved = value;}}});
    return {columns,discharge,sides,next,previous,media,paging,range,saved:() => saved, visible:section => columns.slice(section*12,(section+1)*12).filter(c => !c.hidden).map(c => c.dataset.doorLabel)};
}
test('desktop sides preserve configured order and align Final Door and Ballmat rosters', () => {
    const b = board(); assert.equal(b.visible(0).length,12);
    b.sides[2].click(); assert.deepEqual(b.visible(0),['D17','D13','D9','D6','D4','D1']);
    assert.deepEqual(b.visible(0),b.visible(1));
    assert.deepEqual(board(false,b.saved()).visible(0),b.visible(0));
});
test('mobile pages three doors with both sections aligned and retained side', () => {
    const b = board(true); assert.deepEqual(b.visible(0),['D34','D32','D29']);
    b.next.click(); assert.deepEqual(b.visible(0),['D26','D24','D21']);
    assert.deepEqual(b.visible(0),b.visible(1)); assert.equal(b.next.disabled,true);
    b.sides[2].click(); assert.deepEqual(b.visible(0),['D17','D13','D9']);
    assert.deepEqual(board(true,b.saved()).visible(0),b.visible(0));
    b.media.matches = false; b.media.handlers.change(); assert.equal(b.visible(0).length,6);
});

test('Discharge is a shared right-side start-area column and appears on the final East mobile page', () => {
    const desktop = board(); assert.equal(desktop.discharge.hidden, false);
    const west = board(); west.sides[1].click(); assert.equal(west.discharge.hidden, false);
    const mobile = board(true); assert.equal(mobile.discharge.hidden, true);
    mobile.sides[2].click(); assert.equal(mobile.discharge.hidden, true);
    mobile.next.click(); assert.equal(mobile.discharge.hidden, false);
    assert.match(mobile.range.textContent, /DISCHARGE$/);
});

const tick = () => new Promise(resolve => setImmediate(resolve));
function dropBoard() {
    const requests = [], timers = [], attrs = {};
    const element = (dataset = {}) => ({dataset, children:[], hidden:false, handlers:{},
        classList:{values:new Set(),add(k){this.values.add(k);},remove(k){this.values.delete(k);},toggle(k,v){v?this.values.add(k):this.values.delete(k);}},
        addEventListener(key, fn){this.handlers[key]=fn;}, setAttribute(k,v){this[k]=v;}, contains(node){return node===this;},
        append(...nodes){for(const node of nodes){if(node.parent)node.parent.children.splice(node.parent.children.indexOf(node),1);node.parent=this;this.children.push(node);}},
        querySelector(selector){return this.nodes?.[selector] || null;},
        closest(selector){for(let node=this;node;node=node.parent){if(selector==='[data-final-door-target]' && node.dataset.finalDoorTarget)return node;
            if(selector==='[data-ballmat-door]' && node.dataset.ballmatDoor)return node;}return null;}
    });
    const labels = ['D34','D32','D29','D26','D24','D21','D17','D13','D9','D6','D4','D1'];
    const columns = [0,1].flatMap(section => labels.map((label,index) => {
        const column=element({rosterColumn:String(index),rosterColumnSide:index<6?'west':'east',doorLabel:label,
            ...(section ? {ballmatDoor:String(index+1)}:{finalDoorTarget:String(index+1)})});
        const people=element(), count=element(), empty=element(); people.parent=column;
        column.nodes={'[data-roster-people]':people,'[data-roster-count]':count,'[data-roster-empty]':empty};return column;
    }));
    const source=columns[0], destination=columns[1];
    const person=(id,last,first,ballmat=false)=>{
        const card=element({[ballmat?'ballmatPerson':'rosterPerson']:id,personLast:last,personFirst:first,finalDoor:'1',flowVersion:'old:7'});
        card.nodes={'[data-flow-warning]':element()}; card.nodes['[data-flow-warning]'].hidden=true;
        card.href='/neostaffing/shift-flow?person_id='+id;return card;
    };
    const card=person('42','SMITH','Ada'), other=person('2','Smith','Zoe'), before=person('3','Adams','Zoe');
    source.nodes['[data-roster-people]'].append(card);
    destination.nodes['[data-roster-people]'].append(other,before);
    const ballmat=person('42','SMITH','Ada',true);columns[12].nodes['[data-roster-people]'].append(ballmat);
    columns[13].nodes['[data-roster-people]'].append(person('2','Smith','Zoe',true),person('3','Adams','Zoe',true));
    const feedback=element(), sides=['all','west','east'].map(side=>element({rosterSide:side}));
    const media=element();media.matches=false;
    const version={value:'old:7'}, final={value:'1'}, phase={dataset:{version:'old:7'}};
    const editor={querySelector:s=>s==='[name="expected_version"]'?version:final};
    const root={dataset:{finalDoorUrl:'/neostaffing/shift-flow/0/final-door'},style:{setProperty(){}},setAttribute:(k,v)=>attrs[k]=v,
        querySelectorAll:s=>s==='[data-roster-column]'?columns:s==='[data-roster-side]'?sides:s==='[data-final-door-target]'?columns.slice(0,12):s==='[data-roster-person][draggable="true"]'?[card]:[],
        querySelector:s=>s==='[data-roster-feedback]'?feedback:s==='[data-ballmat-person="42"]'?ballmat:s==='[data-ballmat-door="2"]'?columns[13]:s==='[data-ballmat-door="1"]'?columns[12]:element()};
    vm.runInNewContext(code,{document:{querySelector:s=>s==='[data-shift-roster]'?root:s==='meta[name="csrf-token"]'?{content:'csrf'}:s==='.neostaffing-shift-flow-drawer form'?editor:s==='[data-phase-editor]'?phase:null},
        window:{matchMedia:()=>media},sessionStorage:{getItem(){},setItem(){}},setTimeout:fn=>timers.push(fn),clearTimeout(){},
        fetch:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}))});
    const start=()=>card.handlers.dragstart({preventDefault(){},dataTransfer:{setData(type,value){assert.equal(type,'application/x-neostaffing-final-door');assert.equal(value,'42');}}});
    const drop=target=>target.handlers.drop({preventDefault(){}});
    return {requests,card,source,destination,columns,ballmat,feedback,version,final,phase,attrs,timers,start,drop};
}

test('Final Door drop sends only Final Door and revision with CSRF, locks duplicate saves', async()=>{
    const b=dropBoard();b.start();b.drop(b.destination);b.start();
    assert.equal(b.requests.length,1);assert.equal(b.attrs['aria-busy'],'true');
    assert.equal(b.requests[0].url,'/neostaffing/shift-flow/42/final-door');
    assert.deepEqual(JSON.parse(b.requests[0].options.body),{final_door_work_area_id:'2',expected_version:'old:7'});
    assert.equal(b.requests[0].options.headers['X-CSRF-Token'],'csrf');
    assert.equal(b.card.parent,b.source.nodes['[data-roster-people]']);
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,plan_version:'new:8',flow_color:'wave-2',flow_warning:''})});await tick();
    assert.equal(b.attrs['aria-busy'],'false');assert.equal(b.card.dataset.flowVersion,'new:8');
    assert.equal(b.version.value,'new:8');assert.equal(b.final.value,'2');assert.equal(b.phase.dataset.version,'new:8');
});

test('successful drop sorts last name then first name, aligns Ballmat and updates warning, tint and toast', async()=>{
    const b=dropBoard();b.start();b.drop(b.destination);
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,plan_version:'new:8',flow_color:'wave-2',flow_warning:'Ballmat transition is missing.'})});await tick();
    assert.deepEqual(b.destination.nodes['[data-roster-people]'].children.map(p=>p.dataset.rosterPerson),['3','42','2']);
    assert.deepEqual(b.columns[13].nodes['[data-roster-people]'].children.map(p=>p.dataset.ballmatPerson),['3','42','2']);
    assert.equal(b.source.nodes['[data-roster-empty]'].hidden,false);assert.equal(b.destination.nodes['[data-roster-count]'].textContent,'3');
    assert.equal(b.card.nodes['[data-flow-warning]'].hidden,false);assert.match(b.card.nodes['[data-flow-warning]'].title,/missing/);
    assert.ok(b.card.classList.values.has('is-wave-2'));assert.equal(b.feedback.hidden,false);assert.match(b.feedback.textContent,/Final Door saved/);
    b.timers[0]();assert.equal(b.feedback.hidden,true);
    b.start();b.drop(b.source);b.requests[1].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:1,plan_version:'new:9',flow_color:'at-door',flow_warning:''})});await tick();
    assert.equal(b.card.nodes['[data-flow-warning]'].hidden,true);assert.ok(b.card.classList.values.has('is-at-door'));assert.ok(!b.card.classList.values.has('is-wave-2'));
});

test('stale or failed drop preserves all displayed data, shows error and never replays', async()=>{
    for(const payload of [{conflict:{message:'Flow changed. Reload.'}},{error:'Save failed.'}]){
        const b=dropBoard();b.start();b.drop(b.destination);
        b.requests[0].resolve({ok:false,json:async()=>payload});await tick();
        assert.equal(b.requests.length,1);assert.equal(b.card.parent,b.source.nodes['[data-roster-people]']);
        assert.equal(b.card.dataset.flowVersion,'old:7');assert.equal(b.version.value,'old:7');assert.equal(b.final.value,'1');
        assert.ok(b.feedback.classList.values.has('is-error'));assert.match(b.feedback.textContent,/Reload|failed/);
    }
});

test('external drags and same-door drops do not save',()=>{
    const b=dropBoard();b.drop(b.destination);assert.equal(b.requests.length,0);
    b.start();b.drop(b.source);assert.equal(b.requests.length,0);
});
