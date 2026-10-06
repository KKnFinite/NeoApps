const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('shared People/Org scope rail restores, collapses, and submits Sort without mutations',()=>{
 const stored=new Map(),listeners={},attributes={};let collapsed=false,submitted=0;
 const button={setAttribute:(k,v)=>attributes[k]=v};
 const item={dataset:{peopleTreeId:'unit-1'},classList:{toggle:(_,v)=>collapsed=v,contains:()=>collapsed},querySelector:s=>s.includes('button')?button:null};
 const tree={dataset:{},querySelectorAll:()=>[item],querySelector:()=>null,addEventListener:(k,fn)=>listeners[k]=fn};
 const scroll={scrollTop:0,addEventListener:(k,fn)=>listeners['scroll'+k]=fn};
 const picker={addEventListener:(k,fn)=>listeners['sort'+k]=fn};
 vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_scope_rail.js','utf8'),{document:{querySelector:s=>({'[data-people-sort-picker]':picker,'[data-people-tree]':tree,'[data-people-tree-scroll]':scroll}[s])},localStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v)},requestAnimationFrame:fn=>fn()});
 assert.equal(collapsed,true);assert.equal(attributes['aria-expanded'],false);
 listeners.click({target:{closest:()=>item,matches:()=>true}});
 assert.equal(collapsed,false);assert.equal(attributes['aria-expanded'],true);
 assert.deepEqual(JSON.parse(stored.get('neostaffing.people.hierarchy.v1')),['unit-1']);
 scroll.scrollTop=40;listeners.scrollscroll();assert.equal(stored.get('neostaffing.people.hierarchy.v1.scroll'),40);
 listeners.sortchange({target:{form:{submit:()=>submitted++}}});assert.equal(submitted,1);
});
