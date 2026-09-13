const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function mutation() {
  const events = {}, status = {}, controls = [{disabled:false}];
  const part = {querySelector: key => ({value:key === '[data-start]' ? '22:00' : '02:10'})};
  const row = {dataset:{timecardId:'3',version:'7'},querySelectorAll:()=>[part]};
  const root = {addEventListener:(name, fn)=>events[name]=fn,
    querySelector:()=>status,querySelectorAll:()=>controls};
  let resolve, calls = 0, reloads = 0;
  const context = {document:{querySelector:key=>key === '[data-timecards]' ? root : {content:'csrf'}},
    window:{location:{reload:()=>reloads++}}, fetch:(_url, options)=>{
      calls++; const body = JSON.parse(options.body);
      assert.deepEqual(body.commands,[{id:3,version:7,segments:[{start:'22:00',end:'02:10'}]}]);
      return new Promise(done=>resolve=done);
    }};
  vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_timecards.js','utf8'),context);
  events.input({target:{closest:()=>row}});
  const event = {target:{closest:key=>key === '[data-save-times]' ? {} : null}};
  const pending = events.click(event);
  assert.equal(controls[0].disabled,true);
  await events.click(event); assert.equal(calls,1);
  resolve({ok:false,json:async()=>({error:'stale; reload'})}); await pending;
  assert.equal(reloads,0); assert.equal(status.textContent,'stale; reload');
  assert.equal(calls,1); assert.equal(controls[0].disabled,false);
  const valid = events.click(event);
  resolve({ok:true,json:async()=>({saved:true})}); await valid;
  assert.equal(reloads,1);
}

async function archive(complete = true, interrupted = false) {
  let click, deliver, calls = 0, downloads = 0;
  const status = {}, button = {dataset:{archiveDownload:complete ? 'complete' : 'scoped'}};
  const root = {addEventListener:(_name, fn)=>click=fn,querySelector:key=>key === '[data-archive-week]' ? {value:'2026-09-06'} : status};
  const context = {document:{querySelector:key=>key === '[data-timecards]' ? root : {content:'csrf'},
      createElement:()=>({click:()=>downloads++})},
    URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL:()=>{}},setTimeout:()=>{},
    fetch:async(_url, options)=>{
      calls++;
      if (calls === 1) return {ok:true,blob:()=>new Promise((done,reject)=>deliver=interrupted ? ()=>reject(new Error('Transfer interrupted')) : done),headers:{get:()=>complete ? 'signed-token' : null}};
      assert.deepEqual(JSON.parse(options.body),{token:'signed-token'});
      return {ok:true};
    }};
  vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_timecard_archive.js','utf8'),context);
  const pending = click({target:{closest:()=>button}});
  await new Promise(setImmediate);
  assert.equal(calls,1); assert.equal(downloads,0);
  deliver({}); await pending;
  assert.equal(calls,complete && !interrupted ? 2 : 1);
  assert.equal(downloads,interrupted ? 0 : 1);
  assert.match(status.textContent,interrupted ? /interrupted/ : complete ? /countdown started/ : /Retention unchanged/);
}

(async()=>{await mutation(); await archive(); await archive(false); await archive(true,true); console.log('4 Timecards JS workflow checks passed');})().catch(error=>{console.error(error);process.exitCode=1;});
