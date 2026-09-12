const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('history entries append without submitting and stop at the configured bound', () => {
    let click, focused = 0;
    const host = {children: [{}], appendChild(row) { this.children.push(row); },
        get lastElementChild() { return {querySelector() { return {focus() { focused++; }}; }}; }};
    const button = {disabled: false, addEventListener(event, handler) { assert.equal(event, 'click'); click = handler; }};
    const template = {content: {cloneNode(deep) { assert.equal(deep, true); return {}; }}};
    const section = {dataset: {entryLimit: '3'}, querySelector(selector) {
        return {'[data-entry-host]': host, 'template': template, '[data-add-entry]': button}[selector];
    }};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/neostaffing_accountability.js'), 'utf8'), {
        document: {querySelectorAll() { return [section]; }},
    });
    click(); click(); click();
    assert.equal(host.children.length, 3);
    assert.equal(focused, 2);
    assert.equal(button.disabled, true);
});
