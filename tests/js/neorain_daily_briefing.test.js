const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('app/static/js/neorain_daily_briefing.js','utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function page() {
  const requests=[], handlers={}, timers=[];
  const inputs=['briefing_safety','actual_volume'].map(name => ({name,value:'',disabled:false,
    tagName:name==='briefing_safety'?'TEXTAREA':'INPUT',style:{},scrollHeight:30,
    dataset:{savedValue:'',version:'0'}}));
  const preview={textContent:'Initial\n\nReport'}, status={textContent:''}, copyStatus={textContent:''};
  const button={disabled:false}, hidden={value:''}, copy={addEventListener:(_,fn)=>{handlers.copy=fn;}};
  const form={action:'/inputs?operation_id=7',addEventListener:(name,fn)=>{handlers[name]=fn;},
    querySelectorAll:()=>inputs,querySelector:s=>({'[data-briefing-status]':status,'[data-briefing-save]':button,
      '[data-briefing-versions]':hidden}[s])};
  const root={dataset:{stateUrl:'/state?operation_id=7',refreshMs:'60000'},
    querySelector:s=>({'#briefing-text':preview,'#copy-briefing':copy,'#copy-briefing-status':copyStatus,
      '[data-briefing-inputs]':form}[s])};
  let copied=null;
  vm.runInNewContext(code,{document:{querySelector:()=>root,hidden:false},
    navigator:{clipboard:{writeText:async text=>{copied=text;}}},
    setInterval:(fn,ms)=>timers.push({fn,ms}),fetch:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}))});
  const reply=async (index,changes={})=>{
    const payload={ok:true,briefing:'Updated\n\nReport',fields:Object.fromEntries(inputs.map(input=>[input.name,
      {editable:true,manual_value:'saved',version:1}])),...changes};
    requests[index].resolve({ok:payload.ok,json:async()=>payload});await tick();
  };
  return {inputs,preview,status,copyStatus,button,requests,timers,reply,handlers,copied:()=>copied,
    submit:()=>handlers.submit({preventDefault(){}})};
}
test('save sends only changed values and versions; zero and exact copy text survive',async()=>{
  const p=page();p.inputs[1].value='0';const save=p.submit();await tick();
  assert.deepEqual(JSON.parse(p.requests[0].options.body),{values:{actual_volume:'0'},versions:{actual_volume:0}});
  await p.reply(0,{fields:{actual_volume:{editable:true,manual_value:'0',version:1}}});await save;
  assert.equal(p.inputs[1].value,'0');assert.equal(p.status.textContent,'Saved.');
  await p.handlers.copy();assert.equal(p.copied(),'Updated\n\nReport');assert.equal(p.copyStatus.textContent,'Copied.');
  assert.equal(p.timers.length,1);assert.equal(p.timers[0].ms,60000);
});
test('poll refreshes confirmed report while preserving unfinished input and its stale-write version',async()=>{
  const p=page();p.inputs[0].value='Draft';const poll=p.timers[0].fn();await tick();await p.reply(0);await poll;
  assert.equal(p.preview.textContent,'Updated\n\nReport');assert.equal(p.inputs[0].value,'Draft');
  assert.equal(p.inputs[0].dataset.version,'0');assert.equal(p.inputs[1].value,'saved');
});
test('an older poll cannot overwrite a newer successful save',async()=>{
  const p=page();const poll=p.timers[0].fn();await tick();p.inputs[0].value='New';const save=p.submit();await tick();
  await p.reply(1,{briefing:'Fresh saved report'});await save;
  await p.reply(0,{briefing:'Old polled report'});await poll;
  assert.equal(p.preview.textContent,'Fresh saved report');
});
test('typing during save keeps new draft and acknowledges the saved version',async()=>{
  const p=page();p.inputs[0].value='First';const save=p.submit();await tick();p.inputs[0].value='Second';
  await p.reply(0,{fields:{briefing_safety:{editable:true,manual_value:'First',version:1}}});await save;
  assert.equal(p.inputs[0].value,'Second');assert.equal(p.inputs[0].dataset.version,1);
  assert.equal(p.status.textContent,'Saved. New edits remain unsaved.');
});
test('failed save retains drafts, confirmed report and original versions',async()=>{
  const p=page();p.inputs[0].value='Draft';const save=p.submit();await tick();
  await p.reply(0,{ok:false,error:'Safety changed. Reload before retrying.'});await save;
  assert.equal(p.inputs[0].value,'Draft');assert.equal(p.inputs[0].dataset.version,'0');
  assert.equal(p.preview.textContent,'Initial\n\nReport');assert.match(p.status.textContent,/changed/);
  assert.equal(p.button.disabled,false);
});

test('notes expand for typing and refreshed values without changing save or draft state',async()=>{
  const p=page(),note=p.inputs[0];assert.equal(note.style.height,'32px');
  note.value='Longer\nunfinished\nnote';note.scrollHeight=66;
  p.handlers.input({target:note});assert.equal(note.style.height,'68px');
  assert.equal(p.status.textContent,'Unsaved changes.');assert.equal(p.requests.length,0);
  const poll=p.timers[0].fn();await tick();await p.reply(0);await poll;
  assert.equal(note.value,'Longer\nunfinished\nnote');assert.equal(note.style.height,'68px');
  note.value='';note.scrollHeight=30;
  const next=p.timers[0].fn();await tick();await p.reply(1);await next;
  assert.equal(note.value,'saved');assert.equal(note.style.height,'32px');
});
