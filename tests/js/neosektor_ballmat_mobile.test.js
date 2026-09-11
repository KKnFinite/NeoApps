const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const settle = () => new Promise(resolve => setImmediate(resolve));

function harness({available = true, mode = 1, canEdit = true} = {}) {
    const elements = [], listeners = {};
    const element = dataset => {
        const e = {dataset, disabled: !canEdit, value: '0', textContent: '', attrs: {},
            setAttribute(k, v) { this.attrs[k] = v; },
            matches(s) { return this.closest(s) === this; },
            closest(s) {
                const key = s.slice(6, -1).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
                return key in this.dataset ? this : null;
            }};
        elements.push(e); return e;
    };
    [1, 2].forEach(n => element({bmMode: String(n)}));
    element({bmModeStatus: ''}); element({bmNotice: ''});
    for (const key of ['first', 'second', 'open']) {
        for (const position of ['total', 'left', 'right']) {
            element({bmValue: `${key}:${position}`, bmInput:''});
            [-1, 1].forEach(delta => element({bmStep: String(delta), metric: key, position}));
        }
        element({bmOther: key});
        for (const field of ['bmArrive', 'bmUnload', 'bmRoute']) element({[field]: key});
    }
    ['Bay 1', 'Bay 2'].forEach(name => { element({bmBay: name}); element({bmBayValue: name}); element({bmBack:name}); });
    const select = s => {
        if (s === 'button,input') return elements.filter(e => e.dataset.bmStep || e.dataset.bmMode || e.dataset.bmBay);
        const [, attr, value] = s.match(/^\[data-([\w-]+)(?:="([^"]+)")?\]$/);
        const key = attr.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        return elements.filter(e => key in e.dataset && (value === undefined || e.dataset[key] === value));
    };
    const panel = {dataset: {}, attrs: {}, setAttribute(k,v) { this.attrs[k]=v; },
        removeAttribute(k) { delete this.attrs[k]; }, querySelectorAll: select,
        querySelector: s => select(s)[0], addEventListener: (name, fn) => { listeners[name] = fn; }};
    let server = {spotters: {available, mode, mode_version:0, pending_mode:null, request_version:0, side: 'east', counts: Object.fromEntries(['first','second','open'].map(k => [k,{left:0,right:0,total:0}]))},
        sides: Object.fromEntries(['east','west'].map(k => [k,{waves:[{count:0},{count:0}],open_bays:0,bays:['Bay 1','Bay 2'].map(bay_name=>({bay_name,status:'Empty'}))}])),
        waves:[{left:0,left_to_arrive:0},{left:0,left_to_arrive:0}], ballmat_routing:{first:'-',second:'-'}};
    const calls = [], pending = [];
    const timers = [];
    const context = {window: {setTimeout:fn=>timers.push(fn), clearTimeout:()=>{}}, document: {activeElement:null}};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../app/static/js/neosektor_ballmat_mobile.js'),'utf8'),context);
    const api = context.window.NeoBallmatMobile.create({querySelector:()=>panel}, {
        state: structuredClone(server), canEdit, statusLabels:['Empty','Light','Moderate','Full','Overflowing'],
        send: payload => new Promise(resolve => { calls.push(payload); pending.push({payload,resolve}); }),
    });
    const click = (key='first',position=mode === 1 ? 'total' : 'left',delta=1) => listeners.click({target:elements.find(e=>e.dataset.metric===key && e.dataset.position===position && e.dataset.bmStep===String(delta))});
    const bay = (value, event='input', name='Bay 1') => {
        const target = select(`[data-bm-bay="${name}"]`)[0]; target.value=String(value); listeners[event]({target});
    };
    const finish = async (ok=true) => {
        const {payload:p,resolve} = pending.shift();
        if (ok) {
            if (p.mode_request) server.spotters.pending_mode = p.mode_request.mode;
            if (p.spotter) {
                if (p.spotter.mode) server.spotters.mode=p.spotter.mode;
                else {
                    const v=server.spotters.counts[p.spotter.metric], pos=p.spotter.position;
                    const desired=p.spotter.value ?? (v[pos]+p.spotter.delta);
                    const change=Math.min(Math.max(desired,0),99-(v.total-v[pos]))-v[pos];
                    v[pos]+=change; if(pos==='total') v.left=v.total; else v.total=v.left+v.right;
                }
            }
            if (p.waves) for(const [key,v] of Object.entries(p.waves)) server.spotters.counts[key].left=server.spotters.counts[key].total=v.count;
            if (p.open_bays !== undefined) server.spotters.counts.open.left=server.spotters.counts.open.total=p.open_bays;
            if (p.bay_statuses) for (const [name,status] of Object.entries(p.bay_statuses)) server.sides.east.bays.find(b=>b.bay_name===name).status=status;
            if (p.back_pickups) for (const [name,enabled] of Object.entries(p.back_pickups)) server.sides.east.bays.find(b=>b.bay_name===name).back_pickup=enabled;
            for (const bay of server.sides.east.bays) if (bay.status !== 'Overflowing') bay.back_pickup=false;
        }
        // Opaque server-derived values: the client must copy, never compute these.
        server.waves[0].left='LTU-'+calls.length;
        server.ballmat_routing.first='ROUTE-'+calls.length;
        api.apply(structuredClone(server)); resolve(ok); await settle();
    };
    const text = selector => { const e=select(selector)[0]; return 'bmInput' in e.dataset ? Number(e.value) : e.textContent; };
    const type = (key,position,value,event='change') => {
        const target=select(`[data-bm-value="${key}:${position}"]`)[0];
        target.value=String(value); listeners.input({target});
        if(event==='change') listeners.change({target});
    };
    const unlocked = () => {
        assert.equal(panel.attrs['aria-busy'],undefined);
        assert.ok(elements.filter(e=>e.dataset.bmStep || e.dataset.bmBay).every(e=>!e.disabled));
    };
    return {api,server,panel,calls,pending,click,bay,type,finish,text,unlocked,select,listeners,timers};
}

test('Back Pickup shares canonical refresh, respects Overflowing, and never freezes counts', async () => {
    const h=harness();
    const back=h.select('[data-bm-back="Bay 1"]')[0];
    assert.equal(back.disabled,true);
    h.bay(4,'change'); await h.finish();
    assert.equal(back.disabled,false);
    back.checked=true; h.listeners.change({target:back});
    h.unlocked(); h.click();
    assert.equal(h.calls.length,2); // status + in-flight Back Pickup; count queues normally.
    h.api.apply(structuredClone(h.server));
    assert.equal(back.checked,true); // Old poll cannot repaint the pending flag.
    await h.finish(); await h.finish();
    assert.equal(back.checked,true);
    h.bay(3,'input');
    assert.equal(back.checked,false); assert.equal(back.disabled,true);
    h.bay(3,'change'); await h.finish();
    assert.equal(h.server.sides.east.bays[0].back_pickup,false);
    assert.equal(h.text('[data-bm-value="first:total"]'),1);
});

test('numeric entry uses guarded absolute commands, preserves tap order and two-mode total is read-only', async () => {
    for(const mode of [1,2]) {
        const h=harness({mode});
        for(const key of ['first','second','open']) {
            const positions=mode===1 ? ['total'] : ['left','right'];
            for(const pos of positions) {
                h.type(key,pos,12); h.click(key,pos); h.type(key,pos,17); h.click(key,pos);
                while(h.pending.length) await h.finish();
                assert.equal(h.text(`[data-bm-value="${key}:${pos}"]`),18);
                assert.equal(h.server.spotters.counts[key][pos],18);
            }
        }
        assert.ok(h.calls.every(p=>p.spotter.expected_mode===mode && p.spotter.expected_mode_version===0));
        assert.deepEqual(h.calls.slice(0,4).map(p=>p.spotter.value ?? p.spotter.delta),[12,1,17,1]);
        if(mode===2) { const n=h.calls.length; h.type('first','total',90); assert.equal(h.calls.length,n); }
    }
});

test('rapid count burst holds derived LTU/routing until latest canonical response; single tap repaints immediately', async () => {
    const h=harness();
    h.click(); h.click(); h.click('open'); h.click('second');
    for(let i=0;i<3;i++) {
        await h.finish();
        assert.equal(h.text('[data-bm-unload="first"]'),0);
        assert.equal(h.text('[data-bm-route="first"]'),'-');
        h.api.apply(structuredClone(h.server));
        assert.equal(h.text('[data-bm-unload="first"]'),0);
    }
    await h.finish();
    assert.equal(h.text('[data-bm-unload="first"]'),h.server.waves[0].left);
    assert.equal(h.text('[data-bm-route="first"]'),h.server.ballmat_routing.first);
    assert.equal(h.text('[data-bm-value="first:total"]'),2);
    h.click(); await h.finish();
    assert.equal(h.text('[data-bm-unload="first"]'),'LTU-5');
    assert.equal(h.text('[data-bm-route="first"]'),'ROUTE-5');
});

test('numeric drafts survive responses, stale absolute generations are reconciled without replay', async () => {
    const h=harness(); h.click(); h.type('first','total',25,'input');
    await h.finish(); assert.equal(h.text('[data-bm-value="first:total"]'),25);
    h.listeners.change({target:h.select('[data-bm-value="first:total"]')[0]});
    h.type('second','total',15);
    h.server.spotters.mode=2; h.server.spotters.mode_version=1;
    await h.finish(false); // 409 carrying the canonical newer generation.
    assert.equal(h.calls.length,2);
    assert.equal(h.calls[1].spotter.value,25);
    assert.equal(h.calls[1].spotter.expected_mode_version,0);
    assert.equal(h.text('[data-bm-value="first:left"]'),1);
    assert.equal(h.text('[data-bm-unload="first"]'),h.server.waves[0].left);
});

test('failed write rebases later absolute entry and deltas without retrying; numeric limits remain bounded', async () => {
    const h=harness({mode:2});
    h.click('first','right'); h.type('first','right',20); h.click('first','right');
    await h.finish(false);
    assert.equal(h.text('[data-bm-value="first:right"]'),21);
    await h.finish(); await h.finish();
    assert.equal(h.server.spotters.counts.first.right,21);
    assert.equal(h.calls.length,3);
    h.type('first','left',100); await h.finish();
    assert.equal(h.server.spotters.counts.first.total,99);
    assert.equal(h.text('[data-bm-value="first:left"]'),78);
    const n=h.calls.length;
    h.type('first','left',''); h.type('first','left','1.5');
    assert.equal(h.calls.length,n);
    assert.equal(h.text('[data-bm-value="first:left"]'),78);
});

test('rapid deltas preserve every tap, optimistic values survive polls and intermediate replies', async () => {
    const h=harness({mode:2});
    for(let i=0;i<8;i++) h.click('first','right');
    assert.equal(h.calls.length,1);
    assert.equal(h.text('[data-bm-value="first:right"]'),8);
    h.api.apply(structuredClone(h.server)); h.unlocked();
    for(let i=0;i<8;i++) {
        await h.finish(); h.unlocked();
        assert.equal(h.text('[data-bm-value="first:right"]'),8);
    }
    assert.equal(h.server.spotters.counts.first.total,8);
    assert.equal(h.calls.length,8);
    assert.ok(h.calls.every(p=>p.spotter.delta===1 && !p.waves));
});

test('ordered plus/minus and boundary taps retain API semantics', async () => {
    const h=harness();
    [-1,1,1,-1,1].forEach(delta=>h.click('first','total',delta));
    assert.equal(h.text('[data-bm-value="first:total"]'),2);
    while(h.pending.length) await h.finish();
    assert.equal(h.server.spotters.counts.first.total,2);
    assert.deepEqual(h.calls.map(p=>p.spotter.delta),[-1,1,1,-1,1]);
});

test('bay input previews without saving, latest release survives older reply and polling', async () => {
    const h=harness(); h.bay(1); h.bay(2);
    assert.equal(h.calls.length,0);
    assert.equal(h.text('[data-bm-bay-value="Bay 1"]'),'Moderate');
    h.bay(2,'change'); h.bay(3,'change'); h.bay(4,'input');
    h.api.apply(structuredClone(h.server)); h.unlocked();
    await h.finish();
    assert.equal(h.text('[data-bm-bay-value="Bay 1"]'),'Overflowing');
    h.bay(4,'change'); await h.finish();
    assert.equal(h.text('[data-bm-bay-value="Bay 1"]'),'Overflowing');
    await h.finish(); h.unlocked();
    assert.equal(h.server.sides.east.bays[0].status,'Overflowing');
});

test('Google aggregate writes coalesce queued absolute values without blocking taps', async () => {
    const h=harness({available:false});
    for(let i=0;i<7;i++) h.click();
    assert.equal(h.text('[data-bm-value="first:total"]'),7);
    await h.finish();
    assert.equal(h.text('[data-bm-value="first:total"]'),7);
    await h.finish(); h.unlocked();
    assert.deepEqual(h.calls.map(p=>p.waves.first.count),[1,7]);
    assert.equal(h.server.spotters.counts.first.total,7);
});

test('failed write is not retried; later taps rebase and unrelated bay remains editable', async () => {
    const h=harness(); h.click(); h.click(); h.bay(4,'change');
    await h.finish(false); h.unlocked();
    assert.equal(h.text('[data-bm-value="first:total"]'),1);
    await h.finish(); await h.finish();
    assert.equal(h.server.spotters.counts.first.total,1);
    assert.equal(h.calls.length,3);
    assert.equal(h.server.sides.east.bays[0].status,'Overflowing');
});

test('mode buttons request only, keep counts/bays interactive and show canonical pending mode', async () => {
    const h=harness();
    h.listeners.click({target:h.select('[data-bm-mode="2"]')[0]});
    assert.equal(h.panel.attrs['aria-busy'],undefined);
    assert.equal(h.select('[data-bm-bay="Bay 1"]')[0].disabled,false);
    h.click(); h.bay(1,'change');
    await h.finish(); await h.finish(); await h.finish(); h.unlocked();
    assert.equal(h.server.spotters.mode,1);
    assert.equal(h.server.spotters.pending_mode,2);
    assert.equal(h.calls[0].mode_request.expected_mode_version,0);
    assert.equal(h.calls[0].spotter,undefined);
    assert.equal(h.text('[data-bm-mode-status]'),'1 SPOTTER · REQUEST 2 PENDING');
    assert.equal(h.calls.length,3);
});

test('failed bay save restores both the slider and readout without retry', async () => {
    const h=harness(); h.bay(4,'change'); await h.finish(false); h.unlocked();
    assert.equal(Number(h.select('[data-bm-bay="Bay 1"]')[0].value),0);
    assert.equal(h.text('[data-bm-bay-value="Bay 1"]'),'Empty');
    assert.equal(h.calls.length,1);
});

test('read-only permissions still prevent every mobile write', () => {
    const h=harness({canEdit:false}); h.click(); h.bay(3,'change');
    assert.equal(h.calls.length,0);
});

test('two devices converge on conductor mode change and discard old-generation queued taps', async () => {
    const devices = [harness(), harness()];
    for (const h of devices) {
        h.server.spotters.counts.first = {left:10,right:0,total:10};
        h.api.apply(structuredClone(h.server));
        h.click(); h.click(); h.click();
        const stale = structuredClone(h.server);
        h.server.spotters.mode=2; h.server.spotters.mode_version=1;
        h.api.apply(structuredClone(h.server));
        assert.equal(h.panel.dataset.mode,2);
        assert.equal(h.text('[data-bm-value="first:left"]'),10);
        assert.equal(h.text('[data-bm-value="first:right"]'),0);
        assert.equal(h.text('[data-bm-notice]'),'MODE CHANGED');
        h.api.apply(stale); // In-flight pre-change response cannot regress the mode.
        assert.equal(h.panel.dataset.mode,2);
        await h.finish(false); // 409 canonical response; no stale retry.
        assert.equal(h.calls.length,1);
        h.click('first','right'); h.click('first','right');
        await h.finish(); await h.finish();
        for (let n=0;n<3;n++) h.api.apply(structuredClone(h.server));
        assert.deepEqual(h.server.spotters.counts.first,{left:10,right:2,total:12});
        assert.equal(h.text('[data-bm-value="first:left"]'),10);
        assert.equal(h.text('[data-bm-value="first:right"]'),2);
        assert.equal(h.text('[data-bm-value="first:total"]'),12);
        assert.ok(h.calls.slice(1).every(p=>p.spotter.expected_mode===2 && p.spotter.expected_mode_version===1));
        h.timers.at(-1)(); assert.equal(h.select('[data-bm-notice]')[0].hidden,true);
        h.unlocked();
    }
});

test('generation change reconciles even when mode cycles back to the same value', async () => {
    const h=harness(); h.click(); h.click();
    h.server.spotters.mode_version=2;
    h.api.apply(structuredClone(h.server));
    await h.finish(false);
    assert.equal(h.calls.length,1);
    assert.equal(h.text('[data-bm-value="first:total"]'),0);
    h.click(); assert.equal(h.calls.at(-1).spotter.expected_mode_version,2);
});
