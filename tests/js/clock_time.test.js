const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const handlers = {};
const window = {};
const document = {addEventListener(name, handler) {handlers[name] = handler;}};
vm.runInNewContext(fs.readFileSync('app/static/js/clock_time.js', 'utf8'), {window, document});
const clock = window.NeoAppsClockTime;

test('shared clock normalizes compact, shorthand, colon and optional blank', () => {
    for (const [raw, expected] of Object.entries({
        '0500': '05:00', '0830': '08:30', '1230': '12:30', '2100': '21:00',
        '2359': '23:59', '500': '05:00', '930': '09:30', '21:00': '21:00',
        '': '',
    })) assert.equal(clock.normalize(raw), expected);
});

test('invalid and incomplete values never normalize or save', () => {
    for (const raw of ['2400', '2360', '9999', 'abcd', '0', '05', '5:00', '']) {
        assert.equal(clock.normalize(raw, false), null);
    }
});

test('mobile numeric entry stays untouched until blur/change; form submit validates', () => {
    let validity = '', focused = false, prevented = false;
    const input = {value: '05', matches: selector => selector === '[data-clock-time]',
        setCustomValidity(value) {validity = value;}, setAttribute() {}, removeAttribute() {},
        reportValidity() {}, focus() {focused = true;}};
    handlers.input({target: input});
    assert.equal(input.value, '05');
    handlers.submit({target: {querySelectorAll: () => [input]}, preventDefault() {prevented = true;}, stopImmediatePropagation() {}});
    assert.equal(prevented, true); assert.equal(focused, true); assert.notEqual(validity, '');
    input.value = '0500'; handlers.focusout({target: input});
    assert.equal(input.value, '05:00'); assert.equal(validity, '');
});
