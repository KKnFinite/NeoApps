const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('collapsible scope selection cascades and clears partial parents', () => {
    function node(children = [], selectable = true) {
        const result = { children };
        result.input = { checked: true, indeterminate: false, dataset: { scopeSelectable: selectable ? '1' : '0' }, closest: selector => selector === '[data-vacation-scope-check]' ? result.input : result };
        result.querySelector = () => result.input;
        result.querySelectorAll = selector => {
            if (selector === ':scope > details > ul > [data-vacation-scope-node]') return children;
            assert.equal(selector, ':scope > details > ul [data-vacation-scope-check]');
            return children.flatMap(child => [child.input, ...child.querySelectorAll(selector)]);
        };
        return result;
    }
    const left = node(), right = node(), root = node([left, right]);
    const inputs = [root.input, left.input, right.input];
    const select = { value: '1' };
    const tree = { dataset: { vacationOperationTree: '1' }, querySelector: () => root, querySelectorAll: () => inputs };
    let change;
    const editor = { querySelector: () => select, querySelectorAll: selector => selector === '[data-vacation-operation-tree]' ? [tree] : [], addEventListener: (_, fn) => { change = fn; } };
    vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_vacation.js', 'utf8'), { document: { querySelector: selector => selector === '[data-vacation-union-editor]' ? editor : null } });
    left.input.checked = false;
    change({ target: left.input });
    assert.equal(root.input.checked, false);
    assert.equal(root.input.indeterminate, true);
    root.input.checked = true;
    change({ target: root.input });
    assert.ok(inputs.every(input => input.checked));
    root.input.dataset.scopeSelectable = '0';
    change({ target: select });
    assert.equal(root.input.disabled, true);
    assert.equal(root.input.checked, false);
    select.value = '2';
    change({ target: select });
    assert.ok(inputs.every(input => input.disabled));
});
