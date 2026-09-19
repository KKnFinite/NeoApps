const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('management picker matches Shi case-insensitively, retains multiple IDs and excludes duplicates/ineligible units', () => {
    const element = () => ({ children: [], value: '', hidden: true, events: {}, dataset: {},
        append(...items) { this.children.push(...items); }, replaceChildren() { this.children = []; },
        setAttribute() {}, addEventListener(name, fn) { this.events[name] = fn; } });
    const classification = element(), search = element(), results = element(), chosen = element(), primary = element();
    classification.value = 'full_time_supervisor';
    const picker = { querySelector: s => ({ '[data-assignment-search]': search, '[data-assignment-results]': results, '[data-assignment-selected]': chosen })[s] };
    const catalog = [['1', 'department', 'Shift'], ['2', 'department', 'Outbound'], ['3', 'work_area', 'Shift Door']].map(([unitId, unitType, unitName]) => ({
        dataset: { unitId, unitType, unitName, unitPath: 'Night / Ramp / ' + unitName }, hasAttribute: () => false,
    }));
    const form = { querySelector: s => ({ '[data-person-classification]': classification, '[data-assignment-picker]': picker, '[data-twenty-c-primary]': primary })[s], querySelectorAll: () => catalog };
    const source = fs.readFileSync('app/templates/neostaffing/people.html', 'utf8');
    const start = source.indexOf("document.querySelectorAll('[data-people-smart-form]')");
    const end = source.indexOf('})();</script>', start);
    assert.ok(start > 0 && end > start);
    vm.runInNewContext(source.slice(start, end), { document: { querySelectorAll: () => [form], createElement: element, addEventListener() {} } });
    for (const query of ['Shi', 'sHI', 'hif']) {
        search.value = query; search.events.input();
        assert.equal(results.children.length, 1);
        assert.equal(results.children[0].children[0].textContent, 'Shift');
    }
    results.children[0].events.click();
    search.value = 'Shi'; search.events.input();
    assert.equal(results.children[0].textContent, 'No matching assignments');
    search.value = 'out'; search.events.input(); results.children[0].events.click();
    assert.deepEqual(chosen.children.map(row => row.children[2].value), ['1', '2']);
    classification.value = 'part_time_supervisor'; classification.events.change();
    assert.equal(chosen.children.length, 0);
    search.value = 'Shi'; search.events.input();
    assert.equal(results.children[0].children[0].textContent, 'Shift Door');
});
