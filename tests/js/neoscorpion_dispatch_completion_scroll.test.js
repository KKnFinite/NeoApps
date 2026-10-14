const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/neoscorpion_fuel_dispatch_live.js', 'utf8');
const initialize = source.slice(source.indexOf('function initializeDispatchScroll'), source.indexOf('function initializeDispatchSelects'));
function fixture() {
    const stored = new Map();
    const wrap = {scrollTop: 500, scrollLeft: 87, getBoundingClientRect: () => ({top:100,bottom:600})};
    const row = {dataset:{dispatchRowKey:'8-1'}, complete:false,
        classList:{contains: () => row.complete}, getBoundingClientRect: () => ({top:110,bottom:208})};
    const scope = {style:{overflowAnchor:''},
        querySelector: s => s.includes('table-wrap') ? wrap : row,
        querySelectorAll: s => s.includes('aria-expanded') ? [] : [row]};
    const window = {location:{pathname:'/neoscorpion/fuel-dispatch'},scrollX:0,scrollY:300,
        scrollTo({top,left}) {this.scrollY=top;this.scrollX=left;}, requestAnimationFrame: f => f(),
        sessionStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)}};
    const document={activeElement:null,getElementById:()=>null};
    const create=vm.runInNewContext(`(${initialize.trim()})`,{window,document});
    return {stored,wrap,row,scope,window,document,create};
}
test('live completion drops only the completed anchor and preserves both scroll axes', () => {
    const f=fixture(), scroll=f.create(f.scope), saved=scroll.snapshot();
    f.row.complete=true;
    f.row.getBoundingClientRect=()=>({top:2400,bottom:2498});
    scroll.restoreSnapshot(saved);
    assert.equal(f.wrap.scrollTop,500); assert.equal(f.wrap.scrollLeft,87);
    assert.equal(f.window.scrollY,300); assert.equal(f.scope.style.overflowAnchor,'');
});
test('native COMPLETE snapshot survives pagehide and redirect without completed focus', () => {
    const f=fixture(), scroll=f.create(f.scope); let blurred=0;
    scroll.preserveCompletion({blur(){blurred++;}});
    f.wrap.scrollTop=2000; scroll.preserve(); // pagehide cannot overwrite intent
    f.row.complete=true; f.document.activeElement={closest:()=>f.row,blur(){blurred++;}};
    f.create(f.scope);
    assert.equal(f.wrap.scrollTop,500); assert.equal(f.wrap.scrollLeft,87);
    assert.equal(f.window.scrollY,300); assert.ok(blurred>=2);
});
test('ordinary refresh still follows the active mission anchor', () => {
    const f=fixture(), scroll=f.create(f.scope), saved=scroll.snapshot();
    f.row.getBoundingClientRect=()=>({top:160,bottom:258});
    scroll.restoreSnapshot(saved);
    assert.equal(f.wrap.scrollTop,550); // retained 50px active-anchor correction
    assert.equal(f.wrap.scrollLeft,87);
});
