const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness({confirm=true,canEdit=true}={}) {
    const calls=[], listeners={}, panels=[], pending=[], prompts=[];
    const modes={east:{mode:2,mode_version:3,request_version:4,pending_mode:1,right_nonzero:true,available:true},
        west:{mode:1,mode_version:0,request_version:0,pending_mode:null,right_nonzero:false,available:true}};
    for(const side of ['east','west']) {
        const status={}, buttons=[];
        const panel={dataset:{conductorMode:side}, querySelector:s=>status[s] ||= {}, querySelectorAll:()=>buttons};
        for(const [action,target] of [['set','1'],['set','2'],['approve',''],['deny','']]) {
            buttons.push({dataset:{modeAction:action,modeTarget:target},
                closest:s=>s==='[data-conductor-mode]'?panel:buttons.find(b=>b.dataset.modeAction===action && b.dataset.modeTarget===target)});
        }
        panel.buttons=buttons; panels.push(panel);
    }
    const context={window:{}};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../app/static/js/neosektor_spotter_modes.js'),'utf8'),context);
    const api=context.window.NeoSektorSpotterModes.create({querySelectorAll:()=>panels,addEventListener:(key,fn)=>listeners[key]=fn}, {
        state:{ballmat_modes:modes},canEdit,confirm:message=>{prompts.push(message);return confirm;},onError:()=>{},
        send:command=>new Promise(resolve=>{calls.push(command);pending.push(resolve);}),
    });
    const click=(side,action,target='')=>listeners.click({target:panels.find(p=>p.dataset.conductorMode===side).buttons.find(b=>b.dataset.modeAction===action && b.dataset.modeTarget===target)});
    return {api,panels,modes,calls,pending,prompts,click};
}

test('approve nonempty RIGHT requires explicit confirmation and carries request/mode versions', async()=>{
    const cancelled=harness({confirm:false}); await cancelled.click('east','approve');
    assert.equal(cancelled.calls.length,0);
    const h=harness(); const work=h.click('east','approve');
    assert.equal(h.prompts.length,1);
    assert.equal(h.calls[0].expected_mode_version,3);
    assert.equal(h.calls[0].request_version,4);
    assert.equal(h.calls[0].confirm_clear_right,true);
    assert.ok(h.panels[0].buttons.every(b=>b.disabled));
    assert.equal(h.panels[1].buttons.find(b=>b.dataset.modeTarget==='2').disabled,false);
    h.pending.shift()(); await work;
});

test('direct change and denial remain separate commands; no automatic write retry', async()=>{
    const h=harness(); const deny=h.click('east','deny');
    assert.equal(h.prompts.length,0);
    assert.equal(h.calls[0].action,'deny'); h.pending.shift()(); await deny;
    const direct=h.click('west','set','2');
    assert.equal(h.calls[1].mode,2); assert.equal(h.calls[1].action,'set');
    h.pending.shift()(); await direct;
    h.modes.east.pending_mode=null; h.api.apply({ballmat_modes:h.modes});
    assert.equal(h.panels[0].buttons.find(b=>b.dataset.modeAction==='approve').hidden,true);
    const readonly=harness({canEdit:false}); await readonly.click('east','approve');
    assert.equal(readonly.calls.length,0);
});
