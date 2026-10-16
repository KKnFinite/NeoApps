const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/neoscorpion_fuel_dispatch_live.js','utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function harness() {
    const requests=[], buttons=['trucks','spear','statistics'].map(section=>({dataset:{dispatchCollapse:section},
        attrs:{},setAttribute(k,v){this.attrs[k]=v;},hasAttribute(){return false;}}));
    const bodies={trucks:{hidden:false},spear:{hidden:false},statistics:{hidden:false}};
    const status={classList:{toggle(){}},textContent:''};
    const saved={rowKey:'mission-1',tableScrollTop:125,y:90,openDetails:['mission-1']};
    let click, restored, observer;
    const classes=new Set();
    const root={dataset:{sortMode:'etd',arrivedFirst:'1',preferenceVersion:'0',sortPreferencesUrl:'/preferences'},
        addEventListener(name,fn){if(name==='click')click=fn;},
        querySelectorAll(s){return s==='[data-dispatch-collapse]'?buttons:s==='[data-dispatch-preference-status]'?[status]:[];},
        querySelector(s){return s.includes('"trucks"')?bodies.trucks:s.includes('"spear"')?bodies.spear:s.includes('"statistics"')?bodies.statistics:null;}};
    const context={root,document:{querySelector:()=>({content:'csrf'}),body:{classList:{contains:c=>classes.has(c)}}},URLSearchParams,
        MutationObserver:class {constructor(callback){observer=callback;}observe(){}},
        initializeDispatchScroll:()=>({snapshot:()=>saved,restoreSnapshot:s=>restored=s}),
        applyDispatchSort:()=>{},fetch(url,options){return new Promise(resolve=>requests.push({options,
            resolve(payload,ok=true){resolve({ok,json:async()=>payload});}}));}};
    const fragment=source.slice(source.indexOf('    let dispatchSortMode'),source.indexOf('    const preserveDispatchScroll'));
    const render=source.slice(source.indexOf('    function renderSortControls'),source.indexOf('    function applyDispatchSort'));
    vm.runInNewContext(fragment+render+';globalThis.values=preferenceValues;',context);
    return {context,buttons,bodies,status,requests,saved,get restored(){return restored;},
        click(i){click({target:{closest:()=>buttons[i]}});},
        board(value){if(value)classes.add('operational-board-view');else classes.delete('operational-board-view');observer();},
        acknowledge(index,fields,ok=true){requests[index].resolve({ok,preferences:{secondary_sort:'etd',arrived_first:true,
            truck_cards_collapsed:false,board_truck_cards_collapsed:false,statistics_collapsed:false,spear_data_collapsed:false,version:index+1,...fields}},ok);}};
}
test('collapse is immediate, independent and preserves mission scroll/details anchors', async()=>{
    const h=harness();h.click(0);
    assert.equal(h.bodies.trucks.hidden,true);assert.equal(h.bodies.spear.hidden,false);
    assert.equal(h.buttons[0].attrs['aria-expanded'],'false');assert.equal(h.buttons[0].textContent,'EXPAND');
    assert.equal(h.restored,h.saved);
    assert.equal(h.requests[0].options.body.get('truck_cards_collapsed'),'1');
    assert.equal(h.requests[0].options.body.has('secondary_sort'),false);
    h.acknowledge(0,{truck_cards_collapsed:true});await tick();
    assert.equal(h.bodies.trucks.hidden,true);
    h.click(1);assert.equal(h.bodies.spear.hidden,true);
    h.acknowledge(1,{truck_cards_collapsed:true,spear_data_collapsed:true});await tick();
    assert.equal(h.bodies.trucks.hidden,true);assert.equal(h.bodies.spear.hidden,true);
});
test('queued rapid toggles preserve latest intent and do not send empty patches',async()=>{
    const h=harness();h.click(0);h.click(0);h.click(0);
    assert.equal(h.requests.length,1);h.acknowledge(0,{truck_cards_collapsed:true});await tick();
    assert.equal(h.requests.length,1);assert.equal(h.bodies.trucks.hidden,true);
    h.click(0);h.click(1);h.acknowledge(1,{truck_cards_collapsed:false});await tick();
    assert.equal(h.requests.length,3);assert.equal(h.requests[2].options.body.get('spear_data_collapsed'),'1');
    assert.equal(h.requests[2].options.body.get('expected_version'),'2');
    h.acknowledge(2,{truck_cards_collapsed:false,spear_data_collapsed:true});await tick();
    assert.equal(h.bodies.trucks.hidden,false);assert.equal(h.bodies.spear.hidden,true);
});
test('conflicts remain visibly unsaved without replay and preserve unrelated server sorting',async()=>{
    const h=harness();h.click(0);
    h.acknowledge(0,{secondary_sort:'arrival',version:9},false);await tick();
    assert.equal(h.bodies.trucks.hidden,true);assert.match(h.status.textContent,/NOT SAVED/);
    assert.equal(h.requests.length,1);assert.equal(h.context.values().secondary_sort,'arrival');
    h.click(1);assert.equal(h.requests[1].options.body.get('expected_version'),'9');
    assert.equal(h.requests[1].options.body.has('secondary_sort'),false);
});
test('normal and Board truck collapse persist independently; statistics has its own sparse patch',async()=>{
    const h=harness();h.click(0);h.acknowledge(0,{truck_cards_collapsed:true});await tick();
    h.board(true);assert.equal(h.bodies.trucks.hidden,false);
    h.click(0);assert.equal(h.requests[1].options.body.get('board_truck_cards_collapsed'),'1');
    assert.equal(h.requests[1].options.body.has('truck_cards_collapsed'),false);
    h.acknowledge(1,{truck_cards_collapsed:true,board_truck_cards_collapsed:true});await tick();
    h.board(false);h.click(0);
    h.acknowledge(2,{truck_cards_collapsed:false,board_truck_cards_collapsed:true});await tick();
    assert.equal(h.bodies.trucks.hidden,false);h.board(true);assert.equal(h.bodies.trucks.hidden,true);
    h.click(2);assert.equal(h.bodies.statistics.hidden,true);
    assert.equal(h.requests[3].options.body.get('statistics_collapsed'),'1');
    assert.equal(h.requests[3].options.body.has('board_truck_cards_collapsed'),false);
});
