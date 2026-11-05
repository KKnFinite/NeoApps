const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const settle = () => new Promise(resolve => setImmediate(resolve));

function harness() {
    let now = 100000, id = 0, accepted = -Infinity, version = 'v1', watch;
    const timers = new Map(), requests = [], events = {};
    const listen = (name, fn) => (events[name] ||= []).push(fn);
    const context = {URL, Date: {now: () => now}, document: {hidden: false,
        addEventListener: listen, removeEventListener() {}}, window: {
        location: {href: 'https://neo.test/neosektor/driver-routing'},
        addEventListener: listen,
        setTimeout(fn, delay) { timers.set(++id, {fn, at: now + delay}); return id; },
        clearTimeout(key) { timers.delete(key); },
    }, fetch: async () => ({ok: true, json: async () => ({ok: true, version})})};
    for (const file of ['live_updates.js', 'neosektor_routing_watch.js']) {
        vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../app/static/js', file), 'utf8'), context);
    }
    const controller = context.window.NeoLiveUpdates.create({intervalMs: 5000,
        continuousWhileVisible: true, immediate: false, poll: () => watch.refresh()});
    const signal = () => ({version, sort_date: '2026-11-04', sort_name: 'night'});
    watch = context.window.NeoSektorRoutingWatch.create({versionUrl: '/version', initial: signal(),
        requestRefresh: () => controller.refreshNow(),
        fetchState: async () => {
            const status = now - accepted < 5000 ? 429 : 200;
            requests.push({at: now, status});
            if (status === 429) throw Object.assign(new Error('cooling down'), {retryAt: accepted + 5000});
            accepted = now; watch.observe(signal());
        },
    });
    controller.setEnabled(true); watch.setEnabled(true);
    return {controller, watch, requests, events, context,
        change() { version += 'x'; },
        async advance(ms) {
            const target = now + ms;
            for (;;) {
                const due = [...timers].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
                if (!due) break;
                now = due[1].at; timers.delete(due[0]); due[1].fn(); await settle();
            }
            now = target; await settle();
        },
    };
}

test('fast committed hints share the real controller cadence without avoidable 429s', async () => {
    const h = harness();
    await h.controller.refreshNow();
    for (let i = 0; i < 3; i++) {
        await h.advance(1000); h.change(); await h.advance(4000);
    }
    assert.equal(h.requests.length, 4);
    assert.deepEqual(h.requests.map(r => r.status), [200, 200, 200, 200]);
    assert.deepEqual(h.requests.map(r => r.at), [100000, 105000, 110000, 115000]);
});

test('a hint cannot bypass Retry-After or acknowledge an unsuccessful canonical fetch', async () => {
    const h = harness();
    await h.controller.refreshNow();
    h.controller.retryAt = 120000; h.change();
    await h.advance(19000);
    assert.equal(h.requests.length, 1);
    // The next existing 1.5-second hint may resume after the deadline;
    // it must not reset/acknowledge the outstanding invalidation forever.
    await h.advance(2000);
    assert.equal(h.requests.length, 2);
    assert.equal(h.requests[1].at, 121000);
});

test('hints respect hidden/disabled screens and resume through the same controller', async () => {
    const h = harness(); await h.controller.refreshNow();
    h.controller.setEnabled(false); h.watch.setEnabled(false); h.change();
    await h.advance(6000); assert.equal(h.requests.length, 1);
    h.controller.setEnabled(true); h.watch.setEnabled(true);
    h.context.document.hidden = true;
    await h.advance(6000); assert.equal(h.requests.length, 1);
    h.context.document.hidden = false;
    h.events.visibilitychange.forEach(fn => fn()); await settle();
    assert.equal(h.requests.length, 2);
});
