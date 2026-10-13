const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const page = fs.readFileSync('app/templates/neomotherbrain/sort_timeline.html', 'utf8');
const begin = page.indexOf('<script>\n(() => {');
const script = page.slice(begin + '<script>'.length, page.indexOf('</script>', begin));

function setup() {
    const events = {}, calls = [], timers = [];
    const status = {dataset: {}, textContent: ''};
    const clock = {value: '', matches(selector) {return selector.includes('[data-military-time]');},
        setCustomValidity(value) {this.validity = value;}, removeAttribute() {}, setAttribute() {}};
    const form = {addEventListener(name, handler) {events[name] = handler;},
        querySelectorAll(selector) {return selector === '[data-military-time]' ? [clock] : [];}};
    const document = {activeElement: clock, addEventListener() {},
        querySelector(selector) {return selector === '[data-sort-timeline-autosave]' ? form : status;},
        querySelectorAll() {return [];}};
    const window = {location: {href: '/motherbrain/sort-timeline'},
        clearTimeout() {}, setTimeout(handler) {timers.push(handler); return timers.length;}};
    const context = {document, window, AbortController,
        FormData: class {constructor() {this.time = clock.value;}},
        fetch(_url, options) {calls.push(options.body.time); return Promise.resolve({ok:true,json:async()=>({})});}};
    vm.runInNewContext(fs.readFileSync('app/static/js/clock_time.js', 'utf8'), context);
    vm.runInNewContext(script, context);
    return {clock, document, events, calls, timers, status};
}

test('Sort Timeline never autosaves partial mobile input; blur sends normalized HH:MM', async () => {
    const h = setup();
    for (const partial of ['0', '05', '050']) {
        h.clock.value = partial;
        h.events.input({target:h.clock});
        assert.equal(h.timers.length, 0);
        assert.equal(h.calls.length, 0);
    }
    h.clock.value = '0500';
    h.events.input({target:h.clock});
    assert.equal(h.clock.value, '0500');
    h.document.activeElement = null;
    h.events.focusout({target:h.clock});
    h.timers.pop()();
    await new Promise(setImmediate);
    assert.equal(h.clock.value, '05:00');
    assert.deepEqual(h.calls, ['05:00']);
    assert.equal(h.status.textContent, 'SAVED');
});

test('Sort Timeline invalid time never reaches autosave; dynamic Special Poll uses same input', () => {
    const h = setup();
    h.clock.value = '2400'; h.document.activeElement = null;
    h.events.focusout({target:h.clock});
    assert.equal(h.calls.length, 0);
    assert.equal(h.timers.length, 0);
    assert.match(h.status.textContent, /INVALID TIME/);
    const dynamic = page.slice(page.indexOf('const specialRowMarkup'), page.indexOf('const ensureSingleBlankSpecialRow'));
    assert.match(dynamic, /inputmode="numeric"/);
    assert.match(dynamic, /data-military-time/);
});
