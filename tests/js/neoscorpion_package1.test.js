const test=require('node:test'), assert=require('node:assert/strict');
const fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync('app/static/js/neoscorpion_fuel_dispatch_live.js','utf8');
function harness(){
    let finish, expire, calls=0;
    const status={},messages=[], controls=new Map();
    for(const name of ['expected_assigned_fueler_user_id','expected_assigned_truck_id','assigned_fueler_user_id','assigned_truck_id','mission_id'])
        controls.set(name,{value:name==='mission_id'?'42':'1'});
    const form={id:'assignment-42',dataset:{},action:'/assignment',elements:{namedItem:n=>controls.get(n)},
        closest:()=>({querySelector:()=>status}),querySelector:()=>null};
    const button={disabled:false,matches:()=>false,closest:()=>null};
    const context={AbortController,window:{setTimeout:fn=>(expire=fn,1),clearTimeout:()=>{}},
        root:{querySelector:()=>null},FormData:class{},setStatus:(el,msg,state)=>{assert.equal(el,status);messages.push([msg,state]);},
        fetch:(_url,opts)=>{calls++;return new Promise((resolve,reject)=>{finish=(payload,http=200)=>resolve({ok:http<400,status:http,json:async()=>payload});
            opts.signal.addEventListener('abort',()=>reject(new Error('timeout')));});},
        adoptFingerprint:()=>{}, updateAssignmentBaseline:()=>{}, updateApuAllowanceDisplay:()=>{},updateNeoFuel:()=>{},
        pendingFuelDataRefresh:false,syncDirtyState:()=>{},hasUnsavedControls:()=>true,reloadPage:()=>{throw Error('must not reload drafts');}};
    vm.createContext(context);
    vm.runInContext(source.slice(source.indexOf('    const submitAssignment ='),source.indexOf('    const submitLifecycleAction ='))+'\nglobalThis.submit=submitAssignment;',context);
    return {context,form,button,messages,finish:(...args)=>finish(...args),expire:()=>expire(),get calls(){return calls;}};
}
test('external assignment feedback is visible on rejection; drafts/baselines unchanged',async()=>{
    const h=harness(),promise=h.context.submit(h.form,h.button);
    await h.context.submit(h.form,h.button); assert.equal(h.calls,1);
    h.finish({ok:false,error:'Fuel assignment changed.'},400); await promise;
    assert.match(h.messages.at(-1)[0],/NOT SAVED.*Fuel assignment changed/);
    assert.equal(h.form.dataset.assignmentFailed,'true'); assert.equal(h.button.disabled,false);
    assert.equal(h.form.elements.namedItem('assigned_truck_id').value,'1');
});
test('assignment timeout and server failure report uncertainty without retry',async()=>{
    for(const timedOut of [true,false]){
        const h=harness(),promise=h.context.submit(h.form,h.button);
        if(timedOut)h.expire();else h.finish({},500);
        await promise;
        assert.match(h.messages.at(-1)[0],/RESULT UNKNOWN/); assert.equal(h.calls,1);
        assert.equal(h.form.dataset.assignmentSaving,'false');assert.equal(h.button.disabled,false);
    }
});
test('acknowledged unchanged assignment clears prior failure and reports no change',async()=>{
    const h=harness();h.form.dataset.assignmentFailed='true';
    const promise=h.context.submit(h.form,h.button);h.finish({ok:true,changed:false});await promise;
    assert.equal(h.form.dataset.assignmentFailed,'false');assert.equal(h.messages.at(-1)[0],'No change');
});
test('density browser validation preserves blank, rejects nonfinite and invalid values',()=>{
    let validate,message;const field={value:'',readOnly:false,addEventListener:(_e,fn)=>validate=fn,setCustomValidity:v=>message=v};
    vm.runInNewContext(fs.readFileSync('app/static/js/neoscorpion_settings_validation.js','utf8'),{document:{querySelector:()=>field}});
    for(const input of ['NaN','Infinity','-Infinity','0','-1','x','0x10','1e9999','1e-9999']){
        field.value=input;validate();assert.match(message,/finite/,input);
    }
    for(const input of ['','6.7','0.1','7','6e0']){field.value=input;validate();assert.equal(message,'',input);}
});
