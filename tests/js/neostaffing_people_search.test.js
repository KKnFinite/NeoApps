const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('People search waits for typeahead, does not submit while typing, and clears explicitly', async () => {
  const listeners = {}, inputListeners = {}, clearListeners = {};
  let submitted = 0, fetches = 0;
  const input = {value:'da', setAttribute(){}, addEventListener:(k,fn)=>inputListeners[k]=fn};
  const results = {hidden:true, replaceChildren(){}, querySelectorAll:()=>[]};
  const clear = {addEventListener:(k,fn)=>clearListeners[k]=fn};
  const root = {dataset:{searchUrl:'/neostaffing/people/search'}, contains:()=>true,
    querySelector:s=>({'[data-people-search-input]':input,'[data-people-search-results]':results}[s])};
  const form = {action:'', querySelector:s=>({'[data-people-search-root]':root,'[data-people-search-input]':input,'[data-people-search-results]':results,'[data-people-search-clear]':clear}[s]),
    querySelectorAll:s=>s==='[data-people-auto-submit]'?[]:[],
    addEventListener:(k,fn)=>listeners[k]=fn, requestSubmit:()=>submitted++};
  let timer;
  vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_people_search.js','utf8'), {
    URL, URLSearchParams, FormData: class { constructor(){} },
    AbortController: class { abort(){} get signal(){return {}} },
    fetch: async()=>{fetches++;return {ok:true,json:async()=>({results:[]})}},
    window:{clearTimeout(){},setTimeout:fn=>{timer=fn;return 1;},location:{pathname:'/neostaffing/people',href:'http://local/neostaffing/people',assign(){}}},
    document:{querySelector:()=>form,addEventListener(){}},
  });
  inputListeners.input({isComposing:false});
  assert.equal(submitted,0);
  await timer();
  assert.equal(fetches,1);
  clearListeners.click();
  assert.equal(submitted,1);
});
