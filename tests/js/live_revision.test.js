'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/live_revision.js', 'utf8');

function world() {
    let at = 100000, requests = 0, status = 200, revision = 0, transferred = 0;
    const channels = new Map(), locks = new Map(), messages = [];
    const flush = async () => { for (let i = 0; i < 50; i++) await Promise.resolve(); };
    function tab(scope = 'user-gateway-session', supported = true) {
        const listeners = {};
        class Channel {
            constructor(key) { this.key = key; if (!channels.has(key)) channels.set(key, new Set()); channels.get(key).add(this); }
            postMessage(value) { messages.push(value); for (const peer of channels.get(this.key)) if (peer !== this) queueMicrotask(() => peer.onmessage?.({data: structuredClone(value)})); }
            close() { channels.get(this.key).delete(this); }
        }
        const timers = new Map(); let id = 0;
        const context = {URL, Response, AbortController, JSON, Object, Math, Number, Promise,
            Date: class extends Date { static now() { return at; } },
            document: {querySelector: () => ({content: scope})},
            navigator: {locks: {request(key, options, action) {
                const previous = locks.get(key) || Promise.resolve();
                const result = previous.catch(() => {}).then(action); locks.set(key, result); return result;
            }}},
            BroadcastChannel: Channel,
            fetch: async () => { requests++; const body = JSON.stringify({ok: status === 200, current_operation: true, operation_id: 8,
                revision, tail_revision: 'hash', html: 'PRIVATE HTML MUST NEVER BE SHARED', fuel: 999});
                transferred += Buffer.byteLength(body); return new Response(body, {status, headers: {'Content-Type': 'application/json','Retry-After': '12','X-Neo-Live-Scope':scope}}); },
            setTimeout(fn, ms) { const next = ++id; if (ms === 25) queueMicrotask(fn); else timers.set(next, {fn, ms}); return next; },
            clearTimeout(id) { timers.delete(id); },
            addEventListener(name, action) { listeners[name] = action; },
            location: {href: 'https://neo.test/dispatch', origin: 'https://neo.test'},
        };
        context.window = context;
        if (!supported) { delete context.BroadcastChannel; delete context.navigator.locks; }
        vm.runInNewContext(source, context);
        return {api: context.NeoLiveRevision, listeners, context};
    }
    return {tab, flush, messages, advance(ms) { at += ms; }, setStatus(s) { status = s; }, setRevision(r) { revision = r; },
        get requests() {return requests;}, get bytes() {return transferred;}};
}

for (const count of [1,2,5]) test(`${count} equivalent tabs share six revision reads without sharing private data`, async () => {
    const w = world(), tabs = Array.from({length:count}, () => w.tab());
    for (let cycle=0; cycle<6; cycle++) {
        const results = await Promise.all(tabs.map(t => t.api.revision('/revision', {}, 5000, '8')));
        assert.equal(results.length,count); for (const r of results) assert.equal((await r.json()).revision,0);
        w.advance(5000);
    }
    assert.equal(w.requests,6);
    assert.ok(w.messages.every(m => !JSON.stringify(m).includes('PRIVATE') && !JSON.stringify(m).includes('999')));
    console.log(JSON.stringify({tabs:count,beforeRevisionGets:count*6,afterRevisionGets:w.requests,afterNetworkBytes:w.bytes}));
});
test('different users, gateways, sorts and screens remain isolated', async () => {
    const w=world(), a=w.tab('one'), b=w.tab('two');
    await Promise.all([a.api.revision('/revision',{},5000,'8'),b.api.revision('/revision',{},5000,'8'),
        a.api.revision('/revision',{},5000,'9'),a.api.revision('/other',{},5000,'8')]);
    assert.equal(w.requests,4);
});
test('coordinator closes, remaining tab continues at its configured cadence', async () => {
    const w=world(),a=w.tab(),b=w.tab();await a.api.revision('/revision',{},5000,'8');
    a.listeners.pagehide();w.advance(5000);w.setRevision(2);
    assert.equal((await (await b.api.revision('/revision',{},5000,'8')).json()).revision,2);
    assert.equal(w.requests,2);
});
test('unsupported coordination keeps correct independent reads', async () => {
    const w=world(),a=w.tab('',false),b=w.tab('',false);
    await Promise.all([a.api.revision('/revision'),b.api.revision('/revision')]);assert.equal(w.requests,2);
});
test('429 is shared as a deadline and no network retry occurs before Retry-After', async () => {
    const w=world(),tabs=Array.from({length:5},()=>w.tab());w.setStatus(429);
    await Promise.all(tabs.map(t=>assert.rejects(t.api.revision('/revision',{},5000,'8'),e=>e.status===429)));
    assert.equal(w.requests,1);w.advance(5000);
    await Promise.all(tabs.map(t=>assert.rejects(t.api.revision('/revision',{},5000,'8'))));assert.equal(w.requests,1);
    w.advance(7000);w.setStatus(200);
    await Promise.all(tabs.map(t=>t.api.revision('/revision',{},5000,'8')));assert.equal(w.requests,2);
});
test('401 and 403 are never broadcast as successful revisions', async () => {
    for (const status of [401,403]) {const w=world(),a=w.tab(),b=w.tab();w.setStatus(status);
        await a.api.revision('/revision');await b.api.revision('/revision');assert.equal(w.requests,2);
        assert.equal(w.messages.filter(m=>m.type==='revision').length,0);}
});
test('sharing expires; a peer never turns an old hint into a new authoritative result', async () => {
    const w=world(),a=w.tab(),b=w.tab();await a.api.revision('/revision');w.advance(5001);w.setRevision(4);
    assert.equal((await (await b.api.revision('/revision')).json()).revision,4);assert.equal(w.requests,2);
});
test('mutation is never coordinated or replayed', async () => {
    const w=world(),a=w.tab();w.setStatus(429);const r=await a.api.fetch('/save',{method:'POST'});
    assert.equal(r.status,429);assert.equal(w.requests,1);assert.equal(w.messages.length,0);
});
test('unavailable Web Lock falls back instead of silently losing live updates', async () => {
    const w=world(),a=w.tab();a.context.navigator.locks.request=()=>Promise.reject(new Error('Locks disabled'));
    assert.equal((await a.api.revision('/revision')).status,200);assert.equal(w.requests,1);
});

test('newly opened tab requests a fresh peer hint without another GET',async()=>{
    const w=world(),a=w.tab();await a.api.revision('/revision');
    const b=w.tab();await b.api.revision('/revision');assert.equal(w.requests,1);
});
test('changing query parameters cannot bypass an endpoint cooldown',async()=>{
    const w=world(),a=w.tab();w.setStatus(429);
    await assert.rejects(a.api.fetch('/state?revision=1'));
    await assert.rejects(a.api.fetch('/state?revision=2'));assert.equal(w.requests,1);
});
test('HTTP-date Retry-After is respected and normal reads recover',async()=>{
    const w=world(),a=w.tab();let calls=0;
    a.context.fetch=async()=>{calls++;return new Response('{}',{status:429,headers:{'Retry-After':'Thu, 01 Jan 1970 00:02:00 GMT'}});};
    await assert.rejects(a.api.fetch('/state'));w.advance(19999);
    await assert.rejects(a.api.fetch('/state'));assert.equal(calls,1);
    w.advance(1);a.context.fetch=async()=>new Response('{}');assert.equal((await a.api.fetch('/state')).status,200);
});
test('unresponsive lock owner cannot indefinitely suppress a tab',async()=>{
    const w=world(),a=w.tab();const timer=a.context.setTimeout;
    a.context.setTimeout=(fn,ms)=>{if(ms===2000)queueMicrotask(fn);return timer(fn,ms);};
    a.context.navigator.locks.request=(_key,options)=>new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('owner gone'),{name:'AbortError'}))));
    assert.equal((await a.api.revision('/revision')).status,200);assert.equal(w.requests,1);
});
test('a changed authenticated scope fails closed without publishing or mutating',async()=>{
    const w=world(),a=w.tab();a.context.fetch=async()=>new Response('{"revision":1}',{headers:{'X-Neo-Live-Scope':'different-login'}});
    await assert.rejects(a.api.revision('/revision'),e=>e.sessionChanged===true);
    assert.equal(w.messages.filter(m=>m.type==='revision').length,0);
});
test('malformed nested source metadata is never broadcast',async()=>{
    const w=world(),a=w.tab();a.context.fetch=async()=>new Response('{"revision":1,"tail_revision":{"private":"aircraft"}}',{headers:{'X-Neo-Live-Scope':'user-gateway-session'}});
    await a.api.revision('/revision');assert.equal(w.messages.filter(m=>m.type==='revision').length,0);
});
test('login redirects never masquerade as successful operational revisions',async()=>{
    const w=world(),a=w.tab();a.context.fetch=async()=>({status:200,redirected:true,headers:new Headers(),body:null});
    await assert.rejects(a.api.revision('/revision'),e=>e.sessionChanged===true);
    assert.equal(w.messages.filter(m=>m.type==='revision').length,0);
});

test('Dispatch peers carry fresh edit authority, never legacy permission-free hints',async()=>{
    const w=world(),a=w.tab(),b=w.tab();let calls=0;
    for(const tab of [a,b]) tab.context.fetch=async()=>{calls++;return new Response(
        JSON.stringify({revision:1,operation_id:8,can_edit:false}),
        {headers:{'X-Neo-Live-Scope':'user-gateway-session'}});};
    const url='/neoscorpion/fuel-dispatch/revision';
    assert.equal((await (await a.api.revision(url)).json()).can_edit,false);
    assert.equal((await (await b.api.revision(url)).json()).can_edit,false);
    assert.equal(calls,1);
    const other=world(),old=other.tab(),fresh=other.tab();
    // A pre-release tab can publish revisions without the new authority bit.
    // The current receiver must make its own authorized request instead.
    await old.api.revision(url);
    await fresh.api.revision(url);
    assert.equal(other.requests,2);
});
