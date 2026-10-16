const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/neoscorpion_tail_notes.js', 'utf8');
function harness() {
    const events = {}, requests = [];
    const make = (tail, value) => {
        const status = {textContent: '', classList: {add(){}, remove(){}}};
        const label = {textContent: ''};
        const input = {value, defaultValue: value, dispatchEvent(){}};
        const buttons = [{disabled: false}, {disabled: false}];
        return {action: '/tail-notes', dataset: {}, elements: {notes: input, tail_number: {value: tail}, expected_version: {value: '0'}},
            buttons, status, label, querySelector: () => status, querySelectorAll: () => buttons,
            closest: () => ({querySelector: () => label})};
    };
    const form = make('N432UP', 'saved'), duplicate = make('N432UP', 'saved'), other = make('N433UP', 'other');
    const root = {addEventListener: (name, callback) => events[name] = callback,
        querySelectorAll: () => [form, duplicate, other]};
    const context = {document: {querySelector: () => root}, Event: class {},
        FormData: class { constructor(form) { this.tail = form.elements.tail_number.value; this.notes = form.elements.notes.value; } },
        fetch: (url, options) => new Promise(resolve => requests.push({url, options, resolve}))};
    vm.runInNewContext(source, context);
    const submit = () => events.submit({target: {closest: () => form}, preventDefault(){}});
    return {form, duplicate, other, requests, submit, events,
        resolve(ok = true, notes = 'new', version = 1) {requests.at(-1).resolve({ok, json: async () => ok
            ? {ok: true, note: {tail: 'N432UP', notes, version, editor: 'Dispatcher', updated_at: '2026-10-16T06:00:00Z'}}
            : {ok: false, error: 'Tail Notes changed in another window.'}});}};
}
test('saved notes reconcile the same tail only and retain independent drafts', async () => {
    const h = harness(); h.form.elements.notes.value = 'new';
    h.duplicate.elements.notes.value = 'independent draft';
    const saving = h.submit();
    assert.equal(h.requests[0].options.body.tail, 'N432UP');
    assert.equal(h.form.buttons[0].disabled, true);
    h.resolve(); await saving;
    assert.equal(h.form.elements.notes.defaultValue, 'new');
    assert.equal(h.form.elements.expected_version.value, '1');
    assert.equal(h.duplicate.elements.notes.value, 'independent draft');
    assert.equal(h.other.elements.notes.value, 'other');
    assert.equal(h.form.status.textContent, 'SAVED');
});
test('typing while a save is in flight remains an unsaved draft and cannot be overwritten', async () => {
    const h = harness(); h.form.elements.notes.value = 'new';
    const saving = h.submit(); h.form.elements.notes.value = 'newer unfinished entry';
    h.resolve(); await saving;
    assert.equal(h.form.elements.notes.value, 'newer unfinished entry');
    assert.equal(h.form.elements.notes.defaultValue, 'new');
    assert.match(h.form.status.textContent, /new draft unsaved/);
    assert.equal(h.duplicate.elements.notes.value, 'new');
});
test('a conflict retains draft and original version; a second in-flight submit is ignored', async () => {
    const h = harness(); h.form.elements.notes.value = 'my draft';
    const saving = h.submit(); await h.submit();
    assert.equal(h.requests.length, 1); h.resolve(false); await saving;
    assert.equal(h.form.elements.notes.value, 'my draft');
    assert.equal(h.form.elements.expected_version.value, '0');
    assert.equal(h.form.elements.notes.defaultValue, 'saved');
    assert.match(h.form.status.textContent, /changed in another window/);
    assert.equal(h.form.buttons[0].disabled, false);
});
test('clear submits an empty tail note through the same versioned save path', async () => {
    const h = harness(); let saving;
    h.form.requestSubmit = () => { saving = h.submit(); };
    h.events.click({target: {closest: () => ({closest: () => h.form})}});
    assert.equal(h.requests[0].options.body.notes, '');
    h.resolve(true, ''); await saving;
    assert.equal(h.form.elements.notes.defaultValue, '');
    assert.equal(h.form.elements.expected_version.value, '1');
});
