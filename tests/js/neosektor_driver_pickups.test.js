const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('canonical side cards replace normal recommendations and clear stale bay labels on refresh', () => {
    const template = fs.readFileSync('app/templates/neonodes/neosektor/driver_routing.html', 'utf8');
    const start = template.indexOf('    const applyBayPriority =');
    const end = template.indexOf('    const applyState =', start);
    assert.ok(start > 0 && end > start);
    const cards = Array.from({length:3}, () => ({dataset:{}, hidden:false, fields:{},
        querySelector(key) { assert.notEqual(key, '[data-driver-rank]'); return this.fields[key] ||= {textContent:''}; }}));
    const context = {root:{querySelectorAll:()=>cards}};
    vm.runInNewContext(template.slice(start,end) + '\nthis.render = applyBayPriority;', context);
    const normal = [{pickup:'front',bay_name:'Bay 5',rank_label:'1ST',status:'Full'}];
    const back = side => ({pickup:'back',side:side.toLowerCase(),bay_name:'',rank_label:'',status:'',label:'← BACK PICKUP '+side});
    context.render(normal);
    assert.equal(cards[0].fields['[data-driver-bay-name]'].textContent, '5');
    for (const sides of [['EAST'], ['WEST'], ['EAST','WEST']]) {
        context.render(sides.map(back));
        assert.equal(cards.filter(c=>!c.hidden).length, sides.length);
        sides.forEach((side,i) => {
            assert.equal(cards[i].dataset.pickup,'back');
            assert.equal(cards[i].fields['[data-driver-bay-name]'].textContent,'');
            assert.equal(cards[i].fields['[data-driver-rank]'],undefined);
            assert.equal(cards[i].fields['[data-driver-pickup]'].textContent,'DISCHARGE '+side);
        });
    }
    context.render([]); // Cut Discharge's canonical empty priority list.
    assert.ok(cards.every(c=>c.hidden));
    context.render(normal);
    assert.equal(cards[0].dataset.pickup,'front');
    assert.equal(cards[0].fields['[data-driver-pickup]'].textContent,'');
    assert.equal(cards[0].fields['[data-driver-bay-name]'].textContent,'5');
});
