const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('app/static/js/frost_vault.js','utf8');
function fixture({status=200,result={ok:true,document_id:12},failure=null,hold=false,legacy=false}={}){
 let submit,change,poll,enabled,redirect,pending,last,requests=0,progressEvent;
 const button={disabled:false},output={textContent:'',dataset:{}},progress={hidden:true,value:0};
 const file={files:[],disabled:false,addEventListener:(_,handler)=>change=handler},filename={textContent:''};
 const form={querySelector:s=>({'[data-frost-submit]':button,'output':output,'[type=file]':file,'[data-frost-filename]':filename,'progress':progress}[s]),
  reportValidity:()=>true,setAttribute(){},removeAttribute(){},requestSubmit:()=>pending=submit({preventDefault(){}}),
  getAttribute:()=>'/neosubzero/frost-vault',addEventListener:(_,f)=>submit=f};
 button.click=()=>pending=submit({preventDefault(){}});
 if(legacy)delete form.requestSubmit;
 const root={dataset:{statusUrl:'/status'},querySelector:s=>s==='[data-frost-upload]'?form:null};
 const window={addEventListener(){},location:{href:'https://example.test/neosubzero/frost-vault',assign:u=>redirect=u},NeoLiveUpdates:{create:o=>{
  poll=o.poll;return {setServerStatus:s=>enabled=s.auto_refresh_enabled,destroy(){}};
 }}};
 class XMLHttpRequest {
  constructor(){last=this;this.upload={addEventListener:(_,fn)=>progressEvent=fn};}
  open(method,path){assert.equal(method,'POST');assert.equal(path,'/neosubzero/frost-vault');}
  setRequestHeader(){}
  send(){requests++;if(!hold)queueMicrotask(()=>failure?this['on'+failure]():this.respond());}
  respond(code=status,payload=result){this.status=code;this.responseText=JSON.stringify(payload);this.onload();}
 }
 vm.runInNewContext(source,{document:{readyState:'complete',querySelector:()=>root},window,
  fetch:async()=>({ok:true,json:async()=>({documents:[]})}),URL,XMLHttpRequest,
  FormData:class{constructor(){assert.equal(file.disabled,false);}},queueMicrotask});
 return {button,output,file,filename,progress,get requests(){return requests;},get enabled(){return enabled;},get redirect(){return redirect;},
  select(files){file.files=files;change();return pending;},respond:(...args)=>last.respond(...args),
  advance:event=>progressEvent(event),submit:()=>submit({preventDefault(){}}),poll:()=>poll()};
}
test('shared live controller is explicitly enabled',async()=>{
 const f=fixture();
 assert.equal(f.enabled,true);await f.poll();
});
test('rejected import recovers controls and reports conflict',async()=>{
 const f=fixture({status:409,result:{message:'Exact duplicate already preserved.'}});
 await f.select([{name:'season.csv'}]);assert.equal(f.button.disabled,false);assert.match(f.output.textContent,/duplicate/);
 assert.equal(f.file.files[0].name,'season.csv');assert.equal(f.file.disabled,false);
 assert.equal(f.redirect,undefined);
});
test('duplicate clicks cannot submit concurrently and success opens catalog',async()=>{
 const f=fixture({hold:true});
 const first=f.select([{name:'season.csv'}]);await f.submit();assert.equal(f.requests,1);assert.equal(f.button.disabled,true);
 f.respond();await first;
 assert.equal(f.redirect,'https://example.test/neosubzero/frost-vault?imported=12');assert.equal(f.button.disabled,false);
});
test('network failure retains an actionable error and recovers button',async()=>{
 const f=fixture({failure:'error'});await f.select([{name:'season.csv'}]);
 assert.match(f.output.textContent,/Network error/);assert.equal(f.button.disabled,false);assert.equal(f.requests,1);
});
test('native change auto-submits and reports real progress separately from processing',async()=>{
 const f=fixture({hold:true});const pending=f.select([{name:'iCloud.csv'}]);
 assert.equal(f.filename.textContent,'iCloud.csv');assert.equal(f.progress.hidden,false);
 f.advance({lengthComputable:true,loaded:25,total:100});assert.equal(f.output.textContent,'UPLOADING 25%…');
 f.advance({lengthComputable:true,loaded:100,total:100});assert.equal(f.output.textContent,'PROCESSING CSV…');
 f.respond();await pending;assert.equal(f.progress.hidden,true);
});
test('timeout and interruption retain selection and enable manual retry',async()=>{
 for(const failure of ['timeout','abort']){const f=fixture({failure});await f.select([{name:'season.csv'}]);
  assert.match(f.output.textContent,/kept/);assert.equal(f.button.textContent,'RETRY UPLOAD');
  assert.equal(f.file.files[0].name,'season.csv');assert.equal(f.button.disabled,false);assert.equal(f.requests,1);}
});
test('empty picker change does not submit',()=>{
 const f=fixture();f.select([]);assert.equal(f.requests,0);assert.equal(f.button.hidden,true);
});
test('older Safari without requestSubmit still submits through the real form button',async()=>{
 const f=fixture({legacy:true});await f.select([{name:'season.csv'}]);assert.equal(f.requests,1);
 assert.equal(f.redirect,'https://example.test/neosubzero/frost-vault?imported=12');
});
