const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/neorain_delay_info.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture() {
    const events = {}, pending = [];
    const fields = Object.fromEntries(Object.entries({mission_id:'12', action:'add', minutes:'10', code:'RS', notes:'Keep this draft'})
        .map(([name,value]) => [name,{value}]));
    const button = {disabled:false};
    const output = {dataset:{},textContent:'',setAttribute(){}};
    const scope = {querySelector:()=>output};
    const form = {dataset:{},action:{value:'add'},getAttribute:()=>'/neorain/delay-info',
        elements:{namedItem:name=>fields[name]},
        reportValidity:()=>true,
        parentElement:{closest:()=>scope},
        closest:()=>null, querySelectorAll:()=>[button],
        removeAttribute:name=>delete form.dataset[name === 'data-neorain-delay-saving' ? 'neorainDelaySaving':'neorainDelayDirty']};
    const root = {dataset:{},contains:()=>true,addEventListener:(name,handler)=>{events[name]=handler;},
        querySelectorAll:()=>[],querySelector:()=>null};
    vm.runInNewContext(source, {
        document:{readyState:'complete',querySelector:()=>root},
        window:{setTimeout:()=>1,clearTimeout(){}}, AbortController,
        FormData:class {constructor(){}},
        fetch:(_url, options)=>new Promise((resolve,reject)=>pending.push({options,resolve,reject})),
    });
    const submit = () => events.submit({target:{closest:()=>form},preventDefault(){}});
    const input = () => events.input({target:{closest:()=>form}});
    return {form,fields,button,output,pending,submit,input};
}

test('dirty/saving state protects refresh; double clicks send only one request', async()=>{
    const f=fixture(); f.input();
    assert.equal(f.form.dataset.neorainDelayDirty,'true');
    const saved=f.submit(); await f.submit();
    assert.equal(f.pending.length,1); assert.equal(f.button.disabled,true);
    assert.equal(f.form.dataset.neorainDelaySaving,'true');
    f.pending[0].resolve({ok:true,json:async()=>({ok:true,action:'add',delay_id:45,count:1})});
    await saved;
    assert.equal(f.output.textContent,'DELAY INFO SAVED.');
    assert.equal(f.form.dataset.neorainDelayDirty,undefined);
    assert.equal(f.form.dataset.neorainDelaySaving,undefined);
    assert.equal(f.fields.notes.value,''); assert.equal(f.button.disabled,false);
});

test('permission rejection is visible and retains the complete draft for recovery',async()=>{
    const f=fixture(); const saved=f.submit();
    f.pending[0].resolve({ok:false,json:async()=>({ok:false,error:'Gateway role does not permit editing'})});
    await saved;
    assert.match(f.output.textContent,/role does not permit/);
    assert.equal(f.output.dataset.error,'true'); assert.equal(f.fields.notes.value,'Keep this draft');
    assert.equal(f.form.dataset.neorainDelayDirty,'true'); assert.equal(f.button.disabled,false);
});

test('network and timeout failures never retry or leave SAVING stuck',async()=>{
    for(const name of ['TypeError','AbortError']) {
        const f=fixture(); const saved=f.submit();
        const error=new Error('Connection interrupted'); error.name=name;
        f.pending[0].reject(error); await saved; await tick();
        assert.equal(f.pending.length,1); assert.equal(f.fields.minutes.value,'10');
        assert.equal(f.fields.notes.value,'Keep this draft'); assert.equal(f.button.disabled,false);
        assert.equal(f.form.dataset.neorainDelaySaving,undefined);
        assert.equal(f.output.dataset.error,'true'); assert.doesNotMatch(f.output.textContent,/SAVING/);
    }
});
