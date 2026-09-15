const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('autosave submits one signed row, updates original, rejects stale without retry', async () => {
  const handlers = {}, notice = {}, requests = [];
  const select = {name:'status_12',value:'',disabled:false};
  const original = {name:'original_12',value:'signed-before'};
  const form = {action:'/employees',querySelectorAll:()=>[select],addEventListener:(name,fn)=>handlers[name]=fn,
    querySelector:s=>s.includes('original_')?original:s.includes('operation')?{value:'7'}:notice};
  let finish;
  vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_node_attendance.js','utf8'), {
    document:{querySelector:s=>s.includes('autosave')?form:{content:'csrf'},querySelectorAll:()=>[]}, FormData,
    fetch:(url,options)=>{requests.push(options); return new Promise(resolve=>finish=resolve);},
  });
  select.value='here'; const pending=handlers.change({target:select});
  assert.equal(select.disabled,true);
  assert.deepEqual([...requests[0].body.keys()],['sort_date_operation_id','status_12','original_12']);
  await handlers.change({target:select}); assert.equal(requests.length,1);
  finish({ok:true,json:async()=>({ok:true,rows:{12:{status:'here',original:'signed-after'}}})}); await pending;
  assert.equal(original.value,'signed-after'); assert.equal(select.disabled,false);
  select.value='no_call'; const conflict=handlers.change({target:select});
  finish({ok:false,json:async()=>({error:'Attendance changed. Reload.'})}); await conflict;
  assert.equal(select.value,'here'); assert.match(notice.textContent,/Reload/);
  assert.equal(requests.length,2); assert.equal(original.value,'signed-after');
});
