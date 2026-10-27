const {test}=require('node:test');const assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs');const source=fs.readFileSync('app/static/js/frost_models.js','utf8');
function fixture({ok=false,error='Phase 5 gate blocks approval',hold=false,malformed=false}={}){
 let submit,resolve,requests=0,timeout;
 const button={disabled:false},output={textContent:''},input={value:'keep my reason'};
 const form={action:{value:'import'},getAttribute:()=>'/models',reportValidity:()=>true,querySelector:s=>output,querySelectorAll:()=>[button],addEventListener:(_,f)=>submit=f};
 const response={ok,json:async()=>{if(malformed)throw Error('html');return ok?{ok:true,message:'Saved',models:{approved_version:'v1',revision:2}}:{ok:false,error};}};
 vm.runInNewContext(source,{document:{querySelectorAll:s=>s==='[data-model-form]'?[form]:[input],querySelector:()=>null},
  FormData:class{set(){}},AbortController,setTimeout:f=>{timeout=f;return 1;},clearTimeout(){},
  fetch:url=>{assert.equal(url,'/models','An input named action must not clobber the request URL');requests++;return hold?new Promise(r=>resolve=r):Promise.resolve(response);}});
 return {button,output,input,get requests(){return requests;},submit:()=>submit({preventDefault(){},submitter:button}),resolve:()=>resolve(response)};
}
test('rejected model approval preserves entries and re-enables submission',async()=>{const f=fixture();await f.submit();assert.equal(f.button.disabled,false);assert.equal(f.input.value,'keep my reason');assert.match(f.output.textContent,/gate/);assert.match(f.output.textContent,/retained/);});
test('duplicate clicks do not create concurrent model submissions',async()=>{const f=fixture({hold:true});const pending=f.submit();await f.submit();assert.equal(f.requests,1);f.resolve();await pending;assert.equal(f.button.disabled,false);});
test('malformed response is never success',async()=>{const f=fixture({malformed:true});await f.submit();assert.match(f.output.textContent,/not confirmed/);assert.match(f.output.textContent,/retained/);});
test('successful metadata update does not execute training or refresh the page',async()=>{const f=fixture({ok:true});await f.submit();assert.match(f.output.textContent,/Saved/);assert.equal(f.button.disabled,false);assert.doesNotMatch(source,/location\.reload|train\(/);});
