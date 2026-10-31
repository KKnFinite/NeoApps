'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=name=>fs.readFileSync('app/static/js/'+name,'utf8');
const storage=()=>{const rows=new Map();return {rows,setItem:(key,value)=>rows.set(key,value),getItem:key=>rows.get(key)??null,removeItem:key=>rows.delete(key)};};
test('SubZero Operations drafts retain notes/revision but exclude CSRF',()=>{
    const script=source('neosubzero_operations.js');
    const start=script.indexOf('    const saveDraft =');const end=script.indexOf('    const sync =',start);
    const fields=[{name:'csrf_token',value:'private-token',type:'hidden'},{name:'expected_revision',value:'original-revision',type:'hidden'},
        {name:'notes',value:'Operator draft',type:'text'}];
    const context={dataset:{dirty:'true'},elements:fields};
    const form={dataset:{unlocked:'true'},elements:fields};
    const content={querySelectorAll:()=>[form]},sessionStorage=storage();
    const root={dataset:{userId:'1',operationId:'2'},querySelector:()=>context};
    vm.runInNewContext(script.slice(start,end)+'saveDraft();',{root,detail:()=>content,formKey:()=>'/pass',draftKey:()=>'/draft',sessionStorage,notify(){},draftMessage(){}});
    for(const value of sessionStorage.rows.values()){
        assert.ok(!value.includes('private-token'));assert.ok(!value.includes('csrf_token'));
        assert.ok(value.includes('original-revision'));assert.ok(value.includes('Operator draft'));
    }
});
test('SubZero restored drafts cannot overwrite current CSRF or replace original revision',()=>{
    const script=source('neosubzero_operations.js');const start=script.indexOf('    const initialize =');const end=script.indexOf('    const applyLayout =',start);
    const sessionStorage=storage();sessionStorage.setItem('/draft',JSON.stringify([{key:'/pass',fields:[
        {name:'csrf_token',value:'stale-private-token'},{name:'expected_revision',value:'original-revision'},{name:'notes',value:'Saved draft'}]}]));
    const fields={csrf_token:{value:'fresh-private-token'},expected_revision:{value:'current-revision'},notes:{value:'Initial notes'}};
    const form={dataset:{},elements:fields};const pane={querySelectorAll:()=>[form]};
    vm.runInNewContext(script.slice(start,end)+'initialize(true);',{pane,sync(){},detail:()=>({}),draftKey:()=>'/draft',formKey:()=>'/pass',sessionStorage,draftMessage(){}});
    assert.equal(fields.csrf_token.value,'fresh-private-token');assert.equal(fields.expected_revision.value,'original-revision');
    assert.equal(fields.notes.value,'Saved draft');assert.ok(!sessionStorage.getItem('/draft').includes('csrf_token'));
});
test('SubZero evidence cleans old CSRF without discarding unsaved evidence',()=>{
    const events={},sessionStorage=storage(),fields={csrf_token:{value:'fresh-token'},record_id:{value:'7'},action:{value:'correct'},
        notes:{value:'Current'},expected_revision:{value:'current-revision'}};
    const form={action:'/save',elements:fields,dataset:{},addEventListener(type,fn){events[type]=fn;}};
    const key='subzero.evidence:1:2:/save:7:correct';
    sessionStorage.setItem(key,JSON.stringify([['csrf_token','old-token'],['notes','Old unsaved note'],['expected_revision','original-revision']]));
    const output={classList:{toggle(){}}};
    const root={dataset:{userId:'1',operationId:'2'},querySelector:selector=>selector.includes('feedback')?output:null,querySelectorAll:()=>[form]};
    class FormData {constructor(form){this.values=Object.entries(form.elements).map(([name,field])=>[name,field.value]);}[Symbol.iterator](){return this.values[Symbol.iterator]();}}
    vm.runInNewContext(source('neosubzero_evidence.js'),{document:{querySelector:()=>root},sessionStorage,FormData,window:{confirm:()=>false},location:{reload(){}}});
    assert.equal(fields.csrf_token.value,'fresh-token');assert.equal(fields.notes.value,'Old unsaved note');
    assert.equal(fields.expected_revision.value,'original-revision');assert.ok(!sessionStorage.getItem(key).includes('csrf_token'));
    events.input();assert.ok(!sessionStorage.getItem(key).includes('csrf_token'));assert.ok(sessionStorage.getItem(key).includes('Old unsaved note'));
});
test('Staffing single-add preserves input without persisting or restoring CSRF',()=>{
    const script=source('staffing_people.js');const start=script.indexOf('        singleForm?.addEventListener("submit"');const end=script.indexOf('        const destination =',start);
    const sessionStorage=storage(),fields={csrf_token:{value:'current-token'},first_name:{value:'Initial'}};let submit;
    const singleForm={elements:{namedItem:name=>fields[name]},addEventListener:(_,fn)=>{submit=fn;}};
    sessionStorage.setItem('neostaffing.people.single-add',JSON.stringify({csrf_token:'stale-token',first_name:'Operator draft'}));
    class FormData {constructor(){this.rows=Object.entries(fields).map(([name,field])=>[name,field.value]);}entries(){return this.rows.values();}}
    vm.runInNewContext(script.slice(start,end),{singleForm,sessionStorage,FormData,document:{body:{textContent:''},querySelector:()=>null},requestAnimationFrame:fn=>fn(),employeeId:null});
    assert.equal(fields.csrf_token.value,'current-token');assert.equal(fields.first_name.value,'Operator draft');
    submit();const saved=JSON.parse(sessionStorage.getItem('neostaffing.people.single-add'));
    assert.deepEqual(saved,{first_name:'Operator draft'});
});
