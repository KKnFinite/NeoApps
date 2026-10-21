const test=require('node:test'),assert=require('node:assert/strict');
const {entryChanges,newerDrafts}=require('../../app/static/js/neoscorpion_fuel_data.js');
test('autosave sends only intentional changes; zero and clearing remain distinct',()=>{
    const fields=[{name:'actual_ctr',value:'0'},{name:'notes',value:''},{name:'remaining_left',value:'6.0'}];
    const before={actual_ctr:'',notes:'Earlier',remaining_left:'6.0'};
    assert.deepEqual(entryChanges(fields,c=>before[c.name],false,'no').map(c=>[c.name,c.value]),[['actual_ctr','0'],['notes','']]);
});
test('manual APU draft never becomes an override until APPLY',()=>{
    const fields=[{name:'apu_override_allowance',value:'1.2'},{name:'apu_override_enabled',value:'1'},{name:'notes',value:'New'}];
    assert.deepEqual(entryChanges(fields,()=>'',false,'yes').map(c=>c.name),['notes']);
    assert.equal(entryChanges(fields,()=>'',true,'yes').length,3);
    assert.equal(entryChanges(fields,()=>'',false,'no').length,3);
});
test('response keeps later typing and unapplied drafts, not the old submitted value',()=>{
    const fields=[{name:'notes',value:'newer'},{name:'actual_ctr',value:'0'},{name:'apu_override_allowance',value:'1.2'}];
    const snapshot=new Map([['notes','submitted'],['actual_ctr','0'],['apu_override_allowance','1.2']]);
    const body=new Map([['notes','submitted'],['actual_ctr','0']]);
    assert.deepEqual(newerDrafts(fields,()=>'',snapshot,body,false),[['notes','newer'],['apu_override_allowance','1.2']]);
});
test('concurrent conflict retains the operator draft for review, not silent replacement',()=>{
    const fields=[{name:'notes',value:'my draft'}];
    assert.deepEqual(newerDrafts(fields,()=> 'prior',new Map([['notes','my draft']]),new Map([['notes','my draft']]),true),[['notes','my draft']]);
});
