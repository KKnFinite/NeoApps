const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('app/static/js/neoscorpion_vendor.js','utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('revision deferred during editing is applied later; only GET refreshes, no mutation retries',async()=>{
 let revision=1,tick,editing=false,replaced=0,revoked=false;
 const calls=[],live={replaceChildren(){replaced++}};
 const access={querySelector:s=>s==='[data-vendor-live]'?live:null,setAttribute(){},textContent:''};
 const activeWindow=[[new Date(Date.now()-60000).toISOString(),new Date(Date.now()+60000).toISOString()]];
 const document={hidden:false,activeElement:{closest:()=>editing?{}:null},addEventListener(){},
  querySelector:s=>s==='[data-vendor-refresh]'?{dataset:{revisionUrl:'/revision',revision:'{"revision":1}',sortWindows:JSON.stringify(activeWindow)}}:access};
 vm.runInNewContext(source,{document,window:{addEventListener(){}},location:{href:'/my-truck'},setInterval:fn=>{tick=fn},
  DOMParser:class{parseFromString(){return{querySelector:s=>s==='[data-vendor-live]'?{childNodes:['updated']}:null}}},
  fetch:async(url,options)=>{calls.push({url,options});return{status:revoked?403:200,ok:!revoked,json:async()=>({revision}),text:async()=>'<readonly>'}}});
 await settle();revision=2;editing=true;await tick();assert.equal(replaced,0);
 editing=false;await tick();assert.equal(replaced,1);await tick();assert.equal(replaced,1);
 editing=true;revoked=true;await tick();assert.match(access.textContent,/revoked/);
 assert.ok(calls.every(c=>!c.options.method||c.options.method==='GET'));
});

test('physical Sort gate suppresses all automatic vendor GETs off-hours and resumes only in-window',async()=>{
 let clock=Date.parse('2026-11-21T12:00:00Z'),tick,events={},requests=0;
 const serverDate={now:()=>clock,parse:Date.parse};
 const root={dataset:{revisionUrl:'/revision',revision:'{"revision":1}',
  sortWindows:JSON.stringify([['2026-11-21T22:00:00Z','2026-11-22T06:00:00Z']])}};
 const access={querySelector:()=>null};
 const document={hidden:false,activeElement:{closest:()=>null},
  addEventListener:(name,fn)=>{events[name]=fn},
  querySelector:s=>s==='[data-vendor-refresh]'?root:access};
 const window={addEventListener:(name,fn)=>{events[name]=fn}};
 vm.runInNewContext(source,{document,window,Date:serverDate,location:{href:'/my-truck'},
  setInterval:fn=>{tick=fn},fetch:async()=>{requests++;return{status:200,ok:true,json:async()=>({revision:1})}}});
 await settle();
 await tick();await events.visibilitychange();await events.pageshow();await settle();
 assert.equal(requests,0,'No request when physical Sort is closed');
 clock=Date.parse('2026-11-21T22:00:00Z');
 await tick();assert.equal(requests,1,'Opening boundary enables live requests');
 clock=Date.parse('2026-11-22T06:00:00Z');
 await tick();await events.pageshow();assert.equal(requests,1,'Closing boundary stops all live requests');
});
