const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const repo = path.join(__dirname, '../..');
const read = file => fs.readFileSync(path.join(repo, file), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const snapshot = (first = 'auto', second = 'auto') => ({
    waves: [], sides: {}, operational_settings: {}, refresh: {auto_refresh_enabled: true},
    routing: {west_offset: 0, routes: {first: {override: first}, second: {override: second}}},
});

// Run the actual inline controller plus the shared transport, with controlled
// network completion order. The DOM seam retains range values and visual tracks.
function harness() {
    const requests = [], listeners = {}, timers = new Map();
    let nextTimer = 0, poll;
    const input = (selector, dataset, value) => ({
        dataset, value, min: '0', max: '20', disabled: false, tagName: 'INPUT',
        track: {setAttribute(name, value) { this[name] = value; }},
        matches: query => query.split(', ').includes(selector),
        closest() { return this.track; },
    });
    const overrides = ['first', 'second'].map(wave => input('[data-tunnel-route-override-input]',
        {tunnelRouteOverrideInput: wave, neosektorEditKey: `routing:${wave}_override`}, '1'));
    const offset = input('[data-tunnel-offset-input]', {neosektorEditKey: 'offset'}, '0');
    const root = {
        dataset: {canEdit: 'true', stateUrl: '/state', settingsUrl: '/settings', offsetUrl: '/offset'},
        querySelector: query => query === '[data-tunnel-offset-input]' ? offset : null,
        querySelectorAll: query => query === '[data-tunnel-route-override-input]' ? overrides : [],
        addEventListener(name, callback) { (listeners[name] ||= []).push(callback); },
    };
    const context = {URL, document: {
        activeElement: null, querySelector: query => query === '[data-tunnel-conductor]' ? root : null,
    }, window: {
        location: {href: 'https://neo.test/neosektor/tunnel-conductor'},
        setTimeout(fn) { const id = ++nextTimer; timers.set(id, fn); return id; },
        clearTimeout(id) { timers.delete(id); },
        NeoLiveUpdates: {create(options) { poll = options.poll; return {setServerStatus() {}}; }},
    }, fetch(url, options) {
        return new Promise(resolve => requests.push({url: String(url), options, resolve,
            body: options.body ? JSON.parse(options.body) : null}));
    }};
    vm.createContext(context);
    vm.runInContext(read('app/static/js/neosektor_live.js'), context);
    vm.runInContext(read('app/static/js/neosektor_spotter_modes.js'), context);
    const source = read('app/templates/neonodes/neosektor/tunnel_conductor.html')
        .match(/<script>\s*([\s\S]*?)<\/script>/)[1]
        .replace(/{{ state.refresh\|tojson }}/g, JSON.stringify(snapshot().refresh))
        .replace(/{{ state.refresh.live_screen_refresh_interval_ms }}/g, '5000')
        .replace(/{{ state\|tojson }}/g, JSON.stringify(snapshot()));
    vm.runInContext(source, context);
    const event = (name, target) => (listeners[name] || []).forEach(fn => fn({target}));
    return {requests, overrides, offset, poll: () => poll(),
        change(wave, value, name = 'change') {
            const target = overrides[wave]; target.value = value; event(name, target);
        },
        offsetChange() { offset.value = '3'; event('change', offset); },
        async finish(index, state, ok = true) {
            requests[index].resolve({ok, json: async () => ({ok, state, revision: `r${index}`})});
            await tick();
        },
    };
}

for (const manual of ['west', 'east']) {
    test(`${manual} -> AUTO stays canonical through mutation and subsequent poll`, async () => {
        const h = harness(), value = manual === 'west' ? '2' : '0';
        h.change(0, value); await h.finish(0, snapshot(manual));
        assert.equal(h.requests[0].body.first_override, manual);
        h.change(0, '1'); await h.finish(1, snapshot());
        assert.equal(h.requests[1].body.first_override, 'auto');
        const polling = h.poll(); await h.finish(2, snapshot()); await polling;
        assert.equal(h.overrides[0].value, '1');
        assert.equal(h.overrides[0].track['data-route-position'], '1');
    });
}

test('older offset mutation response cannot repaint a newer successful AUTO override', async () => {
    const h = harness();
    h.change(0, '2'); await h.finish(0, snapshot('west'));
    h.offsetChange(); // Snapshot captured while WEST was canonical; response delayed.
    h.change(0, '1'); await h.finish(2, snapshot());
    await h.finish(1, snapshot('west'));
    assert.equal(h.overrides[0].value, '1');
    assert.equal(h.overrides[0].track['data-route-position'], '1');
});

test('slider input remains pending until release, even if an older save completes', async () => {
    const h = harness();
    h.change(0, '2');
    h.change(0, '1', 'input'); // Dragging before the committed change event.
    await h.finish(0, snapshot('west'));
    assert.equal(h.overrides[0].value, '1');
    assert.equal(h.overrides[0].track['data-route-position'], '1');
    assert.equal(h.requests.length, 1, 'movement does not write');
    h.change(0, '1'); await h.finish(1, snapshot());
});

test('stale poll arriving after AUTO success is discarded', async () => {
    const h = harness();
    h.change(0, '2'); await h.finish(0, snapshot('west'));
    const polling = h.poll();
    h.change(0, '1'); await h.finish(2, snapshot());
    await h.finish(1, snapshot('west')); await polling;
    assert.equal(h.overrides[0].value, '1');
});

test('rapid settings changes drain in order and preserve independent wave intentions', async () => {
    const h = harness();
    h.change(0, '2'); h.change(0, '1'); h.change(1, '0');
    assert.equal(h.requests.length, 1, 'one settings write in flight');
    await h.finish(0, snapshot('west'));
    assert.equal(h.overrides[0].value, '1');
    assert.equal(h.overrides[1].value, '0');
    assert.equal(h.requests.length, 2);
    assert.equal(h.requests[1].body.first_override, 'auto');
    assert.equal(h.requests[1].body.second_override, 'east');
    await h.finish(1, snapshot('auto', 'east'));
    assert.equal(h.overrides[0].value, '1');
    assert.equal(h.overrides[1].value, '0');
});

test('a poll started during a queued AUTO save cannot repaint its later success', async () => {
    const h = harness();
    h.change(1, '0'); h.change(1, '1');
    await h.finish(0, snapshot('auto', 'east'));
    const polling = h.poll(); // EAST still durable; queued AUTO has not replied.
    await h.finish(1, snapshot());
    await h.finish(2, snapshot('auto', 'east')); await polling;
    assert.equal(h.overrides[1].value, '1');
    assert.equal(h.overrides[1].track['data-route-position'], '1');
});

test('failed settings save reconciles by GET without retrying the write', async () => {
    const h = harness();
    h.change(0, '2'); await h.finish(0, snapshot(), false);
    assert.equal(h.requests.length, 2);
    assert.equal(h.requests[1].body, null);
    await h.finish(1, snapshot());
    assert.equal(h.overrides[0].value, '1');
    assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 1);
});
