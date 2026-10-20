const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/neoscorpion_fuel_dispatch_live.js', 'utf8');

test('APU cancel uses the explicitly associated input form outside a form ancestor', () => {
    const allowance = {value:'2.0', dataset:{originalValue:''}};
    const enabled = {value:'1', dataset:{originalValue:'0'}};
    const form = {elements:{namedItem:name=>name==='apu_override_allowance'?allowance:enabled}};
    const editor = {open:true,querySelector:()=>({form})};
    const cancel = {closest:s=>s==='[data-dispatch-apu-editor]'?editor:null};
    const start=source.indexOf('        const cancel = event.target.closest("[data-dispatch-apu-cancel]");');
    const end=source.indexOf('\n    });',start);
    vm.runInNewContext('(function(event){'+source.slice(start,end)+'})(event)',
        {event:{target:{closest:()=>cancel}}});
    assert.equal(editor.open,false);
    assert.equal(allowance.value,'');
    assert.equal(enabled.value,'0');
});

test('APU saved feedback closes the originating external editor, preserving the effective value', () => {
    const effective = {},enabled = {},allowance = {};
    let focused=false;
    const editor = {open:true,querySelector:()=>({focus(){focused=true;}})};
    const form = {id:'visit-7',elements:{namedItem:n=>n==='apu_override_enabled'?enabled:allowance},querySelector:()=>null};
    const button = {closest:()=>editor};
    const start=source.indexOf('    const updateApuAllowanceDisplay =');
    const end=source.indexOf('\n    const submitAssignment =',start);
    const context={root:{querySelector:()=>effective}};
    vm.runInNewContext(source.slice(start,end)+'\nthis.apply=updateApuAllowanceDisplay;',context);
    context.apply(form,{effective_apu_allowance_lbs:600,apu_override_enabled:true,apu_override_allowance_lbs:600},button);
    assert.equal(editor.open,false);
    assert.equal(effective.textContent,'APU: 0.6');
    assert.equal(allowance.value,'0.6');
    assert.equal(enabled.value,'1');
    assert.equal(focused,true);
});
