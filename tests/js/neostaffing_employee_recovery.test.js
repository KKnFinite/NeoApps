const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const editor=fs.readFileSync('app/static/js/neostaffing_employee_editor.js','utf8');
const recovery=fs.readFileSync('app/static/js/neostaffing_employee_recovery.js','utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('delete confirmation is explicit request data; CSRF and original draft remain intact',async()=>{
  let sent;
  const context={AbortController,setTimeout,clearTimeout,document:{querySelector:()=>({content:'csrf'})},FormData:class extends Map {constructor(form){super(Object.entries(form));}},
    fetch:async(url,options)=>{sent={url,...options};return {ok:true,json:async()=>({ok:true})};}};
  const fragment=editor.slice(editor.indexOf('  const post = async'),editor.indexOf('  const open = async'));
  vm.runInNewContext(fragment+';this.send=post;',context);
  const draft={first_name:'Keep draft',expected_person_version:'original'};
  await context.send('/employee/delete',draft,{confirm_delete:'yes',confirmed_employee_id:'A-12'});
  assert.equal(sent.body.get('confirm_delete'),'yes');assert.equal(sent.body.get('confirmed_employee_id'),'A-12');
  assert.equal(sent.headers['X-CSRFToken'],'csrf');assert.equal(draft.first_name,'Keep draft');assert.equal(draft.confirm_delete,undefined);
  context.fetch=async()=>({ok:false,json:async()=>({ok:false,error:'Employee changed; reopen editor.'})});
  await assert.rejects(context.send('/employee/delete',draft),/Employee changed/);
  context.fetch=async()=>{throw new TypeError('network failure');};
  await assert.rejects(context.send('/employee/delete',draft,{confirm_delete:'yes'}),/could not be confirmed/);
});

function harness() {
  const button={disabled:false,hidden:false},status={textContent:'',setAttribute(k,v){this[k]=v;}};
  let submit;const requests=[];
  const form={action:'/recovery/id/restore',addEventListener(_name,fn){submit=fn;},querySelector:s=>s==='button'?button:status};
  vm.runInNewContext(recovery,{AbortController,setTimeout,clearTimeout,document:{querySelectorAll:()=>[form]},FormData:class {},
    fetch:()=>new Promise(resolve=>requests.push(resolve))});
  return {button,status,requests,submit:()=>submit({preventDefault(){}})};
}

test('restore suppresses duplicate clicks and confirms only server-accepted success',async()=>{
  const h=harness();const pending=h.submit();h.submit();assert.equal(h.requests.length,1);assert.equal(h.button.disabled,true);
  h.requests[0]({ok:true,json:async()=>({ok:true,person_id:12})});await pending;
  assert.equal(h.status.textContent,'Employee restored.');assert.equal(h.button.hidden,true);assert.equal(h.button.disabled,false);
});

test('restore conflict recovers button and displays actionable server rejection',async()=>{
  const h=harness();const pending=h.submit();h.requests[0]({ok:false,json:async()=>({ok:false,error:'Related assignment changed.'})});await pending;await tick();
  assert.equal(h.status.textContent,'Related assignment changed.');assert.equal(h.status.role,'alert');
  assert.equal(h.button.disabled,false);assert.equal(h.button.hidden,false);
});
