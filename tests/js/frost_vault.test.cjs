const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('app/static/js/frost_vault.js','utf8');
function fixture(fetch){
 let submit,poll,enabled,redirect;
 const button={disabled:false},output={textContent:'',dataset:{}};
 const form={querySelector:s=>s==='button'?button:output,reportValidity:()=>true,
  getAttribute:()=>'/neosubzero/frost-vault',addEventListener:(_,f)=>submit=f};
 const root={dataset:{statusUrl:'/status'},querySelector:s=>s==='[data-frost-upload]'?form:null};
 const window={addEventListener(){},location:{assign:u=>redirect=u},NeoLiveUpdates:{create:o=>{
  poll=o.poll;return {setServerStatus:s=>enabled=s.auto_refresh_enabled,destroy(){}};
 }}};
 vm.runInNewContext(source,{document:{readyState:'complete',querySelector:()=>root},window,fetch,
  FormData:class{},AbortController,setTimeout,clearTimeout});
 return {button,output,get enabled(){return enabled;},get redirect(){return redirect;},
  submit:()=>submit({preventDefault(){}}),poll:()=>poll()};
}
test('shared live controller is explicitly enabled',async()=>{
 const f=fixture(async()=>({ok:true,json:async()=>({documents:[]})}));
 assert.equal(f.enabled,true);await f.poll();
});
test('rejected import recovers controls and reports conflict',async()=>{
 const f=fixture(async()=>({ok:false,json:async()=>({message:'Exact duplicate already preserved.'})}));
 await f.submit();assert.equal(f.button.disabled,false);assert.match(f.output.textContent,/duplicate/);
 assert.equal(f.redirect,undefined);
});
test('duplicate clicks cannot submit concurrently and success opens catalog',async()=>{
 let release,calls=0;
 const f=fixture(()=>{calls++;return new Promise(resolve=>release=resolve);});
 const first=f.submit();await f.submit();assert.equal(calls,1);assert.equal(f.button.disabled,true);
 release({ok:true,json:async()=>({ok:true})});await first;
 assert.equal(f.redirect,'/neosubzero/frost-vault');assert.equal(f.button.disabled,false);
});
test('network failure retains an actionable error and recovers button',async()=>{
 const f=fixture(async()=>{throw Error('Network unavailable');});await f.submit();
 assert.match(f.output.textContent,/Network unavailable/);assert.equal(f.button.disabled,false);
});
