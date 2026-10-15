const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('app/static/js/neoscorpion_vendor.js','utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('revision deferred during editing is applied later; only GET refreshes, no mutation retries',async()=>{
 let revision=1,tick,editing=false,replaced=0,revoked=false;
 const calls=[],old={replaceChildren(){replaced++},textContent:''};
 const document={hidden:false,activeElement:{closest:()=>editing?{}:null},addEventListener(){},
  querySelector:s=>s==='[data-vendor-refresh]'?{dataset:{revisionUrl:'/revision'}}:old,
  querySelectorAll:()=>[old]};
 vm.runInNewContext(source,{document,location:{href:'/my-truck'},setInterval:fn=>{tick=fn},
  DOMParser:class{parseFromString(){return{querySelector:()=>({childNodes:['updated']})}}},
  fetch:async(url,options)=>{calls.push({url,options});return{status:revoked?403:200,ok:!revoked,json:async()=>({revision}),text:async()=>'<readonly>'}}});
 await settle();revision=2;editing=true;await tick();assert.equal(replaced,0);
 editing=false;await tick();assert.equal(replaced,1);await tick();assert.equal(replaced,1);
 revoked=true;await tick();assert.match(old.textContent,/revoked/);
 assert.ok(calls.every(c=>!c.options.method||c.options.method==='GET'));
});
