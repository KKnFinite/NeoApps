const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
const signal = (version = 'v1', sort_date = '2026-09-09') => ({version, sort_date, sort_name: 'night'});

function harness() {
    const timers = new Map(), events = {}, hints = [], full = [];
    let timerId = 0, api;
    const listen = (name, fn) => { (events[name] ||= []).push(fn); };
    const context = {URL, document: {hidden: false, addEventListener: listen}, window: {
        location: {href: 'https://neo.test/neosektor/driver-routing?tv=1'},
        addEventListener: listen,
        setTimeout(fn, delay) { const id = ++timerId; timers.set(id, {fn, delay}); return id; },
        clearTimeout(id) { timers.delete(id); },
    }, fetch(url, options) { return new Promise(resolve => hints.push({url, options, resolve})); }};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/neosektor_routing_watch.js'), 'utf8'), context);
    api = context.window.NeoSektorRoutingWatch.create({versionUrl: '/version', initial: signal(),
        fetchState: () => new Promise((resolve, reject) => full.push({resolve, reject})).then(next => {
            if (next) api.observe(next);
        }),
    });
    api.setEnabled(true);
    return {api, timers, hints, full,
        async next() { assert.equal(timers.size, 1); [...timers.values()][0].fn(); await tick(); },
        async hint(version, ok = true) {
            hints.at(-1).resolve({ok, json: async () => ({ok, ...signal(version)})}); await tick();
        },
        async finish(next) { full.at(-1).resolve(next); await tick(); },
        async event(name, hidden = false) {
            context.document.hidden = hidden; (events[name] || []).forEach(fn => fn()); await tick();
        },
    };
}

test('unchanged 1.5-second hints never load full state; changed signal fetches once', async () => {
    const h = harness();
    for (let i = 0; i < 3; i++) {
        assert.equal([...h.timers.values()][0].delay, 1500);
        await h.next(); await h.hint('v1');
    }
    assert.equal(h.full.length, 0);
    assert.equal(h.hints[0].url.searchParams.get('sort_date'), '2026-09-09');
    assert.equal(h.hints[0].options.cache, 'no-store');
    await h.next(); await h.hint('v2');
    assert.equal(h.full.length, 1);
    await h.finish(signal('v2'));
    await h.next(); await h.hint('v2');
    assert.equal(h.full.length, 1);
});

test('normal safety-net fetch is single-flight and still runs for unchanged hints (Block-In/timers)', async () => {
    const h = harness();
    const a = h.api.refresh(), b = h.api.refresh(); await tick();
    assert.equal(a, b); assert.equal(h.full.length, 1);
    await h.finish(signal()); await a;
    await h.next(); await h.hint('v1');
    const next = h.api.refresh(); await tick();
    assert.equal(h.full.length, 2); await h.finish(signal()); await next;
});

test('concurrent safety-net response consumes the observed version without duplicate full fetch', async () => {
    const h = harness();
    const normal = h.api.refresh(); await tick();
    await h.next(); await h.hint('v2');
    assert.equal(h.full.length, 1);
    await h.finish(signal('v2')); await normal;
    assert.equal(h.full.length, 1);
    await h.next(); await h.hint('v2');
    assert.equal(h.full.length, 1);
});

test('safety-net snapshot predating observed commit gets one immediate follow-up, never parallel', async () => {
    const h = harness();
    const normal = h.api.refresh(); await tick();
    await h.next(); await h.hint('v2');
    assert.equal(h.full.length, 1);
    await h.finish(signal('v1')); await normal;
    assert.equal(h.full.length, 2);
    await h.finish(signal('v2'));
    await h.next(); await h.hint('v2');
    assert.equal(h.full.length, 2);
});

test('hidden/paused/pagehide stop hints; visibility resumes immediately; stale scope cannot trigger', async () => {
    const h = harness();
    await h.next(); await h.event('visibilitychange', true); await h.hint('v2');
    assert.equal(h.full.length, 0); assert.equal(h.timers.size, 0);
    await h.event('visibilitychange', false);
    assert.equal(h.hints.length, 2);
    h.api.observe(signal('new-sort', '2026-09-10'));
    await h.hint('v2'); assert.equal(h.full.length, 0);
    h.api.setEnabled(false); assert.equal(h.timers.size, 0);
    await h.event('visibilitychange', false); assert.equal(h.hints.length, 2);
    h.api.setEnabled(true); await h.event('pagehide'); assert.equal(h.timers.size, 0);
    await h.event('pageshow'); assert.equal(h.timers.size, 1);
});

test('no-op canonical response acknowledges a hint once; hint failures do not cause full fetch', async () => {
    const h = harness();
    await h.next(); await h.hint('v2'); await h.finish(null);
    await h.next(); await h.hint('v2'); assert.equal(h.full.length, 1);
    await h.next(); await h.hint('v3', false); assert.equal(h.full.length, 1);
    assert.equal([...h.timers.values()][0].delay, 5000);
});

test('failed full fetch keeps version unacknowledged for later polling', async () => {
    const h = harness();
    await h.next(); await h.hint('v2');
    h.full[0].reject(new Error('offline')); await tick();
    assert.equal(h.full.length, 1);
    await h.next(); await h.hint('v2'); assert.equal(h.full.length, 2);
    await h.finish(signal('v2'));
});
