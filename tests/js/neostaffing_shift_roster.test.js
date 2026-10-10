const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('app/static/js/neostaffing_shift_roster.js', 'utf8');
function board(width = 1920, stored = null) {
    const element = (dataset = {}) => ({dataset, hidden:false, disabled:false, handlers:{},
        addEventListener(key, fn) {this.handlers[key] = fn;},
        setAttribute(key, value) {this[key] = value;}, click() {this.handlers.click();}});
    const labels = ['D34','D32','D29','D26','D24','D21','D17','D13','D9','D6','D4','D1'];
    const columns = labels.map((label, index) => element({rosterColumn:String(index), doorLabel:label}));
    const paging = element(), previous = element(), next = element(), range = element();
    const media = element(); media.matches = width <= 700;
    const scroll = element(); scroll.scrollLeft = 0;
    const root = {style:{setProperty(){}}, dataset:{}, querySelectorAll(selector) {return selector === '[data-roster-column]' ? columns:[];},
        querySelector(selector) {return {'[data-roster-paging]':paging,'[data-roster-previous]':previous,'[data-roster-next]':next,'[data-roster-range]':range,'[data-roster-scroll]':scroll}[selector];}};
    let saved = stored;
    const window = {innerWidth:width,matchMedia:() => media,handlers:{},addEventListener(key,fn){this.handlers[key]=fn;}};
    vm.runInNewContext(code, {document:{querySelector:() => root}, window, sessionStorage:{getItem:() => saved, setItem:(_,value) => {saved = value;}}});
    return {columns,next,previous,media,paging,range,scroll,window,saved:() => saved, visible:() => columns.filter(c => !c.hidden).map(c => c.dataset.doorLabel)};
}
test('desktop shows the full configured Final Door order', () => {
    const b = board(); assert.equal(b.visible(0).length,12);
    assert.deepEqual(b.visible(0),['D34','D32','D29','D26','D24','D21','D17','D13','D9','D6','D4','D1']);
    assert.deepEqual(board(1920,b.saved()).visible(0),b.visible(0));
});
test('narrow mobile pages continuously through three doors, retaining its page', () => {
    const b = board(390); assert.deepEqual(b.visible(0),['D34','D32','D29']);
    assert.equal(b.range.textContent,'D34–D29');
    b.next.click(); assert.deepEqual(b.visible(0),['D26','D24','D21']);
    assert.equal(b.next.disabled,false);
    b.next.click(); assert.deepEqual(b.visible(0),['D17','D13','D9']);
    assert.deepEqual(board(390,b.saved()).visible(0),b.visible(0));
    b.next.click(); assert.deepEqual(b.visible(0),['D6','D4','D1']);assert.equal(b.next.disabled,true);
    b.media.matches = false; b.media.handlers.change(); assert.equal(b.visible(0).length,12);
});

test('wider mobile pages four doors with continuous range labels and swipe navigation', () => {
    const mobile = board(450);
    assert.deepEqual(mobile.visible(0), ['D34','D32','D29','D26']);
    assert.equal(mobile.range.textContent, 'D34–D26');
    let prevented = 0;
    mobile.scroll.handlers.touchstart({touches:[{clientX:280,clientY:100}]});
    mobile.scroll.handlers.touchend({changedTouches:[{clientX:150,clientY:110}],preventDefault(){prevented++;}});
    assert.deepEqual(mobile.visible(0), ['D24','D21','D17','D13']);
    assert.equal(mobile.range.textContent, 'D24–D13');
    mobile.next.click();
    assert.deepEqual(mobile.visible(0), ['D9','D6','D4','D1']);
    assert.equal(mobile.range.textContent, 'D9–D1');
    assert.equal(mobile.next.disabled,true);
    assert.equal(prevented,1);
    mobile.window.innerWidth = 390; mobile.window.handlers.resize();
    assert.deepEqual(mobile.visible(0), ['D17','D13','D9']);
});

const tick = () => new Promise(resolve => setImmediate(resolve));
function dropBoard(width = 1920) {
    const requests = [], timers = [], attrs = {};
    const element = (dataset = {}) => ({dataset, children:[], hidden:false, handlers:{},
        classList:{values:new Set(),add(k){this.values.add(k);},remove(k){this.values.delete(k);},toggle(k,v){v?this.values.add(k):this.values.delete(k);}},
        addEventListener(key, fn){this.handlers[key]=fn;}, setAttribute(k,v){this[k]=v;}, contains(node){return node===this;},
        append(...nodes){for(const node of nodes){if(node.parent)node.parent.children.splice(node.parent.children.indexOf(node),1);node.parent=this;this.children.push(node);}},
        remove(){if(this.parent)this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;},
        insertBefore(node,next){if(node.parent)node.parent.children.splice(node.parent.children.indexOf(node),1);node.parent=this;
            const index=next?this.children.indexOf(next):this.children.length;this.children.splice(index,0,node);},
        get nextSibling(){return this.parent?.children[this.parent.children.indexOf(this)+1] || null;},
        querySelector(selector){return this.nodes?.[selector] || null;},
        closest(selector){for(let node=this;node;node=node.parent){if(selector==='[data-final-door-target]' && node.dataset.finalDoorTarget)return node;
            if(selector==='[data-ballmat-side]' && node.dataset.ballmatSide)return node;
            if(selector==='[data-roster-needs-people]' && node.dataset.rosterNeedsPeople)return node;
            if(selector==='[data-roster-unassigned-people]' && node.dataset.rosterUnassignedPeople)return node;}return null;}
    });
    const labels = ['D34','D32','D29','D26','D24','D21','D17','D13','D9','D6','D4','D1'];
    const columns = labels.map((label,index) => {
        const column=element({rosterColumn:String(index),rosterColumnSide:index<6?'west':'east',doorLabel:label,
            finalDoorTarget:String(index+1)});
        const people=element(), count=element(), empty=element(); people.parent=column;
        column.nodes={'[data-roster-people]':people,'[data-roster-count]':count,'[data-roster-empty]':empty};return column;
    });
    const source=columns[0], destination=columns[1];
    const person=(id,last,first,ballmat=false)=>{
        const card=element({[ballmat?'ballmatPerson':'rosterPerson']:id,personName:`${first} ${last}`,personLast:last,personFirst:first,flowColor:'wave-2',finalDoor:'1',flowVersion:'old:7',ballmatStart:'false'});
        card.nodes={'[data-flow-warning]':element(),'[data-setup-strip]':element()}; card.nodes['[data-flow-warning]'].hidden=true;
        card.nodes['[data-setup-strip]'].hidden=true;
        card.href='/neostaffing/shift-flow?person_id='+id;return card;
    };
    const card=person('42','SMITH','Ada'), other=person('2','Smith','Zoe'), before=person('3','Adams','Zoe');
    source.nodes['[data-roster-people]'].append(card);
    destination.nodes['[data-roster-people]'].append(other,before);
    const ballmatSides={west:element({ballmatSide:'west'}),east:element({ballmatSide:'east'})};
    for(const side of Object.values(ballmatSides))side.nodes={'[data-ballmat-people]':element(),'[data-ballmat-side-count]':{textContent:side.dataset.ballmatSide==='west'?'1':'2'},'[data-ballmat-empty]':element()};
    for(const side of Object.values(ballmatSides))side.nodes['[data-ballmat-people]'].parent=side;
    const ballmat=person('42','SMITH','Ada',true);ballmatSides.west.nodes['[data-ballmat-people]'].append(ballmat);
    ballmatSides.east.nodes['[data-ballmat-people]'].append(person('2','Smith','Zoe',true),person('3','Adams','Zoe',true));
    const needsPeople=element({rosterNeedsPeople:'true'}), needs=person('77','Pending','Ian');
    needs.dataset.finalDoor='';needs.dataset.ballmatStart='true';
    const needsReason=element({rosterNeedsReason:'77'}), needsEmpty=element();needsEmpty.hidden=true;
    needsPeople.append(needs,needsReason,needsEmpty);
    const unassigned=person('88','Unassigned','Uma');
    unassigned.dataset.finalDoor=''; unassigned.dataset.flowColor=''; unassigned.dataset.ballmatStart='false';
    const unassignedPeople=element({rosterUnassignedPeople:'true'}), unassignedEmpty=element();
    unassignedEmpty.hidden=true;
    unassignedPeople.append(unassigned, unassignedEmpty);
    const search=element(),searchStatus=element(),searchPrevious=element(),searchNext=element();search.value='';
    const scroll=element(),paging=element(),previous=element(),next=element(),range=element();scroll.scrollLeft=0;scroll.scrollWidth=1800;
    const extra=person('99','Other','Zoe');columns[7].nodes['[data-roster-people]'].append(extra);
    const needsCount={textContent:'1'},unassignedCount={textContent:'1'},rosterTotal={textContent:'3'},plannedCount={textContent:'3'},ballmatTotal={textContent:'3'},headerBallmatTotal={textContent:'3'};
    const feedback=element(), sides=['all','west','east'].map(side=>element({rosterSide:side}));
    const media=element();media.matches=width<=700;
    const version={value:'old:7'}, final={value:'1'}, startArea={value:'1'}, setup={value:''}, transition={value:'2'}, phase={dataset:{version:'old:7'}};
    const fields={expected_version:version,shift_flow_final_door_work_area_id:final,
        shift_flow_sort_start_work_area_id:startArea,shift_flow_setup_work_area_id:setup,shift_flow_ballmat_transition:transition};
    const editor={querySelector:s=>fields[s.match(/name="([^"]+)"/)[1]]};
    const westCount=ballmatSides.west.nodes['[data-ballmat-side-count]'],eastCount=ballmatSides.east.nodes['[data-ballmat-side-count]'];
    const root={dataset:{finalDoorUrl:'/neostaffing/shift-flow/0/final-door'},style:{setProperty(){}},setAttribute:(k,v)=>attrs[k]=v,
        querySelectorAll:s=>s==='[data-roster-column]'?columns:s==='[data-roster-side]'?sides:s==='[data-final-door-target]'?columns.slice(0,12):s==='[data-roster-person][draggable="true"]'?[card,needs,unassigned]:s==='[data-roster-person]'?[card,other,before,extra,needs,unassigned]:[],
        querySelector:s=>s==='[data-roster-scroll]'?scroll:s==='[data-roster-paging]'?paging:s==='[data-roster-previous]'?previous:s==='[data-roster-next]'?next:s==='[data-roster-range]'?range:s==='[data-roster-feedback]'?feedback:s==='[data-ballmat-person="42"]'?ballmat:s==='[data-ballmat-person="77"]'?Object.values(ballmatSides).flatMap(side=>side.nodes['[data-ballmat-people]'].children).find(row=>row.dataset.ballmatPerson==='77')||null:s==='[data-ballmat-person="88"]'?null:s==='[data-roster-needs-people]'?needsPeople:s==='[data-roster-unassigned-people]'?unassignedPeople:s==='[data-roster-unassigned-count]'?unassignedCount:s==='[data-roster-unassigned-empty]'?unassignedEmpty:s==='[data-roster-needs-count]'?needsCount:s==='[data-roster-needs-empty]'?needsEmpty:s==='[data-roster-needs-reason="77"]'?needsReason:s==='[data-roster-total-count]'?rosterTotal:s==='[data-ballmat-total-count]'?ballmatTotal:s==='[data-roster-search]'?search:s==='[data-search-status]'?searchStatus:s==='[data-search-previous]'?searchPrevious:s==='[data-search-next]'?searchNext:s==='[data-ballmat-side="west"]'?ballmatSides.west:s==='[data-ballmat-side="east"]'?ballmatSides.east:element()};
    let hitTarget = destination, frameId = 0;
    const frames = new Map(), handlers = {}, body = element();
    scroll.getBoundingClientRect = () => ({left:0,right:300,top:0,bottom:400,width:300});
    let scrollLeft = 0;
    Object.defineProperty(scroll, 'scrollLeft', {get:()=>scrollLeft, set:value=>{scrollLeft=Math.max(0,Math.min(156,value));}});
    for (const item of [card, needs, unassigned]) {
        item.setPointerCapture = () => {};
        item.getBoundingClientRect = () => ({width:120});
        item.cloneNode = () => ({...element(), style:{}, removeAttribute(){}});
    }
    vm.runInNewContext(code,{document:{body,elementFromPoint:()=>hitTarget,createElement:()=>element(),querySelector:s=>s==='[data-shift-roster]'?root:s==='[data-roster-planned-count]'?plannedCount:s==='[data-roster-header-ballmat-count]'?headerBallmatTotal:s==='meta[name="csrf-token"]'?{content:'csrf'}:s==='.neostaffing-shift-flow-drawer form'?editor:s==='[data-phase-editor]'?phase:null},
        window:{innerWidth:width,matchMedia:()=>media,addEventListener:(key,fn)=>handlers[key]=fn,
            requestAnimationFrame:fn=>{frames.set(++frameId,fn);return frameId;},cancelAnimationFrame:id=>frames.delete(id)},sessionStorage:{getItem(){},setItem(){}},setTimeout:fn=>timers.push(fn),clearTimeout(){},
        fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))});
    const start=()=>card.handlers.dragstart({preventDefault(){},dataTransfer:{setData(type,value){assert.equal(type,'application/x-neostaffing-final-door');assert.equal(value,'42');}}});
    const startNeeds=()=>needs.handlers.dragstart({preventDefault(){},dataTransfer:{setData(type,value){assert.equal(type,'application/x-neostaffing-final-door');assert.equal(value,'77');}}});
    const startUnassigned=()=>unassigned.handlers.dragstart({preventDefault(){},dataTransfer:{setData(type,value){assert.equal(type,'application/x-neostaffing-final-door');assert.equal(value,'88');}}});
    const drop=target=>target.handlers.drop({preventDefault(){}});
    const touch = (type, x=150, y=100, person=needs) => {
        const event={type,pointerType:'touch',pointerId:1,isPrimary:true,clientX:x,clientY:y,preventDefault(){}};
        if(type==='pointerdown') person.handlers.pointerdown(event); else handlers[type](event);
    };
    const advance = timestamp => { const pending=[...frames.values()];frames.clear();pending.forEach(fn=>fn(timestamp)); };
    return {requests,card,source,destination,columns,ballmatSides,ballmat,feedback,version,final,startArea,setup,transition,westCount,eastCount,phase,attrs,timers,start,startNeeds,startUnassigned,drop,person,needs,needsPeople,needsReason,needsCount,needsEmpty,unassignedPeople,unassignedEmpty,unassignedCount,rosterTotal,plannedCount,ballmatTotal,headerBallmatTotal,unassigned,search,searchStatus,searchPrevious,searchNext,scroll,range,extra,touch,advance,hit:target=>{hitTarget=target;},body};
}

test('Unassigned mouse drag creates a plan in the selected Door and updates counts', async()=>{
    const b=dropBoard();b.startUnassigned();b.drop(b.destination);
    assert.equal(b.requests.length,1);
    assert.equal(b.requests[0].url,'/neostaffing/shift-flow/88/final-door');
    assert.deepEqual(JSON.parse(b.requests[0].options.body),{final_door_work_area_id:'2',expected_version:'old:7'});
    assert.equal(b.unassigned.parent,b.destination.nodes['[data-roster-people]']);
    assert.equal(b.unassignedCount.textContent,'0');assert.equal(b.unassignedEmpty.hidden,false);
    assert.equal(b.rosterTotal.textContent,'4');assert.equal(b.plannedCount.textContent,'4');
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,
        plan_version:'new:8',flow_color:'at-door',flow_warning:'',has_setup:false})});await tick();
    assert.equal(b.unassigned.dataset.finalDoor,'2');assert.equal(b.unassigned.dataset.flowVersion,'new:8');
    assert.ok(b.unassigned.classList.values.has('is-at-door'));
    assert.equal(b.unassignedCount.textContent,'0');assert.equal(b.ballmatTotal.textContent,'3');
});
test('Unassigned conflict restores the employee and both counts without a plan', async()=>{
    const b=dropBoard();b.startUnassigned();b.drop(b.destination);
    b.requests[0].resolve({ok:false,json:async()=>({conflict:{message:'Reload before assigning.'}})});await tick();
    assert.equal(b.unassigned.parent,b.unassignedPeople);
    assert.equal(b.unassignedCount.textContent,'1');assert.equal(b.unassignedEmpty.hidden,true);
    assert.equal(b.rosterTotal.textContent,'3');assert.equal(b.plannedCount.textContent,'3');
    assert.equal(b.unassigned.dataset.finalDoor,'');assert.equal(b.unassigned.dataset.flowVersion,'old:7');
    assert.match(b.feedback.textContent,/Reload/);
});
test('Unassigned mobile touch drag reaches an offscreen Door and does not open editor', async()=>{
    const b=dropBoard(390);
    b.touch('pointerdown',150,100,b.unassigned);b.touch('pointermove',298);
    for(let timestamp=16;timestamp<=2400;timestamp+=16)b.advance(timestamp);
    assert.equal(b.range.textContent,'D17–D9');
    b.hit(b.columns[7]);b.touch('pointermove',150);b.touch('pointerup');
    assert.equal(b.requests.length,1);
    assert.equal(b.unassigned.parent,b.columns[7].nodes['[data-roster-people]']);
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:8,
        plan_version:'new:8',flow_color:'',flow_warning:'Ballmat transition is missing.',has_setup:false})});await tick();
    assert.equal(b.unassignedCount.textContent,'0');assert.equal(b.unassignedEmpty.hidden,false);
    assert.equal(b.body.children.length,0);
});

test('Needs Assignment touch drag pages to an offscreen Door and uses the protected save', async()=>{
    const b=dropBoard(390);
    b.touch('pointerdown'); b.touch('pointermove',298);
    for(let timestamp=16;timestamp<=2400;timestamp+=16)b.advance(timestamp);
    assert.equal(b.range.textContent,'D17–D9');
    b.hit(b.columns[7]);b.touch('pointermove',150);b.touch('pointerup');
    assert.equal(b.requests.length,1);
    assert.equal(b.requests[0].url,'/neostaffing/shift-flow/77/final-door');
    assert.deepEqual(JSON.parse(b.requests[0].options.body),{final_door_work_area_id:'8',expected_version:'old:7'});
    assert.equal(b.needs.parent,b.columns[7].nodes['[data-roster-people]']);
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:8,plan_version:'new:8',
        flow_color:'wave-2',flow_warning:'',has_setup:false,previous_ballmat_side:'west',ballmat_side:'east'})});
    await tick();
    assert.equal(b.needsReason.parent,null);
    assert.equal(b.needsCount.textContent,'0');
    assert.equal(b.range.textContent,'D17–D9');
    assert.equal(b.body.children.length,0);
});
test('Needs Assignment touch drag restores row and feedback on conflicting save', async()=>{
    const b=dropBoard(390);
    b.touch('pointerdown',100);b.touch('pointermove',150);b.touch('pointerup');
    b.requests[0].resolve({ok:false,json:async()=>({conflict:{message:'Reload before assigning.'}})});
    await tick();
    assert.equal(b.needs.parent,b.needsPeople);
    assert.equal(b.needsReason.hidden,false);
    assert.match(b.feedback.textContent,/Reload/);
});

test('Needs Assignment drop moves immediately, preserves existing fields and adds Ballmat roster on success', async()=>{
    const b=dropBoard();b.startNeeds();b.drop(b.destination);
    assert.deepEqual(JSON.parse(b.requests[0].options.body),{final_door_work_area_id:'2',expected_version:'old:7'});
    assert.equal(b.needs.parent,b.destination.nodes['[data-roster-people]']);
    assert.equal(b.needsCount.textContent,'0');assert.equal(b.needsEmpty.hidden,false);
    assert.equal(b.needsReason.hidden,true);assert.equal(b.rosterTotal.textContent,'4');
    assert.equal(b.plannedCount.textContent,'4');assert.equal(b.ballmatTotal.textContent,'3');assert.equal(b.headerBallmatTotal.textContent,'3');
    assert.deepEqual(b.ballmatSides.east.nodes['[data-ballmat-people]'].children.map(row=>row.dataset.ballmatPerson),['2','3']);
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,plan_version:'new:8',flow_color:'wave-2',
        flow_warning:'Ballmat transition is missing.',has_setup:true,previous_ballmat_side:'west',ballmat_side:'east'})});await tick();
    assert.equal(b.needs.dataset.finalDoor,'2');assert.equal(b.needs.dataset.flowVersion,'new:8');
    assert.equal(b.needsReason.parent,null);assert.equal(b.eastCount.textContent,'3');assert.equal(b.westCount.textContent,'1');
    assert.equal(b.ballmatTotal.textContent,'4');
    assert.deepEqual(b.ballmatSides.east.nodes['[data-ballmat-people]'].children.map(row=>row.dataset.ballmatPerson),['3','77','2']);
    assert.equal(b.needs.nodes['[data-flow-warning]'].hidden,false);
    assert.equal(b.setup.value,'');assert.equal(b.transition.value,'2');
    assert.equal(typeof b.unassigned.handlers.dragstart,'function');
});

test('Needs Assignment conflict restores rail, reason, counts, Ballmat roster and revision', async()=>{
    const b=dropBoard();b.startNeeds();b.drop(b.destination);
    b.requests[0].resolve({ok:false,json:async()=>({conflict:{message:'Reload and try again.'}})});await tick();
    assert.equal(b.needs.parent,b.needsPeople);assert.equal(b.needsPeople.children[0],b.needs);
    assert.equal(b.needsReason.hidden,false);assert.equal(b.needsCount.textContent,'1');assert.equal(b.needsEmpty.hidden,true);
    assert.equal(b.rosterTotal.textContent,'3');assert.equal(b.plannedCount.textContent,'3');assert.equal(b.ballmatTotal.textContent,'3');assert.equal(b.headerBallmatTotal.textContent,'3');
    assert.deepEqual(b.ballmatSides.east.nodes['[data-ballmat-people]'].children.map(row=>row.dataset.ballmatPerson),['2','3']);
    assert.equal(b.needs.dataset.flowVersion,'old:7');assert.equal(b.needs.dataset.finalDoor,'');
    assert.match(b.feedback.textContent,/Reload/);
});

test('subsequent Final Door move changes a Ballmat side without duplicating its start-area marker', async()=>{
    const b=dropBoard();b.startNeeds();b.drop(b.destination);
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,plan_version:'new:8',
        flow_color:'wave-2',previous_ballmat_side:'west',ballmat_side:'east'})});await tick();
    const marker=b.ballmatSides.east.nodes['[data-ballmat-people]'].children.find(row=>row.dataset.ballmatPerson==='77');
    b.startNeeds();b.drop(b.source);
    b.requests[1].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:1,plan_version:'new:9',
        flow_color:'wave-2',previous_ballmat_side:'east',ballmat_side:'west'})});await tick();
    assert.equal(marker.parent,b.ballmatSides.west.nodes['[data-ballmat-people]']);
    assert.equal(b.ballmatTotal.textContent,'4');
    assert.equal(b.westCount.textContent,'2');assert.equal(b.eastCount.textContent,'2');
});

test('name search highlights without rearranging, pages through matches and searches both rail sections',()=>{
    const b=dropBoard(390), order=b.destination.nodes['[data-roster-people]'].children.slice();
    b.search.value='zOe';b.search.handlers.input();
    assert.equal(b.searchStatus.textContent,'1 / 3');assert.equal(b.range.textContent,'D34–D29');
    assert.ok(b.card.classList.values.has('is-search-dimmed'));
    assert.ok(b.extra.classList.values.has('is-search-match'));
    b.searchNext.handlers.click();b.searchNext.handlers.click();
    assert.equal(b.searchStatus.textContent,'3 / 3');assert.equal(b.range.textContent,'D17–D9');
    assert.deepEqual(b.destination.nodes['[data-roster-people]'].children,order);
    b.search.value='ian';b.search.handlers.input();
    assert.ok(b.needs.classList.values.has('is-search-current'));assert.equal(b.scroll.scrollLeft,156);
    b.search.value='uma';b.search.handlers.input();assert.ok(b.unassigned.classList.values.has('is-search-match'));
    b.search.value='';b.search.handlers.input();
    assert.equal(b.searchStatus.textContent,'');assert.ok(!b.card.classList.values.has('is-search-dimmed'));
    assert.ok(!b.unassigned.classList.values.has('is-search-match'));
});

test('Final Door drop sends only Final Door and revision with CSRF, locks duplicate saves', async()=>{
    const b=dropBoard();b.start();b.drop(b.destination);b.start();
    assert.equal(b.requests.length,1);assert.equal(b.attrs['aria-busy'],'true');
    assert.equal(b.requests[0].url,'/neostaffing/shift-flow/42/final-door');
    assert.deepEqual(JSON.parse(b.requests[0].options.body),{final_door_work_area_id:'2',expected_version:'old:7'});
    assert.equal(b.requests[0].options.headers['X-CSRF-Token'],'csrf');
    assert.equal(b.card.parent,b.destination.nodes['[data-roster-people]']);
    assert.deepEqual(b.destination.nodes['[data-roster-people]'].children.map(p=>p.dataset.rosterPerson),['3','42','2']);
    assert.equal(b.card.dataset.finalDoor,'1');assert.equal(b.card.dataset.flowVersion,'old:7');
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,plan_version:'new:8',flow_color:'wave-2',flow_warning:''})});await tick();
    assert.equal(b.attrs['aria-busy'],'false');assert.equal(b.card.dataset.flowVersion,'new:8');
    assert.equal(b.version.value,'new:8');assert.equal(b.final.value,'2');assert.equal(b.phase.dataset.version,'new:8');
});

test('successful drop sorts last name then first name, aligns Ballmat and updates warning, tint and toast', async()=>{
    const b=dropBoard();b.start();b.drop(b.destination);
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,plan_version:'new:8',flow_color:'wave-2',flow_warning:'Ballmat transition is missing.'})});await tick();
    assert.deepEqual(b.destination.nodes['[data-roster-people]'].children.map(p=>p.dataset.rosterPerson),['3','42','2']);
    assert.deepEqual(b.ballmatSides.west.nodes['[data-ballmat-people]'].children.map(p=>p.dataset.ballmatPerson),['42']);
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
        assert.equal(b.card.parent,b.destination.nodes['[data-roster-people]']);
        assert.equal(b.ballmat.parent,b.ballmatSides.west.nodes['[data-ballmat-people]']);
        b.requests[0].resolve({ok:false,json:async()=>payload});await tick();
        assert.equal(b.requests.length,1);assert.equal(b.card.parent,b.source.nodes['[data-roster-people]']);
        assert.equal(b.card.dataset.flowVersion,'old:7');assert.equal(b.version.value,'old:7');assert.equal(b.final.value,'1');
        assert.equal(b.ballmat.parent,b.ballmatSides.west.nodes['[data-ballmat-people]']);
        assert.equal(b.source.nodes['[data-roster-count]'].textContent,'1');
        assert.equal(b.destination.nodes['[data-roster-count]'].textContent,'2');
        assert.deepEqual(b.destination.nodes['[data-roster-people]'].children.map(p=>p.dataset.rosterPerson),['3','2']);
        assert.ok(b.feedback.classList.values.has('is-error'));assert.match(b.feedback.textContent,/Reload|failed/);
    }
});

test('network failure rolls back card and Ballmat grouping without touching editor or colors', async()=>{
    const b=dropBoard();b.card.classList.add('is-wave-2');b.start();b.drop(b.destination);
    b.requests[0].reject(new Error('Network failed'));await tick();
    assert.equal(b.card.parent,b.source.nodes['[data-roster-people]']);
    assert.equal(b.ballmat.parent,b.ballmatSides.west.nodes['[data-ballmat-people]']);
    assert.ok(b.card.classList.values.has('is-wave-2'));assert.equal(b.setup.value,'');
    assert.equal(b.transition.value,'2');assert.equal(b.attrs['aria-busy'],'false');
});

test('server error restores the original alphabetical position and reports non-JSON failures', async()=>{
    const b=dropBoard(), source=b.source.nodes['[data-roster-people]'], destination=b.destination.nodes['[data-roster-people]'];
    const [after,before]=destination.children;
    source.append(before,b.card,after);
    const original=source.children.map(p=>p.dataset.rosterPerson);
    b.start();b.drop(b.destination);
    assert.equal(b.card.parent,destination);
    b.requests[0].resolve({ok:false,json:async()=>{throw new Error('Not JSON');}});await tick();
    assert.deepEqual(source.children.map(p=>p.dataset.rosterPerson),original);
    assert.equal(destination.children.length,0);assert.equal(b.card.dataset.finalDoor,'1');
    assert.equal(b.feedback.textContent,'Final Door was not saved.');
});

test('ordinary clicks follow the existing editor link; drag release click is suppressed', async()=>{
    const b=dropBoard();let prevented=0,stopped=0;
    const click=()=>b.card.handlers.click({preventDefault(){prevented++;},stopPropagation(){stopped++;}});
    click();assert.equal(prevented,0);assert.equal(b.card.href,'/neostaffing/shift-flow?person_id=42');
    b.start();b.drop(b.destination);b.card.handlers.dragend();click();assert.equal(prevented,1);
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,plan_version:'new:8',flow_color:'wave-2'})});await tick();
    b.card.handlers.pointerdown({pointerType:'mouse'});click();assert.equal(prevented,1);assert.equal(stopped,1);
    b.start();b.drop(b.source);b.card.handlers.dragend();
    b.requests[1].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:1,plan_version:'new:9',flow_color:'wave-2'})});await tick();
    click();assert.equal(prevented,2);assert.equal(stopped,2);
});

test('accepted green drop reconciles Start and matching Setup in the shared editor and keeps green', async()=>{
    const b=dropBoard();b.setup.value='1';b.start();b.drop(b.destination);
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,plan_version:'new:8',flow_color:'at-door',flow_warning:'',
        editor_changes:{shift_flow_sort_start_work_area_id:2,shift_flow_final_door_work_area_id:2,shift_flow_setup_work_area_id:2}})});await tick();
    assert.equal(b.startArea.value,'2');assert.equal(b.setup.value,'2');assert.equal(b.final.value,'2');
    assert.ok(b.card.classList.values.has('is-at-door'));assert.equal(b.transition.value,'2');
});

test('accepted Ballmat side switch updates only the changed editor fields and side counts', async()=>{
    const b=dropBoard();b.setup.value='24';b.start();b.drop(b.destination);
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,plan_version:'new:8',flow_color:'wave-2',flow_warning:'',
        previous_ballmat_side:'west',ballmat_side:'east',
        editor_changes:{shift_flow_sort_start_work_area_id:88,shift_flow_final_door_work_area_id:2}})});await tick();
    assert.equal(b.startArea.value,'88');assert.equal(b.setup.value,'24');assert.equal(b.transition.value,'2');
    assert.equal(b.westCount.textContent,'0');assert.equal(b.eastCount.textContent,'3');
    assert.equal(b.ballmat.parent,b.ballmatSides.east.nodes['[data-ballmat-people]']);
});

test('a drawer with a different revision is never reconciled by another employee drop', async()=>{
    const b=dropBoard();b.version.value='other:9';b.start();b.drop(b.destination);
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,plan_version:'new:8',flow_color:'at-door',
        editor_changes:{shift_flow_sort_start_work_area_id:2,shift_flow_final_door_work_area_id:2}})});await tick();
    assert.equal(b.version.value,'other:9');assert.equal(b.final.value,'1');assert.equal(b.startArea.value,'1');
});

test('external drags and same-door drops do not save',()=>{
    const b=dropBoard();b.drop(b.destination);assert.equal(b.requests.length,0);
    b.start();b.drop(b.source);assert.equal(b.requests.length,0);
});

test('optimistic and accepted drops sort exact color groups then names, while Ballmat stays alphabetical', async()=>{
    const b=dropBoard(), people=b.destination.nodes['[data-roster-people]'];
    const expected=[];
    for(const [index,color] of ['at-door','wave-1','wave-2','discharge','cleanup',''].entries()){
        for(const [offset,first,last] of [[2,'Zoe','SMITH'],[0,'Zoe','Adams'],[1,'Ada','smith']]){
            const p=b.person(String(100+index*3+offset),last,first);p.dataset.flowColor=color;people.append(p);
        }
        expected.push(...[0,1,2].map(offset=>String(100+index*3+offset)));
    }
    // Keep the original two employees and the dragged card in the green group.
    people.children.filter(p=>['2','3'].includes(p.dataset.rosterPerson)).forEach(p=>{p.dataset.flowColor='at-door';});
    b.card.dataset.flowColor='at-door';b.start();b.drop(b.destination);
    const ids=()=>people.children.map(p=>p.dataset.rosterPerson);
    assert.deepEqual(ids(),['3','100','42','101','2','102',...expected.slice(3)]);
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,plan_version:'new:8',flow_color:'wave-2',has_setup:true})});await tick();
    assert.deepEqual(ids(),['3','100','101','2','102',...expected.slice(3,6),'106','42','107','108',...expected.slice(9)]);
    assert.equal(b.card.dataset.flowColor,'wave-2');assert.equal(b.card.nodes['[data-setup-strip]'].hidden,false);
    assert.deepEqual(b.ballmatSides.west.nodes['[data-ballmat-people]'].children.map(p=>p.dataset.ballmatPerson),['42']);
});

test('Setup strip is reconciled independently of the flow background and retained on failed saves', async()=>{
    const b=dropBoard();b.card.nodes['[data-setup-strip]'].hidden=false;b.start();b.drop(b.destination);
    b.requests[0].resolve({ok:false,json:async()=>({error:'Failed'})});await tick();
    assert.equal(b.card.nodes['[data-setup-strip]'].hidden,false);assert.equal(b.card.dataset.flowColor,'wave-2');
    b.start();b.drop(b.destination);
    b.requests[1].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,plan_version:'new:8',flow_color:'wave-2',has_setup:false})});await tick();
    assert.equal(b.card.nodes['[data-setup-strip]'].hidden,true);assert.ok(b.card.classList.values.has('is-wave-2'));
});

test('Discharge-start card retains blue after a Final Door drop, without stale wave tint', async()=>{
    const b=dropBoard();b.card.classList.add('is-wave-2');b.start();b.drop(b.destination);
    b.requests[0].resolve({ok:true,json:async()=>({ok:true,final_door_work_area_id:2,plan_version:'new:8',flow_color:'discharge',flow_warning:''})});await tick();
    assert.ok(b.card.classList.values.has('is-discharge'));
    assert.ok(!b.card.classList.values.has('is-wave-2'));
    assert.equal(b.card.nodes['[data-flow-warning]'].hidden,true);
});
