const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/neoscorpion_fuel_dispatch_live.js','utf8');
const apply = source.slice(source.indexOf('function applyDispatchSort'),source.indexOf('function initializeSpearSplash'));

function sorted(mode, primary) {
    const specs = [[1,2,0,false,false],[2,1,2,false,true],[3,0,1,false,true],[4,4,3,true,true],[5,3,4,true,false]];
    let children = [];
    for (const [id,arrival,etd,complete,present] of specs) {
        for (const kind of ['history','primary','details']) {
            children.push({id,kind,dataset:{dispatchSortGroup:String(id),dispatchArrivalRank:arrival,dispatchEtdRank:etd},
                classList:{contains:c=>c==='is-complete'?complete:c==='is-arrived'?present:false},matches:()=>kind==='primary'});
        }
    }
    const tbody={get children(){return children;},appendChild(row){children=children.filter(r=>r!==row);children.push(row);}};
    const scope={querySelectorAll:()=>[],querySelector:s=>s.includes('tbody')?tbody:null};
    vm.runInNewContext(`(${apply.trim()})(scope)`,{scope,renderSortControls:()=>{},document:{activeElement:null},dispatchSortMode:mode,arrivedFirst:primary,preferenceMessage:''});
    return children;
}
test('primary grouping independent from secondary; completed use selected rank',()=>{
    for(const [mode,primary,expected] of [['etd',true,[3,2,1,4,5]],['etd',false,[1,3,2,4,5]],
        ['arrival',true,[3,2,1,5,4]],['arrival',false,[3,2,1,5,4]]]) {
        const rows=sorted(mode,primary);
        assert.deepEqual(rows.filter(r=>r.kind==='primary').map(r=>r.id),expected);
        assert.deepEqual(rows.map(r=>r.id),expected.flatMap(id=>[id,id,id]));
    }
});
