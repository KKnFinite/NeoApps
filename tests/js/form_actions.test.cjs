'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const vm = require('node:vm'), fs = require('node:fs');
const source = fs.readFileSync('app/static/js/form_actions.js', 'utf8');
function harness() {
    const events = {}, fields = [], confirms = [];
    class Form {
        constructor() { this.dataset = {}; }
        querySelector() { return fields[0] || null; }
        append(field) { fields.push(field); }
        requestSubmit() { this.submitted = (this.submitted || 0) + 1; }
    }
    class Select { matches() { return true; } }
    let allowed = true;
    vm.runInNewContext(source, {
        window: {confirm(message) { confirms.push(message); return allowed; }},
        HTMLFormElement: Form, HTMLSelectElement: Select,
        document: {addEventListener(name, listener) { events[name] = listener; }, createElement() { return {}; }}
    });
    const event = form => ({target: form, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
        stopImmediatePropagation() { this.stopped = true; }});
    return {events, Form, Select, event, fields, confirms, reject() {allowed = false;}};
}
test('canceled confirmation prevents native and node mutation listeners', () => {
    const h = harness(), form = new h.Form(), e = h.event(form);
    form.dataset.confirmMessage = 'Confirm existing action'; h.reject(); h.events.submit(e);
    assert.equal(e.defaultPrevented, true); assert.equal(e.stopped, true);
    assert.equal(h.fields.length, 0);
});
test('capacity override retains original submitter and adds one explicit flag', () => {
    const h = harness(), form = new h.Form(), e = h.event(form);
    const submitter = {name: 'decision', value: 'approve', dataset: {confirmMessage: 'Approve override'}, hasAttribute: () => true};
    e.submitter = submitter; h.events.submit(e); h.events.submit(e);
    assert.equal(e.submitter, submitter); assert.equal(e.defaultPrevented, false);
    assert.equal(h.fields.length, 1); assert.equal(h.fields[0].name, 'capacity_override');
    assert.equal(h.fields[0].value, '1'); assert.equal(h.fields[0].type, 'hidden');
});
test('canceled or already rejected action cannot create an override', () => {
    const h = harness(), e = h.event(new h.Form());
    e.submitter = {dataset: {confirmMessage: 'Override'}, hasAttribute: () => true};
    h.reject(); h.events.submit(e); assert.equal(h.fields.length, 0);
    const already = h.event(new h.Form()); already.defaultPrevented = true;
    h.events.submit(already); assert.equal(h.confirms.length, 1);
});
test('sort selection uses normal requestSubmit and does not replay an action', () => {
    const h = harness(), select = new h.Select(); select.form = new h.Form();
    h.events.change({target: select}); assert.equal(select.form.submitted, 1);
});
test('removing an unsaved vacation row is a DOM-only action', () => {
    const h = harness(); let removed = 0;
    h.events.click({target: {closest: () => ({closest: () => ({remove() { removed++; }})})}});
    assert.equal(removed, 1);
});
