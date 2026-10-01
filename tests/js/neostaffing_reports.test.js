const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup({mobile=false,download=false,valid=true}={}) {
  const events={}, toggleEvents={}, closeEvents={}; let submits=0, focused=0;
  const fields=['sort','operation','department','work_area'].map((key,index)=>({
    value:String(index+1), options:[{value:'',dataset:{}},{value:String(index+1),dataset:{sort:'1',operation:'2',department:'3'}},{value:'99',dataset:{sort:'9'}}]
  }));
  const classes=new Set(), panel={classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},querySelector:()=>({focus:()=>focused++})};
  const toggle={addEventListener:(type,fn)=>toggleEvents[type]=fn,setAttribute:(_key,value)=>toggle.expanded=value,focus:()=>focused++};
  const close={addEventListener:(type,fn)=>closeEvents[type]=fn};
  const more={open:false};
  const form={querySelector:selector=>{
    if(selector.startsWith('[data-report-scope')) return fields[['sort','operation','department','work_area'].indexOf(selector.split('"')[1])];
    return {'[data-report-filter-panel]':panel,'[data-report-filter-toggle]':toggle,'[data-report-more]':more,'[data-report-filter-close]':close}[selector];
  },addEventListener:(type,fn)=>events[type]=fn,reportValidity:()=>valid,requestSubmit:()=>submits++,hasAttribute:()=>!download};
  const media={matches:mobile,addEventListener:(_type,fn)=>media.resize=fn};
  vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_reports.js','utf8'),{
    document:{querySelectorAll:()=>[form]},window:{matchMedia:()=>media}
  });
  return {fields,events,toggleEvents,closeEvents,toggle,panel,more,media,submits:()=>submits,focused:()=>focused};
}
test('scope changes clear descendants, restrict options and auto-apply once',()=>{
  const f=setup(); assert.equal(f.fields[1].options[2].disabled,true);
  f.fields[1].value=''; f.events.change({target:f.fields[1]});
  assert.deepEqual(f.fields.map(x=>x.value),['1','','','']); assert.equal(f.submits(),1);
});
test('extra filter changes retain the scope rail; invalid values do not submit',()=>{
  const f=setup(); f.events.change({target:{name:'classification'}});
  assert.deepEqual(f.fields.map(x=>x.value),['1','2','3','4']); assert.equal(f.submits(),1);
  const invalid=setup({valid:false}); invalid.events.change({target:{}}); assert.equal(invalid.submits(),0);
});
test('Attendance date changes auto-apply without clearing scope',()=>{
  const f=setup(); f.events.change({target:{name:'attendance_date',value:'2026-07-02'}});
  assert.deepEqual(f.fields.map(x=>x.value),['1','2','3','4']); assert.equal(f.submits(),1);
});
test('mobile panel opens and closes without rebuilding controls',()=>{
  const f=setup({mobile:true}); assert.equal(f.more.open,true);
  f.toggleEvents.click(); assert.equal(f.toggle.expanded,'true'); assert.ok(f.panel.classList.contains('is-open'));
  f.events.keydown({key:'Escape'}); assert.equal(f.toggle.expanded,'false'); assert.equal(f.focused(),2);
  f.media.matches=false; f.media.resize(); assert.equal(f.more.open,false);
});
test('Union PDF controls remain explicit downloads instead of auto-applying',()=>{
  const f=setup({download:true,mobile:true}); f.events.change({target:{}}); assert.equal(f.submits(),0);
  f.toggleEvents.click(); assert.equal(f.toggle.expanded,'true'); f.closeEvents.click(); assert.equal(f.toggle.expanded,'false');
});
