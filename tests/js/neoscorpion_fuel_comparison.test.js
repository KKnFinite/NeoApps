const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/js/neoscorpion_fuel_dispatch_live.js','utf8');
const element=()=>({classList:{values:new Set(),toggle(k,on){on?this.values.add(k):this.values.delete(k);},contains(k){return this.values.has(k);}}});

test('autosave and APU responses apply and clear the same warning on both values',()=>{
    const neo=element(),required=element(),mobileNeo=element(),mobileRequired=element();
    required.value='unsaved fuel-load draft';
    const values={'[data-dispatch-neo-fuel]':neo,'[data-dispatch-required-fuel]':required,
        '[data-dispatch-mobile-neo-fuel]':mobileNeo,'[data-dispatch-mobile-required-fuel]':mobileRequired};
    const row={querySelector:s=>values[s]};
    const context={root:{querySelector:()=>row}};
    const start=source.indexOf('    const updateNeoFuel =');
    const end=source.indexOf('\n    const saveDispatcherCheck =',start);
    vm.runInNewContext(source.slice(start,end)+'\nthis.update=updateNeoFuel;',context);
    for(const [available,mismatch] of [[true,true],[true,false],[true,true],[false,true],[true,false]]){
        context.update('7',{available,mismatch,display:'25.4',required_display:'26.0'});
        for(const value of Object.values(values)) assert.equal(value.classList.contains('is-neo-fuel-mismatch'),available&&mismatch);
        assert.equal(neo.textContent,available?'25.4':'-');
        assert.equal(mobileNeo.textContent,neo.textContent);
        assert.equal(mobileRequired.textContent,'26.0');
        assert.equal(required.value,'unsaved fuel-load draft');
    }
});

test('dirty-panel comparison patch changes only the required warning class, not the input value',()=>{
    const required=element();required.value='editing';
    const neo=element();const context={currentRow:{querySelector:()=>required},nextRow:{querySelector:()=>neo}};
    const start=source.indexOf("                currentRow.querySelector('[data-dispatch-required-fuel]')");
    const end=source.indexOf('\n            }',start);
    for(const on of [true,false]){
        neo.classList.toggle('is-neo-fuel-mismatch',on);
        vm.runInNewContext(source.slice(start,end),context);
        assert.equal(required.classList.contains('is-neo-fuel-mismatch'),on);
        assert.equal(required.value,'editing');
    }
});
