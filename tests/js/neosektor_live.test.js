const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '../..');
const source = name => fs.readFileSync(path.join(root, `app/templates/neonodes/neosektor/${name}.html`), 'utf8');
const helper = fs.readFileSync(path.join(root, 'app/static/js/neosektor_live.js'), 'utf8');
const names = ['live_counts', 'tunnel_conductor', 'ballmat', 'driver_routing', 'discharge'];

// Execute each real template's poll wiring, not a copy of its guards/callbacks.
function harness(name) {
    const calls = [], events = [];
    let finish, fail;
    const context = {
        URL, window: {location: {href: 'https://neo.test/neosektor/'}},
        stateUrl: '/state?side=east', currentRevision: 'r1', mutationEpoch: 0,
        fetch(url, options) {
            calls.push({url, options});
            return new Promise((resolve, reject) => { finish = resolve; fail = reject; });
        },
        setRefreshStatus: status => events.push(['status', status]),
        applyState: state => events.push(['state', state]),
        renderQueue: rows => events.push(['queue', rows]),
        renderSelected: rows => events.push(['selected', rows]),
    };
    vm.createContext(context);
    vm.runInContext(helper, context);
    const pollName = name === 'driver_routing' ? 'fetchRoutingState' : 'refreshState';
    const poll = source(name).match(new RegExp(`    const ${pollName} = async \\(\\) => \\{[\\s\\S]*?\\n    \\};`))[0];
    vm.runInContext(`${poll}\nthis.poll = ${pollName};`, context);
    return {context, calls, events, poll: context.poll,
        finish(payload, ok = true, jsonError = false) {
            finish({ok, json: async () => {
                if (jsonError) throw new SyntaxError('Invalid JSON');
                return payload;
            }});
        },
        fail(error) { fail(error); },
    };
}

for (const name of names) {
    test(`${name}: revision transport, status order and screen renderer`, async () => {
        const h = harness(name), pending = h.poll();
        assert.equal(h.calls.length, 1);
        assert.equal(h.calls[0].url.searchParams.get('revision'), 'r1');
        assert.equal(h.calls[0].url.searchParams.get('side'), 'east');
        assert.equal(h.calls[0].options.cache, 'no-store');
        if (name === 'live_counts') {
            assert.equal(h.calls[0].options.headers.Accept, 'application/json');
            assert.equal(h.calls[0].options.credentials, undefined);
        } else assert.equal(h.calls[0].options.credentials, 'same-origin');
        const refresh = {auto_refresh_enabled: true}, state = {refresh, requests: [{id: 7}]};
        h.finish({ok: true, revision: 'r2', state});
        await pending;
        assert.equal(h.context.currentRevision, 'r2');
        assert.deepEqual(h.events, name === 'discharge'
            ? [['status', refresh], ['queue', state.requests], ['selected', state.requests]]
            : [['status', refresh], ['state', state]]);
    });

    test(`${name}: paused/unchanged polls update revision/status without rendering`, async () => {
        for (const payload of [
            {ok: true, changed: false, revision: 'r2', refresh: {}},
            {ok: true, revision: 'r2', refresh: {auto_refresh_enabled: false}, state: {refresh: {auto_refresh_enabled: true}}},
        ]) {
            const h = harness(name), pending = h.poll();
            h.finish(payload); await pending;
            assert.equal(h.context.currentRevision, 'r2');
            assert.deepEqual(h.events, [['status', payload.refresh]]);
        }
    });
}

test('HTTP, application, JSON, network and missing-state failures propagate without retry', async () => {
    for (const name of names) {
        for (const kind of ['http', 'application', 'json', 'network', 'state']) {
            const h = harness(name), pending = h.poll();
            if (kind === 'network') h.fail(new Error('offline'));
            else if (kind === 'json') h.finish(null, true, true);
            else h.finish({ok: kind !== 'application'}, kind !== 'http');
            await assert.rejects(pending);
            assert.equal(h.calls.length, 1);
            assert.equal(h.events.some(([event]) => ['state', 'queue', 'selected'].includes(event)), false);
        }
    }
});

test('Tunnel and Ballmat reject stale polls before revision, status or state changes', async () => {
    for (const name of ['tunnel_conductor', 'ballmat']) {
        for (const changed of ['currentRevision', 'mutationEpoch']) {
            const h = harness(name), pending = h.poll();
            h.context[changed] = changed === 'currentRevision' ? 'local' : 1;
            h.finish({ok: true, revision: 'stale', state: {}}); await pending;
            assert.equal(h.context.currentRevision, changed === 'currentRevision' ? 'local' : 'r1');
            assert.deepEqual(h.events, []);
        }
    }
});

test('missing revision retains response-time current revision', async () => {
    const h = harness('live_counts'), pending = h.poll();
    h.context.currentRevision = 'newer';
    h.finish({ok: true, changed: false}); await pending;
    assert.equal(h.context.currentRevision, 'newer');
});

test('status uses safe text nodes and preserves each page separator', () => {
    const context = {window: {}, document: {
        createTextNode: text => ({textContent: text}), createElement: () => ({}),
    }};
    vm.runInNewContext(helper, context);
    const root = {dataset: {}}, paused = {
        replaceChildren(...nodes) { this.children = nodes; },
        appendChild(node) { this.children.push(node); },
    };
    for (const separator of [' · ', ' - ']) {
        context.window.NeoSektorLive.renderStatus(root, {
            auto_refresh_enabled: false, operation_label: '<night>', window_label: '22-04',
        }, paused, separator);
        assert.equal(root.dataset.refreshActive, 'false');
        assert.equal(paused.hidden, false);
        assert.equal(paused.children[0].textContent, 'Live updates off - outside Ops window');
        assert.equal(paused.children[1].textContent, `<night>${separator}22-04`);
    }
    context.window.NeoSektorLive.renderStatus(root, {}, paused);
    assert.equal(root.dataset.refreshActive, 'true');
    assert.equal(paused.hidden, true);
    assert.equal(paused.children.length, 1);
});

test('Driver keeps controller options and next-window/visibility lifecycle', async () => {
    const src = source('driver_routing');
    const callbacks = {}, timers = new Map(), polls = [];
    let timerId = 0, controllerOptions, status;
    const context = {URL, root: {dataset: {}}, document: {
        hidden: false, addEventListener: (name, fn) => { callbacks[name] = fn; },
    }, window: {
        setTimeout(fn, delay) {
            const id = ++timerId;
            timers.set(id, {fn() { timers.delete(id); fn(); }, delay});
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
        addEventListener: (name, fn) => { callbacks[name] = fn; },
        NeoLiveUpdates: {create(options) { controllerOptions = options; return {setServerStatus(s) { status = s; }}; }},
    }, routingWatch: null, refreshState: async () => { polls.push('poll'); }};
    vm.createContext(context); vm.runInContext(helper, context);
    const wake = src.slice(src.indexOf('    let nextWindowWakeTimer'), src.indexOf('    const applyRoute'));
    const setter = src.match(/    const setRefreshStatus = \(refresh\) => \{[\s\S]*?\n    \};/)[0].replace(/{{.*?}}/g, '5000');
    const listeners = src.slice(src.indexOf('    document.addEventListener("visibilitychange"'), src.lastIndexOf('})();'));
    vm.runInContext(`let refreshController = null; ${wake}\n${setter}\n${listeners}\nthis.setStatus = setRefreshStatus;`, context);
    context.setStatus({auto_refresh_enabled: false, next_check_seconds: 30});
    assert.equal(controllerOptions.intervalMs, 5000);
    assert.equal(controllerOptions.continuousWhileVisible, true);
    assert.equal(controllerOptions.immediate, false);
    assert.equal(status.auto_refresh_enabled, false);
    assert.equal(timers.size, 1);
    const timer = [...timers.values()][0];
    assert.equal(timer.delay, 30000);
    timer.fn(); await Promise.resolve(); assert.equal(polls.length, 1);
    context.document.hidden = true; callbacks.visibilitychange(); assert.equal(timers.size, 0);
    context.document.hidden = false; callbacks.visibilitychange(); await Promise.resolve();
    assert.equal(polls.length, 2);
    context.setStatus({auto_refresh_enabled: true}); assert.equal(timers.size, 0);
    context.setStatus({auto_refresh_enabled: false, next_check_seconds: 30});
    callbacks.pagehide(); assert.equal(timers.size, 0);
});
