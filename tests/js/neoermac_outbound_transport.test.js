const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {applyDelta} = require('../../app/static/js/neoermac_outbound_live.js');

function board(keys = ['m1', 'm2']) {
    let replaced = 0;
    class Row {
        constructor(key) { this.dataset = {outboundKey: key}; this.parent = null; }
        get nextElementSibling() { return this.parent?.children[this.parent.children.indexOf(this)+1] || null; }
        remove() { if (this.parent) { const p=this.parent; p.children.splice(p.children.indexOf(this),1); this.parent=null; } }
        replaceWith(node) { const p=this.parent, i=p.children.indexOf(this); this.parent=null; p.children[i]=node; node.parent=p; replaced++; }
    }
    class Host {
        constructor(header) { this.children = header ? [new Row(null)] : []; }
        querySelectorAll() { return this.children.filter(n => n.dataset.outboundKey); }
        querySelector() { return this.querySelectorAll()[0] || null; }
        insertBefore(node, reference) { node.remove(); const i=reference ? this.children.indexOf(reference) : this.children.length;
            assert.ok(i>=0); this.children.splice(i,0,node); node.parent=this; }
    }
    const desktop=new Host(false), mobile=new Host(true);
    for (const host of [desktop,mobile]) for(const key of keys) host.insertBefore(new Row(key),null);
    const content = {
        querySelector(selector) { return selector.includes('table-body') ? desktop : mobile; },
        ownerDocument: {createElement() { return {
            set innerHTML(html) { this.html=html; },
            get content() { const html=this.html; return {querySelectorAll() {
                return [...html.matchAll(/data-outbound-key="([^"]+)"/g)].map(m=>new Row(m[1]));
            }}; },
        }; }},
    };
    return {content, desktop, mobile, get replaced() {return replaced;}};
}
const manifest = rows => ({scope:'outbound-v1:1:7',rows});
const html = key => ({desktop:`<tr data-outbound-key="${key}"></tr>`, mobile:`<article data-outbound-key="${key}"></article>`});

test('one mission changes only its desktop/mobile row, preserving other nodes and scroller', () => {
    const b=board(), first=b.desktop.children[0], mobileFirst=b.mobile.children[1], header=b.mobile.children[0];
    assert.equal(applyDelta(b.content,{order:['m1','m2'],rows:{m2:html('m2')}},manifest({m1:'a',m2:'b'}),manifest({m1:'a',m2:'c'})),true);
    assert.equal(b.replaced,2);
    assert.equal(b.desktop.children[0],first);assert.equal(b.mobile.children[1],mobileFirst);assert.equal(b.mobile.children[0],header);
});

test('delta adds, removes and reorders canonical missions, leaving mobile header intact', () => {
    const b=board(['m1','m2','missing:PHX']), original=b.desktop.children[1];
    assert.equal(applyDelta(b.content,{order:['m2','m3'],rows:{m3:html('m3')}},
        manifest({m1:'a',m2:'b','missing:PHX':'z'}),manifest({m2:'b',m3:'c'})),true);
    assert.deepEqual(b.desktop.querySelectorAll().map(n=>n.dataset.outboundKey),['m2','m3']);
    assert.deepEqual(b.mobile.querySelectorAll().map(n=>n.dataset.outboundKey),['m2','m3']);
    assert.equal(b.desktop.children[0],original);
});

test('incompatible scope, missing rows, duplicate order and malformed mobile fragment require atomic fallback', () => {
    const previous=manifest({m1:'a',m2:'b'}), next=manifest({m1:'a',m2:'c'});
    for (const [delta,current] of [
        [{order:['m1','m2'],rows:{m2:html('m2')}},{...next,scope:'other-sort'}],
        [{order:['m1','m2'],rows:{}},next],
        [{order:['m1','m1'],rows:{}},next],
        [{order:['m1','m2'],rows:{m2:{...html('m2'),mobile:'<article></article>'}}},next],
    ]) {
        const b=board(), original=b.desktop.children[1];
        assert.equal(applyDelta(b.content,delta,previous,current),false);
        assert.equal(b.replaced,0);assert.equal(b.desktop.children[1],original);
    }
});

function client() {
    const b=board(), requests=[], statuses=[];
    const root={dataset:{refreshUrl:'/state',outboundRevision:'r0'},querySelector(selector){return selector.includes('outbound-content')?b.content:null;}};
    const context={document:{querySelector(){return null;}},URL,encodeURIComponent,
        location:{origin:'https://test'},NeoLiveUpdates:{create(config){assert.equal(config.intervalMs,10000);return {setServerStatus(s){statuses.push(s);}};}},
        fetch(url) {return new Promise(resolve=>requests.push({url:String(url),reply(payload){resolve({ok:true,json:async()=>payload});}}));}};
    context.window=context;
    vm.runInNewContext(fs.readFileSync('app/static/js/neoermac_outbound_live.js','utf8'),context);
    const live=context.NeoErmacOutboundLive.create(root,{manifest:manifest({m1:'a',m2:'b'}),refresh:{live_screen_refresh_interval_ms:10000}});
    return {b,requests,statuses,live};
}

test('reversed Outbound polls cannot repaint an older revision', async () => {
    const h=client(), old=h.live.poll(), next=h.live.poll();
    h.requests[1].reply({ok:true,changed:true,revision:'r2',row_manifest:manifest({m1:'a',m2:'c'}),row_delta:{order:['m1','m2'],rows:{m2:html('m2')}},refresh:{auto_refresh_enabled:false}});
    await next;
    h.requests[0].reply({ok:true,changed:true,revision:'r1',content_html:'OLD',refresh:{auto_refresh_enabled:true}});await old;
    assert.equal(h.b.content.innerHTML,undefined);assert.equal(h.b.replaced,2);
    assert.equal(h.statuses.at(-1).auto_refresh_enabled,false);
});

test('failed delta requests one snapshot on the next regular poll, without extra fetch or reload', async () => {
    const h=client(), poll=h.live.poll();
    h.requests[0].reply({ok:true,changed:true,revision:'r1',row_manifest:manifest({m1:'a',m2:'c'}),row_delta:{order:['m1','m2'],rows:{}},refresh:{}});
    await poll;assert.equal(h.requests.length,1);
    const next=h.live.poll(), url=new URL(h.requests[1].url);
    assert.equal(url.searchParams.get('rows'),null);assert.equal(url.searchParams.get('revision'),'snapshot-required');
    h.requests[1].reply({ok:true,changed:true,revision:'r1',content_html:'SNAPSHOT',row_manifest:manifest({m1:'a',m2:'c'}),refresh:{}});
    await next;assert.equal(h.b.content.innerHTML,'SNAPSHOT');
});
