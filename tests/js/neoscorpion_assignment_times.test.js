const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/js/neoscorpion_fuel_dispatch_live.js','utf8');
const start=source.indexOf('            const oldSent=');
const end=source.indexOf('\n            if (sameVisit && currentDetails',start);
const patch=source.slice(start,end);

test('assignment receipts refresh while unrelated forms are dirty, preserving history expansion',()=>{
    let received;
    const old={outerHTML:'Truck A ASSIGNED 01:02',querySelector:()=>({open:true}),replaceWith:v=>received=v};
    const details={open:false};
    const fresh={outerHTML:'Truck B ASSIGNED 01:06',querySelector:()=>details,cloneNode(){return this;}};
    const dirty={value:'unsaved notes'};
    const context={currentDetails:{querySelector:()=>old},nextDetails:{querySelector:()=>fresh},dirty};
    vm.runInNewContext(patch,context);
    assert.equal(received,fresh);assert.equal(details.open,true);assert.equal(dirty.value,'unsaved notes');
});

test('unchanged assignment receipts are not replaced on ordinary refresh',()=>{
    let replaced=false;
    const same={outerHTML:'Truck A ASSIGNED 01:02',replaceWith(){replaced=true;}};
    vm.runInNewContext(patch,{currentDetails:{querySelector:()=>same},nextDetails:{querySelector:()=>same}});
    assert.equal(replaced,false);
});

test('missing details never fabricate an assignment timestamp',()=>{
    vm.runInNewContext(patch,{currentDetails:null,nextDetails:null});
    assert.doesNotMatch(patch,/new Date|Date\.now|setInterval/);
});
