const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const integrity = require('../../app/static/js/neoermac_live_integrity.js');
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; }

function lineup() {
    const pending = [], statuses = [], originals = {};
    const doc = {activeElement: null};
    const selects = ['east', 'west'].map(name => {
        originals['original_' + name] = {value: name + '-0'};
        const listeners = {};
        return {
            name, value: '', disabled: false, dataset: {}, options: [{textContent: 'DEST 1'}],
            addEventListener(event, fn) { listeners[event] = fn; },
            change(value) { this.value = value; listeners.change(); },
            replaceChildren(...options) { this.options = options; },
            closest() { return {querySelectorAll() { return []; }}; },
        };
    });
    const form = {dataset: {lineupAutosaveUrl: '/save'}, querySelector() { return null; }, elements: {namedItem(name) { return originals[name]; }}};
    const root = {dataset: {stateUrl: '/state', lineupRevision: 'r0'},
        querySelector(s) { return s === '[data-lineup-autosave-url]' ? form : null; }, querySelectorAll() { return selects; }};
    const context = {document: doc, Option: function(text, value) { this.textContent = text; this.value = value; }, FormData, URL,
        location: {origin: 'https://test'}, NeoErmacLiveIntegrity: integrity,
        NeoLiveUpdates: {create() { return {setServerStatus(status) { statuses.push(status); }}; }},
        fetch(url, options) { const d = deferred(); pending.push({url: String(url), options, resolve(payload, ok = true) { d.resolve({ok, json: async () => payload}); }}); return d.promise; },
    };
    context.window = context;
    vm.runInNewContext(fs.readFileSync('app/static/js/neoermac_lineup_live.js', 'utf8'), context);
    const live = context.NeoErmacLineupLive.create(root, {refresh: {auto_refresh_enabled: true, live_screen_refresh_interval_ms: 10000}});
    const state = {slots: {east: {destination: 'SDF', original: 'east-1'}, west: {destination: 'ONT', original: 'west-1'}}, destination_choices: ['SDF','ONT','PHX'], pull_times: {}};
    return {pending, statuses, originals, selects, doc, live, state};
}

test('Lineup reverses independent autosave responses without cross-slot overwrite', async () => {
    const h = lineup();
    h.selects[0].change('SDF'); h.selects[1].change('ONT');
    h.pending[1].resolve({ok: true, destination: 'ONT', original: 'west-1'}); await tick();
    h.pending[0].resolve({ok: true, destination: 'SDF', original: 'east-1'}); await tick();
    assert.deepEqual(h.selects.map(s => s.value), ['SDF','ONT']);
    assert.equal(h.originals.original_west.value, 'west-1');
});

test('Lineup serializes same-slot changes and never rolls back newer desired input', async () => {
    const h = lineup();
    h.selects[0].change('SDF'); h.selects[0].change('ONT');
    assert.equal(h.pending.length, 1); // Cannot reverse same-slot acknowledgements.
    assert.equal(h.pending[0].options.body.get('original'), 'east-0');
    h.pending[0].resolve({ok: true, destination: 'SDF', original: 'east-1'}); await tick();
    assert.equal(h.selects[0].value, 'ONT');
    assert.equal(h.pending.length, 2);
    assert.equal(h.pending[1].options.body.get('original'), 'east-1');
    h.pending[1].resolve({ok: true, destination: 'ONT', original: 'east-2'}); await tick();
    assert.equal(h.selects[0].value, 'ONT'); assert.equal(h.originals.original_east.value, 'east-2');
});

test('Lineup poll during save never accepts an unapplied revision; disable still applies', async () => {
    const h = lineup(); h.selects[0].change('PHX');
    const poll = h.live.poll();
    h.pending[1].resolve({ok: true, changed: true, revision: 'r1', state: h.state, refresh: {auto_refresh_enabled: false}});
    await poll;
    assert.equal(h.live.appliedRevision, ''); assert.equal(h.selects[0].value, 'PHX');
    assert.equal(h.statuses.at(-1).auto_refresh_enabled, false);
    h.pending[0].resolve({ok: true, destination: 'PHX', original: 'east-2'}); await tick();
    const retry = h.live.poll(); assert.equal(new URL(h.pending[2].url).searchParams.get('revision'), '');
    h.pending[2].resolve({ok: true, changed: true, revision: 'r2', state: {...h.state, slots: {...h.state.slots, east: {destination:'PHX', original:'east-2'}}}, refresh: {auto_refresh_enabled: true}});
    await retry; assert.equal(h.live.appliedRevision, 'r2');
});

test('old Lineup poll after successful save cannot repaint or accept its revision', async () => {
    const h = lineup(); const poll = h.live.poll(); h.selects[0].change('PHX');
    h.pending[1].resolve({ok: true, destination: 'PHX', original: 'east-2'}); await tick();
    h.pending[0].resolve({ok: true, changed: true, revision: 'r1', state: h.state, refresh: {}}); await poll;
    assert.equal(h.selects[0].value, 'PHX'); assert.equal(h.live.appliedRevision, '');
});

test('reversed Lineup polls, focused/dirty controls and observed versus applied revisions', async () => {
    const h = lineup(); const old = h.live.poll(), next = h.live.poll();
    h.doc.activeElement = h.selects[0];
    h.pending[1].resolve({ok:true,changed:true,revision:'r2',state:h.state,refresh:{}}); await next;
    assert.equal(h.live.observedRevision,'r2'); assert.equal(h.live.appliedRevision,'r0');
    assert.equal(h.selects[0].value,''); assert.equal(h.selects[1].value,'ONT');
    h.pending[0].resolve({ok:true,changed:true,revision:'r1',state:h.state,refresh:{}}); await old;
    assert.equal(h.live.observedRevision,'r2');
    h.doc.activeElement = null;
    const retry=h.live.poll();h.pending[2].resolve({ok:true,changed:true,revision:'r2',state:h.state,refresh:{}});await retry;
    assert.equal(h.selects[0].value,'SDF');assert.equal(h.live.appliedRevision,'r2');
});

test('Lineup conflict retains dirty choice, does not retry; refresh patches choices/times without reload', async () => {
    const h=lineup(); h.selects[0].change('PHX'); h.selects[0].change('ONT');
    h.pending[0].resolve({ok:false,error:'Conflict'}, false);await tick();
    assert.equal(h.pending.length,1); assert.equal(h.selects[0].value,'ONT');assert.equal(h.selects[0].dataset.localDirty,'true');
    assert.equal(h.live.reconcile(h.state),false); assert.equal(h.selects[0].value,'ONT');
    const source=fs.readFileSync('app/templates/neonodes/neoermac/building_lineup.html','utf8')+fs.readFileSync('app/static/js/neoermac_lineup_live.js','utf8');
    assert.ok(!source.includes('location.reload'));
});

test('Lineup omitted common data preserves options/times and does not rebuild unchanged native controls', async () => {
    const h=lineup();
    const first=h.live.poll();
    h.pending[0].resolve({ok:true,changed:true,revision:'r1',common_version:'c1',state:h.state,refresh:{}});await first;
    const options=h.selects[1].options;
    h.doc.activeElement=h.selects[0];
    const next=h.live.poll();
    assert.equal(new URL(h.pending[1].url).searchParams.get('common'),'c1');
    h.pending[1].resolve({ok:true,changed:true,revision:'r2',common_version:'c1',state:{slots:h.state.slots},refresh:{}});await next;
    assert.equal(h.selects[1].options,options);
    assert.equal(h.live.appliedRevision,'r1');
    h.doc.activeElement=null;
    const retry=h.live.poll();
    h.pending[2].resolve({ok:true,changed:true,revision:'r2',common_version:'c1',state:{slots:h.state.slots},refresh:{}});await retry;
    assert.equal(h.live.appliedRevision,'r2');
    h.selects[0].change('PHX');
    h.pending[3].resolve({ok:true,destination:'PHX',original:'east-2',pull_times:{pure:'02:00'}});await tick();
    const after=h.live.poll();assert.equal(new URL(h.pending[4].url).searchParams.get('common'),null);
    h.pending[4].resolve({ok:true,changed:true,revision:'r3',common_version:'c2',state:h.state,refresh:{}});await after;
});

function listOf(...names) {
    const list = {children: [], insertBefore(row, before) { row.remove(); const i=before ? this.children.indexOf(before) : this.children.length;this.children.splice(i,0,row);row.parent=this; }};
    function row(name) { return {name, dirty:false, remove() {if(this.parent){this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;}}}; }
    names.forEach(name=>list.insertBefore(row(name),null)); return {list,row};
}

test('Door membership adds/removes/reorders, preserving only genuinely dirty obsolete cards', () => {
    const {list,row}=listOf('SDF','ONT','LAX');const dirty=list.children[0];dirty.dirty=true;
    const reconcile=names=>integrity.reconcileMembership(list,names.map(row),r=>r.name,r=>r.dirty,()=>{});
    assert.equal(reconcile(['LAX','PHX','ONT']),false);
    assert.deepEqual(list.children.map(r=>r.name),['SDF','ONT','LAX','PHX']);
    assert.equal(list.children[0],dirty); // The dirty/focused subtree was not moved.
    dirty.dirty=false;
    assert.equal(reconcile(['ONT','PHX']),true);assert.deepEqual(list.children.map(r=>r.name),['ONT','PHX']);
    assert.equal(reconcile([]),true);assert.equal(list.children.length,0);
    reconcile(['SDF']);assert.equal(list.children[0].name,'SDF');
});

test('Door actual poll wiring rejects old polls after save and unapplied membership revision', async () => {
    const html=fs.readFileSync('app/static/js/neoermac_door_live.js','utf8');
    const source=html.slice(html.indexOf('    const responseOrder ='),html.indexOf('    const escapeHtml ='));
    const requests=[], applied=[];
    const context={window:{NeoErmacLiveIntegrity:integrity,location:{origin:'https://test'}},root:{dataset:{doorViewRevision:'r0'}},stateUrl:'/state',URL,
        setRefreshStatus(){},applyState(s){applied.push(s);return s.complete;},fetch(){const d=deferred();requests.push(payload=>d.resolve({ok:true,json:async()=>payload}));return d.promise;}};
    vm.runInNewContext(source+'\nwindow.poll=refreshState;window.revision=()=>currentRevision;',context);
    const old=context.window.poll();const ticket=context.window.neoErmacBeginPullSave();
    context.window.neoErmacEndPullSave(ticket);requests[0]({ok:true,changed:true,revision:'old',state:{complete:true}});await old;
    assert.equal(applied.length,0);assert.equal(context.window.revision(),'');
    const partial=context.window.poll();requests[1]({ok:true,changed:true,revision:'new',state:{complete:false}});await partial;
    assert.equal(context.window.revision(),'');
    const complete=context.window.poll();requests[2]({ok:true,changed:true,revision:'new',state:{complete:true}});await complete;
    assert.equal(context.window.revision(),'new');
});

test('Door save snapshots cannot overwrite a later mutation or a poll begun during mutation', () => {
    const order=integrity.createOrder(); const first=order.beginSave(),second=order.beginSave();
    const poll=order.beginPoll();assert.equal(order.endSave(second),false);assert.equal(order.endSave(first),false);
    assert.equal(order.accepts(poll),false);
    const fresh=order.beginPoll();assert.equal(order.accepts(fresh),true);
    const html=fs.readFileSync('app/static/js/neoermac_door_live.js','utf8');
    assert.ok(html.includes('if (sequences[pullKey] !== sequence) return;'));
    assert.ok(html.includes('applyCardState(card, payload.card, pullKey, submitted, applySnapshot)'));
    assert.ok(html.includes('window.neoErmacBindPullControls = bindPullControls'));
});

function doorSaves() {
    const html=fs.readFileSync('app/static/js/neoermac_door_live.js','utf8');
    const source=html.slice(html.indexOf('    const savePull ='),html.indexOf('    const scheduleSave ='));
    const requests=[], paints=[], errors=[], order=integrity.createOrder();
    const inputs={pure:{value:'01:45'},mix:{value:'01:55'}}, originals={pure:{value:'p0'},mix:{value:'m0'}};
    const toggles={pure:{checked:false},mix:{checked:false}};
    const card={dataset:{doorDestination:'SDF'},classList:{add(){},remove(){}},querySelector(selector){
        if(selector==='[data-pull-operation]')return {value:'1'};
        if(selector==='[data-pull-mission]')return {value:'2'};
        return originals[selector.includes('pure')?'pure':'mix'];
    }};
    const context={window:{neoErmacBeginPullSave:()=>order.beginSave(),neoErmacEndPullSave:t=>order.endSave(t)},
        FormData,fieldWrites:new WeakMap(),saveSequences:new WeakMap(),saveUrl:'/save',root:{querySelector(){return {value:'D1'};}},
        findActualInput:(c,k)=>inputs[k],findNoPullToggle:(c,k)=>toggles[k],scopeInput:{value:'0'},hhmmPattern:/^\d\d:\d\d$/,
        setCardError(c,error){if(error)errors.push(error);},setCardStatus(){},
        applyCardState(c,state,key,submitted,paint){originals[key].value=state.original;paints.push({key,paint});},
        fetch(url,options){const d=deferred();requests.push({options,resolve(payload,ok=true){d.resolve({ok,json:async()=>payload});}});return d.promise;}};
    vm.runInNewContext(source+'\nwindow.save=savePull;',context);
    return {context,card,inputs,originals,requests,paints,errors,save:key=>context.window.save(card,key)};
}

test('Door actual autosave coalesces duplicate blur; queued edit uses acknowledged original', async () => {
    const h=doorSaves();const first=h.save('pure');await h.save('pure');
    assert.equal(h.requests.length,1);
    h.inputs.pure.value='01:46';await h.save('pure');
    h.requests[0].resolve({ok:true,card:{original:'p1'}});await tick();
    assert.equal(h.requests.length,2);assert.equal(h.requests[1].options.body.get('original'),'p1');
    assert.equal(h.requests[1].options.body.get('actual_pull'),'01:46');
    h.requests[1].resolve({ok:true,card:{original:'p2'}});await first;
    assert.equal(h.originals.pure.value,'p2');assert.equal(h.card.dataset.pendingPullSaves,'0');
});

test('Door reversed independent saves acknowledge fields but not obsolete whole-card snapshots', async () => {
    const h=doorSaves();const pure=h.save('pure'),mix=h.save('mix');
    h.requests[1].resolve({ok:true,card:{original:'m1'}});await mix;
    h.requests[0].resolve({ok:true,card:{original:'p1'}});await pure;
    assert.equal(h.originals.pure.value,'p1');assert.equal(h.originals.mix.value,'m1');
    assert.deepEqual(h.paints.map(p=>p.paint),[false,false]);
});

test('Door rejected writes never replay queued edits', async () => {
    const h=doorSaves();const save=h.save('pure');h.inputs.pure.value='01:46';await h.save('pure');
    h.requests[0].resolve({ok:false,error:'Conflict'},false);await save;
    assert.equal(h.requests.length,1);assert.equal(h.inputs.pure.value,'01:46');
    assert.deepEqual(h.errors,['Conflict']);assert.equal(h.card.dataset.pendingPullSaves,'0');
});

test('Door overlapping saves cannot infer commit order from in-order network replies', async () => {
    const h=doorSaves();const pure=h.save('pure'),mix=h.save('mix');
    h.requests[0].resolve({ok:true,card:{original:'p1'}});await pure;
    h.requests[1].resolve({ok:true,card:{original:'m1'}});await mix;
    assert.deepEqual(h.paints.map(p=>p.paint),[false,false]);
    const single=h.save('pure');h.requests[2].resolve({ok:true,card:{original:'p2'}});await single;
    assert.equal(h.paints.at(-1).paint,true);
});
