const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const tick = () => new Promise(setImmediate);
function setup(workspace='sektor', area='ebm') {
  const events={}, calls=[], status={append(){}}, hours={}, controls=[];
  function input(value,original='') { const result={value,dataset:{original},matches:()=>true}; controls.push(result); return result; }
  const start=input('2230'), end=input('930');
  const part={querySelector: key=>key==='[data-start]'?start:end};
  const parts=[part];
  const container={querySelectorAll:()=>parts};
  const row={dataset:{timecardId:'3',version:'7'},addEventListener:(key,fn)=>events[key]=fn,
    querySelector:key=>key==='[data-time-segments]'?container:key==='[data-row-status]'?status:hours,
    querySelectorAll:key=>key==='input, button'?controls:[]};
  const root={dataset:{nodeWorkspace:workspace,nodeArea:area},querySelectorAll:()=>[row]};
  const unload={}; let deliver, timer;
  vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_node_times.js','utf8'), {
    setTimeout:fn=>{timer=fn;return 1;}, clearTimeout:()=>{timer=null;},
    document:{querySelector:key=>key.includes('data-timecards')?root:{content:'csrf'},createElement:()=>({})},
    window:{location:{href:'/fixture'},addEventListener:(key,fn)=>unload[key]=fn},
    fetch:(url,options)=>{calls.push({url,body:JSON.parse(options.body),headers:options.headers});return new Promise(resolve=>deliver=resolve);}
  });
  return {events,calls,status,controls,start,end,row,parts,unload,
    flush:()=>timer?.(),
    respond:async(ok,body)=>{deliver({ok,json:async()=>body});await tick();}};
}
(async()=>{
  for (const [workspace,area] of [['sektor','ebm'],['ermac','D6']]) {
    const f=setup(workspace,area);
    f.events.change({target:f.start});
    assert.deepEqual(f.calls[0].body,{node_workspace:workspace,node_area:area,commands:[{id:3,version:7,segments:[{start:'22:30',end:'09:30'}]}]});
    assert.equal(f.calls[0].headers['X-CSRFToken'],'csrf');
    assert.ok(f.controls.every(input=>input.disabled));
    f.events.change({target:f.end}); assert.equal(f.calls.length,1);
    await f.respond(true,{saved:true,rows:[{id:3,version:8,hours:'11.00',segments:[{start:'2026-09-12T22:30:00-05:00',end:'2026-09-13T09:30:00-05:00'}]}]});
    assert.equal(f.row.dataset.version,'8'); assert.equal(f.start.value,'22:30:00');
    assert.ok(f.controls.every(input=>!input.disabled));
    assert.match(f.status.textContent,/Saved/);
    f.end.value='1000';f.events.change({target:f.end});
    assert.equal(f.calls[1].body.commands[0].version,8);
    assert.deepEqual(f.calls[1].body.commands[0].segments,[{start:'2026-09-12T22:30:00-05:00',end:'10:00'}]);
    await f.respond(false,{error:'Timecard changed. Reload.'});
    f.events.change({target:f.end}); assert.equal(f.calls.length,2);
    assert.ok(f.controls.every(input=>input.disabled)); assert.match(f.status.textContent,/changed/);
  }
  const multiple=setup();
  const secondStart={value:'01:00:00',dataset:{original:'2026-09-13T01:00:00-05:00'}};
  const secondEnd={value:'0230',dataset:{original:'2026-09-13T02:00:00-05:00'}};
  multiple.parts.push({querySelector:key=>key==='[data-start]'?secondStart:secondEnd});
  multiple.events.input(); multiple.flush();
  assert.equal(multiple.calls[0].body.commands[0].segments.length,2);
  assert.deepEqual(multiple.calls[0].body.commands[0].segments[1],{start:'2026-09-13T01:00:00-05:00',end:'02:30'});
  const removed=setup();removed.start.dataset.original='2026-09-12T22:30:00-05:00';
  removed.parts[0].remove=()=>removed.parts.splice(0,1);
  removed.events.click({target:{closest:key=>key==='[data-add-segment]'?null:removed.parts[0]}});
  assert.deepEqual(removed.calls[0].body.commands[0].segments,[]);
  const f=setup();f.end.value='9999';f.events.change({target:f.end});
  assert.equal(f.calls.length,0);assert.match(f.status.textContent,/valid/);
  let warned=false;f.unload.beforeunload({preventDefault:()=>warned=true});assert.ok(warned);
  let change,submitted=0;
  vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_node_selection.js','utf8'),{
    document:{querySelectorAll:()=>[{addEventListener:(_,fn)=>change=fn,requestSubmit:()=>submitted++}]}});
  change();assert.equal(submitted,1);
  console.log('Node Times: two workspace autosaves, normalization, version chaining, stale blocking, validation, leave guard and immediate Sort navigation passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
