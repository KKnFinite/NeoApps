const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('app/static/js/neosubzero_reporting.js','utf8');
function fixture({clipboard=true,reject=false,copy=false}={}) {
 let handler,written,field;
 const feedback={hidden:true,textContent:''};
 const button={dataset:{sprayCopy:'pass-1'},disabled:false};
 const root={querySelector:()=>feedback,addEventListener:(_,fn)=>handler=fn,append:el=>field=el};
 const document={querySelector:()=>root,getElementById:()=>({textContent:'PHYSICAL TAIL\nPASS 1\nTRUCK A | 12.5'}),
  createElement:()=>({value:'',setAttribute(){},focus(){},select(){this.selected=true;},remove(){this.removed=true;}}),execCommand:()=>copy};
 const navigator=clipboard?{clipboard:{writeText:async text=>{if(reject)throw Error('denied');written=text;}}}:{};
 vm.runInNewContext(source,{document,navigator});
 return {button,feedback,get written(){return written;},get field(){return field;},async click(){await handler({target:{closest:()=>button}});}};
}
test('copy exact current pass text and recover button',async()=>{const f=fixture();await f.click();assert.equal(f.written,'PHYSICAL TAIL\nPASS 1\nTRUCK A | 12.5');assert.equal(f.button.disabled,false);assert.equal(f.feedback.hidden,false);});
test('permission failure uses copy fallback',async()=>{const f=fixture({reject:true,copy:true});await f.click();assert.equal(f.field.removed,true);assert.match(f.feedback.textContent,/COPIED/);assert.equal(f.button.disabled,false);});
test('unsupported clipboard preserves complete selected manual fallback',async()=>{const f=fixture({clipboard:false});await f.click();assert.equal(f.field.selected,true);assert.equal(f.field.value,'PHYSICAL TAIL\nPASS 1\nTRUCK A | 12.5');assert.match(f.feedback.textContent,/manually/);});
test('repeated copy has no cached or modified data',async()=>{const f=fixture();await f.click();await f.click();assert.equal(f.written,'PHYSICAL TAIL\nPASS 1\nTRUCK A | 12.5');assert.equal(f.button.disabled,false);});
