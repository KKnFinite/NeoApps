const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync('app/static/js/neorain_rainrock.js','utf8');
const tick=()=>new Promise(r=>setImmediate(r));
function page(editable=true,{sortEndMs=Date.now()+60000,physicalWindow=true}={}){
 const requests=[],handlers={},intervals=[],timeouts=new Map(),notes={},buttons={};let timer=0;
 const inputs=['actual_volume','note_1'].map(name=>({name,value:'',readOnly:!editable,dataset:{savedValue:'',version:'0'},closest:()=>({})}));
 inputs.forEach(i=>{notes[i.name]={textContent:''};buttons[i.name]={hidden:true};});
 const form={dataset:{canEdit:String(editable)},action:'/rainrock/inputs?operation_id=7',
  querySelectorAll:()=>inputs,addEventListener:(key,fn)=>handlers[key]=fn};
 const status={textContent:'',classList:{toggle(){}}},copyStatus={textContent:''},live={innerHTML:'original'};
 const root={dataset:{operation:'7',stateUrl:'/state?operation_id=7',autoStateUrl:'/state?operation_id=7&automatic=1',sortEndMs:String(sortEndMs),refreshMs:'60000'},querySelector(s){
  if(s==='[data-report-inputs]')return form;if(s==='[data-rainrock-live]')return live;
  if(s==='[data-rainrock-notes]')return null;
  if(s==='[data-report-save-status]')return status;if(s==='[data-rainrock-copy-status]')return copyStatus;
  const field=s.match(/\[data-field-status="(.+)"\]/);if(field)return notes[field[1]];
  const button=s.match(/\[data-report-use-saved="(.+)"\]/);if(button)return buttons[button[1]];
  return {addEventListener:(_,fn)=>{handlers[s.includes('download')?'download':'copy']=fn;}};
 },querySelectorAll:()=>[],dispatchEvent(){}};
 const ctx={CustomEvent:class {},document:{querySelector:()=>root,hidden:false,fonts:{ready:Promise.resolve()}},
  window:{NeoRainBoard:{updateReport:(element,html)=>{element.innerHTML=html;}},addEventListener(){},NeoLiveUpdates:{insidePhysicalSort:()=>physicalWindow}},
  navigator:{},setTimeout:(fn,ms)=>{const id=++timer;timeouts.set(id,{fn,ms});return id;},clearTimeout:id=>timeouts.delete(id),
  setInterval:(fn,ms)=>intervals.push({fn,ms}),clearInterval:()=>{},fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))};
 vm.runInNewContext(code,ctx);
 const reply=async(index,extra={})=>{const payload={ok:true,operation_id:7,can_edit:editable,rainrock_html:'fresh',fields:Object.fromEntries(inputs.map(i=>[i.name,
  {input_value:'saved',version:1,source:'NeoRain entry'}])),...extra};requests[index].resolve({ok:payload.ok,json:async()=>payload});await tick();};
 return {inputs,live,status,copyStatus,notes,buttons,handlers,requests,intervals,timeouts,reply,
  type:(i,value)=>{inputs[i].value=value;handlers.input({target:inputs[i]});},
  blur:i=>handlers.focusout({target:inputs[i]}),submit:()=>handlers.submit({preventDefault(){}})};
}
test('an unexpired report stays idle when the physical Sort is closed',async()=>{
 const p=page(true,{physicalWindow:false});await p.intervals[0].fn();assert.equal(p.requests.length,0);
});
test('zero is saved with exact field/version and normalized report returned',async()=>{
 const p=page();p.type(0,'0');const save=p.blur(0);await tick();
 assert.deepEqual(JSON.parse(p.requests[0].options.body),{values:{actual_volume:'0'},versions:{actual_volume:0}});
 await p.reply(0,{fields:{actual_volume:{input_value:'0',version:1}}});await save;
 assert.equal(p.inputs[0].value,'0');assert.equal(p.status.textContent,'Auto-saves');assert.equal(p.live.innerHTML,'fresh');
});
test('poll updates output without overwriting focused draft or its conflict version',async()=>{
 const p=page();p.inputs[1].value='typing';const poll=p.intervals[0].fn();await tick();await p.reply(0);await poll;
 assert.equal(p.live.innerHTML,'fresh');assert.equal(p.inputs[1].value,'typing');assert.equal(p.inputs[1].dataset.version,'0');
});
test('stale and cross-sort responses cannot replace current report',async()=>{
 const p=page();const poll=p.intervals[0].fn();await tick();p.type(0,'100');const save=p.blur(0);await tick();
 await p.reply(1,{rainrock_html:'new save'});await save;await p.reply(0,{rainrock_html:'stale poll'});await poll;
 assert.equal(p.live.innerHTML,'new save');
 const next=p.intervals[0].fn();await tick();await p.reply(2,{operation_id:8,rainrock_html:'other sort'});await next;
 assert.equal(p.live.innerHTML,'new save');
});
test('typing and an intentional revert during a save are queued, not lost',async()=>{
 const p=page();p.type(0,'100');const save=p.blur(0);await tick();p.inputs[0].value='';
 await p.reply(0,{fields:{actual_volume:{input_value:'100',version:1}}});
 assert.equal(p.requests.length,2);assert.deepEqual(JSON.parse(p.requests[1].options.body),{values:{actual_volume:''},versions:{actual_volume:1}});
 await p.reply(1,{fields:{actual_volume:{input_value:'',version:2}}});await save;assert.equal(p.inputs[0].value,'');
});
test('conflict preserves draft/version, shows recovery, and is never automatically retried',async()=>{
 const p=page();p.type(0,'draft');const save=p.blur(0);await tick();await p.reply(0,{ok:false,error:'Changed elsewhere'});await save;
 assert.equal(p.inputs[0].value,'draft');assert.equal(p.inputs[0].dataset.version,'0');assert.equal(p.notes.actual_volume.textContent,'Changed elsewhere');
 assert.equal(p.buttons.actual_volume.hidden,false);assert.equal(p.requests.length,1);
 p.blur(0);assert.equal(p.requests.length,1);
 await p.handlers.copy();assert.match(p.copyStatus.textContent,/not saved/);assert.equal(p.requests.length,1);
});
test('read-only refresh does not falsely advertise autosave or send mutations',async()=>{
 const p=page(false);p.type(0,'bad');p.blur(0);assert.equal(p.requests.length,0);
 const poll=p.intervals[0].fn();await tick();await p.reply(0);await poll;assert.equal(p.status.textContent,'Read-only');
});
test('image export uses promised PNG clipboard, full scroll height and no text substitution',()=>{
 assert.match(code,/new ClipboardItem\(\{'image\/png':promised\}\)/);
 assert.match(code,/clone\.scrollHeight/);assert.match(code,/canvas\.height!==Math\.floor\(height\*scale\)/);
 assert.doesNotMatch(code,/writeText/);assert.match(code,/Complete PNG downloaded/);
});
test('no automatic request is installed without a valid future Sort deadline',()=>{
 const p=page(true,{sortEndMs:0});
 assert.equal(p.intervals.length,0);
 assert.equal(p.requests.length,0);
});
test('a boundary response stops automatic refresh without applying an update',async()=>{
 const p=page();const timer=p.intervals[0];
 const poll=timer.fn();await tick();
 assert.match(p.requests[0].url,/automatic=1/);
 await p.reply(0,{refresh:{auto_refresh_enabled:false,sort_end_ms:null}});await poll;
 await timer.fn();
 assert.equal(p.requests.length,1);
});
