const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function formFor(scope) {
  const node = () => ({value:'', hidden:false, handlers:{}, addEventListener(k,fn){this.handlers[k]=fn;}});
  const person=node(), unit=node(), level=node(), picker=node(), context=node(), label=node(), guidance=node(), submit=node();
  person.selectedOptions=[{dataset:{classification:''}}];
  unit.options=[{value:'',dataset:{},textContent:''}, ...['sort','operation','department','work_area'].flatMap((type,i)=>[
    {value:String(i*2+1),dataset:{unitType:type},textContent:'Other '+type},
    {value:String(i*2+2),dataset:{unitType:type,...(type===scope?{currentScope:'1'}:{})},textContent:'Night / Current '+type}])];
  Object.defineProperty(unit,'selectedOptions',{get(){return unit.options.filter(o=>o.value===unit.value);}});
  const nodes={'[data-management-person]':person,'[data-management-unit]':unit,'[data-management-level]':level,'[data-management-unit-picker]':picker,'[data-management-current-scope]':context,'[data-management-current-scope-label]':label,'[data-management-assignment-guidance]':guidance,'[data-management-assignment-submit]':submit};
  vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_people_assignment.js','utf8'),{document:{querySelector:()=>({querySelector:s=>nodes[s]})}});
  return {...nodes,person,unit,level,picker,context,label,submit,choose(classification){person.value='100';person.selectedOptions[0].dataset.classification=classification;person.handlers.change();}};
}
for (const [classification,scope] of [['part_time_supervisor','work_area'],['full_time_supervisor','department'],['manager','operation'],['division_manager','sort']]) {
  test(classification+' automatically uses current scope and submits canonical unit/level',()=>{
    const f=formFor(scope);f.choose(classification);
    assert.equal(f.picker.hidden,true);assert.equal(f.context.hidden,false);
    assert.equal(f.unit.selectedOptions[0].dataset.currentScope,'1');assert.equal(f.level.value,scope);
    assert.equal(f.label.textContent,'Night / Current '+scope);assert.equal(f.submit.disabled,false);
  });
}
test('incompatible scope shows only valid targets; returning to compatible classification restores scope',()=>{
  const f=formFor('work_area');f.choose('part_time_supervisor');f.choose('manager');
  assert.equal(f.picker.hidden,false);assert.equal(f.context.hidden,true);
  assert.equal(f.level.value,'operation');
  assert.deepEqual(f.unit.options.filter(o=>o.value&&!o.disabled).map(o=>o.dataset.unitType),['operation','operation']);
  f.unit.value='7';f.choose('part_time_supervisor');assert.equal(f.unit.value,'8');assert.equal(f.picker.hidden,true);
});
test('no People scope keeps target selector and no person cannot submit',()=>{
  const f=formFor(null);assert.equal(f.submit.disabled,true);f.choose('twenty_c_full_time_supervisor');
  assert.equal(f.picker.hidden,false);assert.equal(f.context.hidden,true);
  assert.deepEqual(f.unit.options.filter(o=>o.value&&!o.disabled).map(o=>o.dataset.unitType),['department','department','work_area','work_area']);
});
