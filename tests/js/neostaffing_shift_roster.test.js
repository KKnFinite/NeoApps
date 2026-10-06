const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('app/static/js/neostaffing_shift_roster.js', 'utf8');
function board(mobile = false, stored = null) {
    const element = (dataset = {}) => ({dataset, hidden:false, disabled:false, handlers:{},
        addEventListener(key, fn) {this.handlers[key] = fn;},
        setAttribute(key, value) {this[key] = value;}, click() {this.handlers.click();}});
    const labels = ['D34','D32','D29','D26','D24','D21','D17','D13','D9','D6','D4','D1'];
    const columns = [0,1].flatMap(() => labels.map((label, index) => element({rosterColumn:String(index), rosterColumnSide:index < 6 ? 'west':'east', doorLabel:label})));
    const discharge = element({doorLabel:'DISCHARGE'});
    const sides = ['all','west','east'].map(side => element({rosterSide:side}));
    const paging = element(), previous = element(), next = element(), range = element();
    const media = element(); media.matches = mobile;
    const root = {style:{setProperty(){}}, dataset:{}, querySelectorAll(selector) {return selector === '[data-roster-column]' ? columns:sides;},
        querySelector(selector) {return {'[data-roster-discharge]':discharge,'[data-roster-paging]':paging,'[data-roster-previous]':previous,'[data-roster-next]':next,'[data-roster-range]':range}[selector];}};
    let saved = stored;
    vm.runInNewContext(code, {document:{querySelector:() => root}, window:{matchMedia:() => media}, sessionStorage:{getItem:() => saved, setItem:(_,value) => {saved = value;}}});
    return {columns,discharge,sides,next,previous,media,paging,range,saved:() => saved, visible:section => columns.slice(section*12,(section+1)*12).filter(c => !c.hidden).map(c => c.dataset.doorLabel)};
}
test('desktop sides preserve configured order and align both read-only rosters', () => {
    const b = board(); assert.equal(b.visible(0).length,12);
    b.sides[2].click(); assert.deepEqual(b.visible(0),['D17','D13','D9','D6','D4','D1']);
    assert.deepEqual(b.visible(0),b.visible(1));
    assert.deepEqual(board(false,b.saved()).visible(0),b.visible(0));
});
test('mobile pages three doors with both sections aligned and retained side', () => {
    const b = board(true); assert.deepEqual(b.visible(0),['D34','D32','D29']);
    b.next.click(); assert.deepEqual(b.visible(0),['D26','D24','D21']);
    assert.deepEqual(b.visible(0),b.visible(1)); assert.equal(b.next.disabled,true);
    b.sides[2].click(); assert.deepEqual(b.visible(0),['D17','D13','D9']);
    assert.deepEqual(board(true,b.saved()).visible(0),b.visible(0));
    b.media.matches = false; b.media.handlers.change(); assert.equal(b.visible(0).length,6);
});

test('Discharge is a shared right-side start-area column and appears on the final East mobile page', () => {
    const desktop = board(); assert.equal(desktop.discharge.hidden, false);
    const west = board(); west.sides[1].click(); assert.equal(west.discharge.hidden, false);
    const mobile = board(true); assert.equal(mobile.discharge.hidden, true);
    mobile.sides[2].click(); assert.equal(mobile.discharge.hidden, true);
    mobile.next.click(); assert.equal(mobile.discharge.hidden, false);
    assert.match(mobile.range.textContent, /DISCHARGE$/);
});
