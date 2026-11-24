const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('app/static/js/neorain_daily_briefing.js','utf8');
function page(_canEdit=true,{fallback=false,denyClipboard=false,fallbackSuccess=true}={}) {
  const handlers={};
  const workspace={addEventListener:(name,fn)=>{handlers.update=fn;},flushReportInputs:async()=>true};
  const preview={textContent:'Initial\n\nReport'}, copyStatus={textContent:''};
  const copy={addEventListener:(_,fn)=>{handlers.copy=fn;}};
  const toggle={checked:false,addEventListener:(_,fn)=>{handlers.toggle=fn;}};
  const mode={textContent:''};
  const reports={textContent:JSON.stringify({briefing:preview.textContent,
    briefing_detailed:'Initial\n\nDetailed — 10 RS - Full notes'})};
  const root={closest:()=>workspace,querySelector:s=>({'#briefing-text':preview,'#copy-briefing':copy,
    '#copy-briefing-status':copyStatus,'#briefing-delay-details':toggle,'[data-briefing-delay-mode]':mode,
    '[data-briefing-reports]':reports}[s])};
  let copied=null, area=null;
  vm.runInNewContext(code,{document:{querySelector:()=>root,getSelection:()=>null,
    activeElement:{focus(){}},body:{appendChild(){}},createElement:()=>area={style:{},select(){},remove(){}},
    execCommand:()=>{if(fallbackSuccess)copied=area.value;return fallbackSuccess;}},
    navigator:{clipboard:fallback?undefined:{writeText:async text=>{if(denyClipboard)throw Error('Denied');copied=text;}}}});
  return {preview,copyStatus,workspace,handlers,toggle,mode,copied:()=>copied};
}
test('delay toggle is OFF by default and switches synchronously without any request',async()=>{
  const p=page();assert.equal(p.toggle.checked,false);assert.equal(p.mode.textContent,'OFF');
  for(const enabled of [true,false,true,false]){
    p.toggle.checked=enabled;p.handlers.toggle();
    assert.equal(p.preview.textContent,enabled?'Initial\n\nDetailed — 10 RS - Full notes':'Initial\n\nReport');
    assert.equal(p.mode.textContent,enabled?'ON':'OFF');
    await p.handlers.copy();assert.equal(p.copied(),p.preview.textContent);
  }

});

test('unsupported or denied clipboard fallback copies the exact selected projection',async()=>{
  for(const options of [{fallback:true},{denyClipboard:true}]){
    const p=page(false,options);
    for(const enabled of [true,false]){
      p.toggle.checked=enabled;p.handlers.toggle();await p.handlers.copy();
      assert.equal(p.copied(),p.preview.textContent);assert.equal(p.copyStatus.textContent,'Copied.');

    }
  }
  const p=page(true,{fallback:true,fallbackSuccess:false});await p.handlers.copy();
  assert.match(p.copyStatus.textContent,/Copy failed/);assert.equal(p.copied(),null);
});

test('copy waits for shared autosave and uses the mode selected while saving', async()=>{
  const p=page();
  let finish;
  p.workspace.flushReportInputs=()=>new Promise(resolve=>{finish=resolve;});
  const copy=p.handlers.copy();
  assert.equal(p.copied(),null);
  p.toggle.checked=true;
  p.handlers.toggle();
  p.handlers.update({detail:{briefing:'Saved concise',briefing_detailed:'Saved detailed'}});
  assert.equal(p.preview.textContent,'Saved detailed');
  assert.equal(p.mode.textContent,'ON');
  finish(true);
  await copy;
  assert.equal(p.copied(),'Saved detailed');
});
