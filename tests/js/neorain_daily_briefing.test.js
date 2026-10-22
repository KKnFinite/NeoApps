const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('app/static/js/neorain_daily_briefing.js','utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function page(canEdit=true,{fallback=false,denyClipboard=false,fallbackSuccess=true}={}) {
  const requests=[], handlers={}, timers=[];
  const inputs=['briefing_safety','actual_volume'].map(name => ({name,value:'',disabled:!canEdit,
    tagName:name==='briefing_safety'?'TEXTAREA':'INPUT',style:{},scrollHeight:30,
    hasAttribute:attribute=>name==='briefing_safety'&&attribute==='data-auto-grow',
    dataset:{savedValue:'',version:'0'}}));
  const preview={textContent:'Initial\n\nReport'}, status={textContent:''}, copyStatus={textContent:''};
  const hidden={value:''}, copy={addEventListener:(_,fn)=>{handlers.copy=fn;}};
  const toggle={checked:false,addEventListener:(_,fn)=>{handlers.toggle=fn;}};
  const mode={textContent:''};
  const reports={textContent:JSON.stringify({briefing:preview.textContent,briefing_detailed:'Initial\n\nDetailed — 10 RS - Full notes'})};
  const form={action:'/inputs?operation_id=7',dataset:{canEdit:String(canEdit)},addEventListener:(name,fn)=>{handlers[name]=fn;},
    querySelectorAll:()=>inputs,querySelector:s=>({'[data-briefing-status]':status,
      '[data-briefing-versions]':hidden}[s])};
  const root={dataset:{stateUrl:'/state?operation_id=7',refreshMs:'60000'},
    querySelector:s=>({'#briefing-text':preview,'#copy-briefing':copy,'#copy-briefing-status':copyStatus,
      '#briefing-delay-details':toggle,'[data-briefing-delay-mode]':mode,'[data-briefing-reports]':reports,
      '[data-briefing-inputs]':form}[s])};
  let copied=null;
  let area=null;
  const timeouts=new Map();let nextTimer=0;
  vm.runInNewContext(code,{document:{querySelector:()=>root,hidden:false,
      getSelection:()=>null,activeElement:{focus(){}},body:{appendChild(){}},
      createElement:()=>area={style:{},select(){},remove(){}},
      execCommand:()=>{if(fallbackSuccess)copied=area.value;return fallbackSuccess;}},
    navigator:{clipboard:fallback?undefined:{writeText:async text=>{if(denyClipboard)throw Error('Denied');copied=text;}}},
    setTimeout:(fn,ms)=>{const id=++nextTimer;timeouts.set(id,{fn,ms});return id;},clearTimeout:id=>timeouts.delete(id),
    setInterval:(fn,ms)=>timers.push({fn,ms}),fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))});
  const reply=async (index,changes={})=>{
    const payload={ok:true,briefing:'Updated\n\nReport',fields:Object.fromEntries(inputs.map(input=>[input.name,
      {editable:true,manual_value:'saved',version:1}])),...changes};
    requests[index].resolve({ok:payload.ok,json:async()=>payload});await tick();
  };
  return {inputs,preview,status,copyStatus,requests,timers,timeouts,reply,handlers,toggle,mode,copied:()=>copied,
    enter:(index,value)=>{inputs[index].value=value;handlers.input({target:inputs[index]});},
    blur:index=>handlers.focusout({target:inputs[index]}),
    idle:()=>{const jobs=[...timeouts.values()];timeouts.clear();return Promise.all(jobs.map(job=>job.fn()));},
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
  await p.reply(0,{fields:{briefing_safety:{editable:true,manual_value:'First',version:1}}});
  assert.equal(p.inputs[0].value,'Second');assert.equal(p.inputs[0].dataset.version,1);
  assert.equal(p.requests.length,2);
  await p.reply(1,{fields:{briefing_safety:{editable:true,manual_value:'Second',version:2}}});await save;
  assert.equal(p.status.textContent,'Saved.');
});
test('failed save retains drafts, confirmed report and original versions',async()=>{
  const p=page();p.inputs[0].value='Draft';const save=p.submit();await tick();
  await p.reply(0,{ok:false,error:'Safety changed. Reload before retrying.'});await save;
  assert.equal(p.inputs[0].value,'Draft');assert.equal(p.inputs[0].dataset.version,'0');
  assert.equal(p.preview.textContent,'Initial\n\nReport');assert.match(p.status.textContent,/changed/);
  assert.equal(p.inputs[0].disabled,false);
});

test('typing debounces autosave and leaving a field saves immediately without a button',async()=>{
  const p=page();p.enter(1,'1');p.enter(1,'10');p.enter(1,'100');
  assert.equal(p.requests.length,0);assert.equal(p.timeouts.size,1);
  assert.equal([...p.timeouts.values()][0].ms,650);
  const save=p.blur(1);assert.equal(p.timeouts.size,0);await tick();
  assert.deepEqual(JSON.parse(p.requests[0].options.body),{values:{actual_volume:'100'},versions:{actual_volume:0}});
  assert.equal(p.requests[0].options.keepalive,true);
  await p.reply(0,{fields:{actual_volume:{editable:true,manual_value:'100',version:1}}});await save;
  assert.equal(p.status.textContent,'Saved.');
});

test('pause after entry autosaves an intentional clear and preserves zero',async()=>{
  const p=page();p.inputs[1].value=p.inputs[1].dataset.savedValue='20';p.inputs[1].dataset.version='3';
  p.enter(1,'');const save=p.idle();await tick();
  assert.deepEqual(JSON.parse(p.requests[0].options.body),{values:{actual_volume:''},versions:{actual_volume:3}});
  await p.reply(0,{fields:{actual_volume:{editable:true,manual_value:'',version:4}}});await save;
  p.enter(1,'0');const zero=p.idle();await tick();
  assert.equal(JSON.parse(p.requests[1].options.body).values.actual_volume,'0');
  await p.reply(1,{fields:{actual_volume:{editable:true,manual_value:'0',version:5}}});await zero;
  assert.equal(p.inputs[1].dataset.version,5);
});

test('edits during autosave serialize and use the acknowledged version',async()=>{
  const p=page();p.enter(1,'100');const first=p.blur(1);await tick();
  p.enter(1,'200');const next=p.blur(1);assert.equal(p.requests.length,1);
  await p.reply(0,{fields:{actual_volume:{editable:true,manual_value:'100',version:1}}});
  assert.equal(p.inputs[1].value,'200');assert.equal(p.requests.length,2);
  assert.deepEqual(JSON.parse(p.requests[1].options.body),{values:{actual_volume:'200'},versions:{actual_volume:1}});
  await p.reply(1,{fields:{actual_volume:{editable:true,manual_value:'200',version:2}}});await Promise.all([first,next]);
  assert.equal(p.inputs[1].value,'200');assert.equal(p.status.textContent,'Saved.');
});

test('reverting to the previous value during an in-flight save is also autosaved',async()=>{
  const p=page();p.enter(1,'100');const save=p.blur(1);await tick();
  p.enter(1,'');assert.equal(p.timeouts.size,0);
  await p.reply(0,{fields:{actual_volume:{editable:true,manual_value:'100',version:1}}});
  assert.deepEqual(JSON.parse(p.requests[1].options.body),{values:{actual_volume:''},versions:{actual_volume:1}});
  await p.reply(1,{fields:{actual_volume:{editable:true,manual_value:'',version:2}}});await save;
  assert.equal(p.inputs[1].value,'');assert.equal(p.status.textContent,'Saved.');
});

test('a failed field does not block another field or silently retry its stale version',async()=>{
  const p=page();p.enter(0,'Conflicting draft');const save=p.blur(0);await tick();
  await p.reply(0,{ok:false,error:'Safety changed. Reload before retrying.'});await save;
  p.enter(1,'123');const other=p.blur(1);await tick();
  assert.deepEqual(JSON.parse(p.requests[1].options.body).values,{actual_volume:'123'});
  await p.reply(1,{fields:{actual_volume:{editable:true,manual_value:'123',version:1},briefing_safety:{editable:true,manual_value:'Remote',version:2}}});await other;
  assert.equal(p.inputs[0].value,'Conflicting draft');assert.equal(p.inputs[0].dataset.version,'0');
  await p.handlers.copy();assert.equal(p.copied(),null);assert.match(p.copyStatus.textContent,/not saved/);
});

test('COPY waits for pending autosave and copies the confirmed new report',async()=>{
  const p=page();p.enter(1,'12');const copy=p.handlers.copy();await tick();
  assert.equal(p.copied(),null);
  await p.reply(0,{briefing:'Confirmed new report',fields:{actual_volume:{editable:true,manual_value:'12',version:1}}});await copy;
  assert.equal(p.copied(),'Confirmed new report');assert.equal(p.timeouts.size,0);
});

test('read-only views never autosave or enable inputs after refresh',async()=>{
  const p=page(false);p.enter(1,'123');await p.blur(1);await p.idle();
  assert.equal(p.requests.length,0);
  p.inputs[1].value='';const poll=p.timers[0].fn();await tick();await p.reply(0);await poll;
  assert.equal(p.inputs[1].disabled,true);
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

test('delay toggle is OFF by default and switches synchronously without any request',async()=>{
  const p=page();assert.equal(p.toggle.checked,false);assert.equal(p.mode.textContent,'OFF');
  for(const enabled of [true,false,true,false]){
    p.toggle.checked=enabled;p.handlers.toggle();
    assert.equal(p.preview.textContent,enabled?'Initial\n\nDetailed — 10 RS - Full notes':'Initial\n\nReport');
    assert.equal(p.mode.textContent,enabled?'ON':'OFF');
    await p.handlers.copy();assert.equal(p.copied(),p.preview.textContent);
  }
  assert.equal(p.requests.length,0);
});

test('live payload updates both versions without resetting mode or unsaved drafts',async()=>{
  const p=page();p.toggle.checked=true;p.handlers.toggle();p.inputs[0].value='Unfinished draft';
  const poll=p.timers[0].fn();await tick();
  await p.reply(0,{briefing:'New concise',briefing_detailed:'New detailed — 5 GL - Complete notes'});await poll;
  assert.equal(p.preview.textContent,'New detailed — 5 GL - Complete notes');
  assert.equal(p.inputs[0].value,'Unfinished draft');assert.equal(p.toggle.checked,true);
  p.toggle.checked=false;p.handlers.toggle();assert.equal(p.preview.textContent,'New concise');
});

test('copy uses the mode selected while its pending autosave finishes',async()=>{
  const p=page();p.enter(1,'12');const copy=p.handlers.copy();await tick();
  p.toggle.checked=true;p.handlers.toggle();
  await p.reply(0,{briefing:'Saved concise',briefing_detailed:'Saved detailed',
    fields:{actual_volume:{editable:true,manual_value:'12',version:1}}});await copy;
  assert.equal(p.copied(),'Saved detailed');assert.equal(p.copied(),p.preview.textContent);
});

test('unsupported or denied clipboard fallback copies the exact selected projection',async()=>{
  for(const options of [{fallback:true},{denyClipboard:true}]){
    const p=page(false,options);
    for(const enabled of [true,false]){
      p.toggle.checked=enabled;p.handlers.toggle();await p.handlers.copy();
      assert.equal(p.copied(),p.preview.textContent);assert.equal(p.copyStatus.textContent,'Copied.');
      assert.equal(p.requests.length,0);
    }
  }
  const p=page(true,{fallback:true,fallbackSuccess:false});await p.handlers.copy();
  assert.match(p.copyStatus.textContent,/Copy failed/);assert.equal(p.copied(),null);
});
