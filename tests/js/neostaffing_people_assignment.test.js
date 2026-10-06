const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function formFor(scope) {
  const node = () => ({value:'', hidden:false, handlers:{}, addEventListener(k,fn){this.handlers[k]=fn;}});
  const person=node(), unit=node(), level=node(), guidance=node(), submit=node();
  person.selectedOptions=[{dataset:{classification:''}}];
  unit.options=[{value:'',dataset:{},textContent:''}, ...['sort','operation','department','work_area'].flatMap((type,i)=>[
    {value:String(i*2+1),dataset:{unitType:type},textContent:'Other '+type},
    {value:String(i*2+2),dataset:{unitType:type,...(type===scope?{currentScope:'1'}:{})},textContent:'Night / Current '+type}])];
  Object.defineProperty(unit,'selectedOptions',{get(){return unit.options.filter(o=>o.value===unit.value);}});
  const nodes={'[data-management-person]':person,'[data-management-unit]':unit,'[data-management-level]':level,'[data-management-assignment-guidance]':guidance,'[data-management-assignment-submit]':submit};
  vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_people_assignment.js','utf8'),{document:{querySelector:()=>({querySelector:s=>nodes[s]})}});
  return {...nodes,person,unit,level,submit,choose(classification){person.value='100';person.selectedOptions[0].dataset.classification=classification;person.handlers.change();}};
}
for (const [classification,scope] of [['part_time_supervisor','work_area'],['full_time_supervisor','department'],['manager','operation'],['division_manager','sort']]) {
  test(classification+' scope is selected before person and stays editable after selection',()=>{
    const f=formFor(scope);
    assert.equal(f.unit.selectedOptions[0].dataset.currentScope,'1');
    assert.equal(f.submit.disabled,true);
    assert.equal(f.unit.options.filter(o=>o.value&&!o.disabled).length,8);
    f.choose(classification);
    assert.equal(f.unit.selectedOptions[0].dataset.currentScope,'1');assert.equal(f.level.value,scope);
    assert.equal(f.submit.disabled,false);
    const other=f.unit.options.find(o=>o.value&&o.dataset.unitType===scope&&!o.dataset.currentScope);
    f.unit.value=other.value;f.unit.handlers.change();
    assert.equal(f.unit.value,other.value);assert.equal(f.submit.disabled,false);
  });
}
test('incompatible current scope requires a valid target instead of choosing an arbitrary one',()=>{
  const f=formFor('work_area');f.choose('manager');
  assert.equal(f.unit.value,'');assert.equal(f.submit.disabled,true);
  assert.deepEqual(f.unit.options.filter(o=>o.value&&!o.disabled).map(o=>o.dataset.unitType),['operation','operation']);
  f.unit.value='3';f.unit.handlers.change();assert.equal(f.submit.disabled,false);
  f.choose('part_time_supervisor');assert.equal(f.unit.value,'8');
});
test('no People scope starts blank and stays blank until a target is chosen',()=>{
  const f=formFor(null);assert.equal(f.unit.value,'');assert.equal(f.submit.disabled,true);
  f.choose('twenty_c_full_time_supervisor');assert.equal(f.unit.value,'');assert.equal(f.submit.disabled,true);
  assert.deepEqual(f.unit.options.filter(o=>o.value&&!o.disabled).map(o=>o.dataset.unitType),['department','department','work_area','work_area']);
});
