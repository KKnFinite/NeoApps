const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('Dispatch APU save reconciles missing, zero and numeric values with server formatting', () => {
    const script = fs.readFileSync(path.join(__dirname, '../../app/static/js/neoscorpion_fuel_dispatch_live.js'), 'utf8');
    const fn = script.match(/    const updateApuAllowanceDisplay = \(form, payload, button\) => \{[\s\S]*?\n    \};/)[0];
    const effective = {textContent: ''};
    const context = {root: {querySelector: () => effective}};
    vm.createContext(context);
    vm.runInContext(`${fn}\nthis.update = updateApuAllowanceDisplay;`, context);
    const form = {id: 'assignment-1', elements: {namedItem: () => null}, querySelector: () => null};
    for (const [lbs, expected] of [[null, '-'], [undefined, '-'], [0, '0.0'], [500, '0.5'], [1200, '1.2']]) {
        context.update(form, {effective_apu_allowance_lbs: lbs}, null);
        assert.equal(effective.textContent, `APU: ${expected}`);
    }
});
