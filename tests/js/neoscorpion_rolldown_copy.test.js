const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync('app/static/js/neoscorpion_prof_report.js','utf8');

test('COPY REPORT uses the complete canonical export even with event details collapsed',async()=>{
    let click, copied;
    const status={textContent:''};
    const text='TRUCK ROLLDOWN\nSource ID: work:1\nUTC: 2026-10-15T05:00:00Z\nNotes: complete evidence';
    vm.runInNewContext(script,{document:{querySelector:s=>s==='[data-prof-copy]'?{addEventListener:(_,fn)=>click=fn}:
        s==='[data-prof-copy-status]'?status:{value:text}},navigator:{clipboard:{writeText:async value=>copied=value}}});
    await click();assert.equal(copied,text);assert.equal(status.textContent,'Copied');
});

test('clipboard failure keeps the CSV fallback and reports the failure',async()=>{
    let click;const status={textContent:''};
    vm.runInNewContext(script,{document:{querySelector:s=>s==='[data-prof-copy]'?{addEventListener:(_,fn)=>click=fn}:
        s==='[data-prof-copy-status]'?status:{value:'unchanged report'}},navigator:{clipboard:{writeText:async()=>{throw Error('denied');}}}});
    await click();assert.equal(status.textContent,'Copy unavailable. Use CSV download.');
});
