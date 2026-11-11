const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('attendance totals use confirmed snapshots and ignore older responses', async () => {
    function control() {
        return {value: '', dataset: {}, handlers: {}, addEventListener(name, listener) { this.handlers[name] = listener; }};
    }
    function employee(id) {
        const status = {...control(), options: []};
        const original = {value: `signed-${id}`};
        const output = {dataset: {}};
        const row = {...control(), dataset: {attendancePerson: id, attendanceStatus: ''}};
        const fields = {'select': status, 'input[name^="note_"]': control(), 'input[name^="original_"]': original,
            '[data-attendance-state]': output, '[data-attendance-retry]': control()};
        row.querySelector = selector => fields[selector];
        return {row, status, output};
    }

    const first = employee('1'), second = employee('2');
    const area = {dataset: {attendanceScopeHere: '8'}, textContent: '0'};
    const outside = {dataset: {attendanceScopeHere: '9'}, textContent: '—'};
    const roster = {...control(), dataset: {saveUrl: '/attendance/save'}};
    const fields = {'[data-attendance-search]': control(), '[data-attendance-clear]': control(),
        '[data-attendance-matches]': {}, '[data-attendance-empty]': {cells: [{}]}};
    roster.querySelector = selector => fields[selector];
    roster.querySelectorAll = selector => selector === '[data-attendance-person]' ? [first.row, second.row] : [];
    const pending = [];
    vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_attendance.js', 'utf8'), {
        document: {querySelector: () => roster, querySelectorAll: selector => selector === '[data-attendance-scope-here]' ? [area, outside] : []},
        fetch: () => new Promise(resolve => pending.push(resolve)), URLSearchParams, clearTimeout, setTimeout,
    });

    function change(person, value) {
        person.status.value = value;
        person.status.handlers.change();
    }
    async function respond(index, status, body) {
        pending[index]({ok: status === 200, status, json: async () => body});
        await new Promise(resolve => setImmediate(resolve));
    }
    const row = status => ({status, note: '', original: 'saved'});
    const snapshot = (revision, count) => ({revision, counts: {'8': count}});

    change(first, 'here');
    change(second, 'here');
    assert.equal(area.textContent, '0');
    await respond(1, 200, {rows: [row('here')], scope_here: snapshot('101', 2)});
    await respond(0, 200, {rows: [row('here')], scope_here: snapshot('100', 1)});
    assert.equal(area.textContent, 2);
    assert.equal(outside.textContent, '—');

    change(first, 'vacation');
    await respond(2, 503, {error: 'Unavailable'});
    assert.equal(area.textContent, 2);
    assert.match(first.output.textContent, /Unavailable/);
    change(second, 'vacation');
    await respond(3, 409, {error: 'Conflict', row: row('call_in'), scope_here: snapshot('102', 0)});
    assert.equal(area.textContent, 0);
    assert.match(second.output.textContent, /Changed by another supervisor/);
});
