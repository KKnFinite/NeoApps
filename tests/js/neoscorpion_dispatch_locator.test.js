const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/neoscorpion_fuel_dispatch_live.js','utf8');
const clock = source.slice(source.indexOf('function updateTopOffAlerts(scope)'),source.indexOf('    updateTopOffAlerts(root);'));

function card(status, eta, badge='TOPPING OFF') {
    const classes=new Set(); const styles={};
    return {dataset:{truckOperationalStatus:status,topOffReturnUtc:eta},classes,styles,
        classList:{contains:c=>classes.has(c),toggle:(c,on)=>on?classes.add(c):classes.delete(c)},
        style:{setProperty:(k,v)=>styles[k]=v},querySelector:()=>({textContent:badge})};
}
test('strict return boundary, full UTC dates/offsets, no guessed or invalid estimates',()=>{
    const now=Date.parse('2026-10-22T05:00:00Z');
    const cards=[card('topping_off','2026-10-22T05:00:00Z'),card('topping_off','2026-10-22T04:59:59Z'),
        card('topping_off','2026-10-22T00:01:00-05:00'),card('available','2026-10-21T23:59:00Z'),
        card('topping_off',''),card('topping_off','invalidZ'),card('topping_off','23:59'),
        card('topping_off','2026-10-21T23:59:59-05:00'),card('topping_off','2026-10-21T00:00:00Z','FUELING · C01')];
    const context={topOffAlertTimer:null,Date:{now:()=>now,parse:Date.parse},root:{},
        window:{clearTimeout:()=>{},setTimeout:(f,ms)=>{context.delay=ms;return 1;}}};
    vm.createContext(context);vm.runInContext(clock+';this.update=updateTopOffAlerts;',context);
    context.update({querySelectorAll:()=>cards});
    assert.deepEqual(cards.map(c=>c.classes.has('is-top-off-overdue')),[false,true,false,false,false,false,false,true,false]);
    assert.equal(context.delay,1);
    const old=cards[1].styles['--top-off-pulse-delay'];
    context.update({querySelectorAll:()=>cards});assert.equal(cards[1].styles['--top-off-pulse-delay'],old);
    cards[1].dataset.truckOperationalStatus='available';
    cards[7].dataset.topOffReturnUtc='2026-10-22T06:00:00Z';
    context.update({querySelectorAll:()=>cards});
    assert.equal(cards[1].classes.size,0);assert.equal(cards[7].classes.size,0);
    context.Date.now=()=>now+1;context.update({querySelectorAll:()=>cards});assert.equal(cards[0].classes.size,1);
});

test('search is presentation-only, toolbar is retained through panel refresh',()=>{
    const search=source.slice(source.indexOf('function applyMissionSearch(scope)'),source.indexOf('let topOffAlertTimer'));
    assert.match(search,/data-dispatch-sort-group/);
    assert.match(search,/is-search-match/);assert.match(search,/is-search-dim/);
    assert.doesNotMatch(search,/\.sort\(|\.remove\(|scrollIntoView|fetch\(/);
    assert.match(source,/incomingToolbar\.replaceWith\(toolbar\)/);
    assert.match(source,/search\.focus\(\{preventScroll:true\}\)/);
});
