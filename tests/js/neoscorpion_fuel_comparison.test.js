const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/js/neoscorpion_fuel_dispatch_live.js','utf8');
const element=()=>({classList:{values:new Set(),toggle(k,on){on?this.values.add(k):this.values.delete(k);},contains(k){return this.values.has(k);}}});

test('autosave/APU presentation makes both values red only for a shortfall and clears stale classes',()=>{
    const neo=element(),required=element();required.value='unsaved fuel-load draft';
    const row={querySelector:s=>s==='[data-dispatch-neo-fuel]'?neo:required};
    const context={root:{querySelector:()=>row}};
    const start=source.indexOf('    const updateNeoFuel =');
    const end=source.indexOf('\n    const saveDispatcherCheck =',start);
    vm.runInNewContext(source.slice(start,end)+'\nthis.update=updateNeoFuel;',context);
    for(const [available,shortfall,excess] of [[true,true,false],[true,false,false],[true,false,true],
        [false,true,true],[true,true,false],[true,false,false]]){
        context.update('7',{available,shortfall,excess,display:'25.4'});
        assert.equal(neo.classList.contains('is-neo-fuel-shortfall'),available&&shortfall);
        assert.equal(required.classList.contains('is-neo-fuel-shortfall'),available&&shortfall);
        assert.equal(neo.classList.contains('is-neo-fuel-excess'),available&&excess);
        assert.equal(required.classList.contains('is-neo-fuel-excess'),false);
        assert.equal(neo.textContent,available?'25.4':'-');
        assert.equal(required.value,'unsaved fuel-load draft');
    }
});

test('dirty-panel comparison patch changes only the required warning class, not the input value',()=>{
    const required=element();required.value='editing';
    const neo=element();const context={currentRow:{querySelector:()=>required},nextRow:{querySelector:()=>neo}};
    const start=source.indexOf("                currentRow.querySelector('[data-dispatch-required-fuel]')");
    const end=source.indexOf('\n            }',start);
    for(const on of [true,false]){
        neo.classList.toggle('is-neo-fuel-shortfall',on);
        vm.runInNewContext(source.slice(start,end),context);
        assert.equal(required.classList.contains('is-neo-fuel-shortfall'),on);
        assert.equal(required.value,'editing');
    }
});
