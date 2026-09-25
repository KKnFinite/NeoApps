const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
    const element = () => ({ children: [], value: '', hidden: true, events: {}, dataset: {},
        append(...items) { this.children.push(...items); }, replaceChildren() { this.children = []; },
        setAttribute() {}, addEventListener(name, fn) { this.events[name] = fn; } });
    const classification = element(), search = element(), results = element(), chosen = element();
    const select = values => {const control = element(); control.options=values.map(value=>({value,dataset:{}}));
        Object.defineProperty(control,'selectedOptions',{get:()=>[control.options.find(o=>o.value===control.value)]});return control;};
    const primarySelect=select(['','1:10','1:11','2:12']), secondarySelect=select(['','1:10','1:11','2:12']);
    const primary=element(),secondary=element(),reporting=element(),reportingLabel=element(),reportingSelect=select(['','10','20']);
    reportingSelect.options[1].dataset.reportingClassification='full_time_supervisor';
    reportingSelect.options[2].dataset.reportingClassification='manager';
    primary.querySelector=()=>primarySelect; secondary.querySelector=()=>secondarySelect;
    reporting.querySelector=s=>s==='select'?reportingSelect:reportingLabel;
    classification.value = 'full_time_supervisor';
    const picker = { querySelector: s => ({ '[data-assignment-search]': search, '[data-assignment-results]': results, '[data-assignment-selected]': chosen })[s] };
    const catalog = [['1', 'department', 'Shift'], ['2', 'department', 'Outbound'], ['3', 'work_area', 'Shift Door']].map(([unitId, unitType, unitName]) => ({
        dataset: { unitId, unitType, unitName, unitPath: 'Night / Ramp / ' + unitName }, hasAttribute: () => false,
    }));
    const form = { querySelector: s => ({ '[data-person-classification]': classification, '[data-assignment-picker]': picker, '[data-twenty-c-primary]': primary, '[data-twenty-c-secondary]': secondary, '[data-reporting-selection]': reporting })[s], querySelectorAll: () => catalog };
    const source = fs.readFileSync('app/templates/neostaffing/people.html', 'utf8');
    const start = source.indexOf("document.querySelectorAll('[data-people-smart-form]')");
    const end = source.indexOf('})();</script>', start);
    assert.ok(start > 0 && end > start);
    vm.runInNewContext(source.slice(start, end), { document: { querySelectorAll: () => [form], createElement: element, addEventListener() {} } });
    return {classification,search,results,chosen,primary,secondary,primarySelect,secondarySelect,reporting,reportingSelect};
}

test('management picker matches Shi case-insensitively, retains multiple IDs and excludes duplicates/ineligible units', () => {
    const {classification,search,results,chosen}=setup();
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


test('20C requires Primary, filters Secondary by Sort/person and hides generic Reports To', () => {
    const h=setup();
    h.classification.value='twenty_c_full_time_supervisor';h.classification.events.change();
    assert.equal(h.reporting.hidden,true);assert.equal(h.reportingSelect.disabled,true);
    assert.equal(h.primary.hidden,false);assert.equal(h.primarySelect.required,true);
    assert.equal(h.secondary.hidden,false);assert.equal(h.secondarySelect.disabled,false);
    h.primarySelect.value='1:10';h.primarySelect.events.change();
    assert.deepEqual(h.secondarySelect.options.filter(o=>o.value&&!o.disabled).map(o=>o.value),['1:11']);
    h.secondarySelect.value='1:11';h.primarySelect.value='2:12';h.primarySelect.events.change();
    assert.equal(h.secondarySelect.value,'');
    h.classification.value='full_time_supervisor';h.classification.events.change();
    assert.equal(h.primary.hidden,true);assert.equal(h.primarySelect.required,false);
    assert.equal(h.primarySelect.value,'');assert.equal(h.secondarySelect.value,'');
    assert.equal(h.reporting.hidden,false);assert.equal(h.reportingSelect.disabled,false);
    assert.deepEqual(h.reportingSelect.options.filter(o=>o.value&&!o.disabled).map(o=>o.value),['20']);
});
