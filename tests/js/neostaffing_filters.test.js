const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup({invalid=false, denied=false, mobile=false}={}) {
  const events={}, timers=new Map(), moved=[], summary=[], actions=[];
  let submits=0, stored;
  const field={name:'search',value:'Worker',tagName:'INPUT',labels:[],
    getAttribute:()=>null,setAttribute(){},matches:()=>true};
  const page={disabled:false}, person={disabled:false}, button={hidden:false};
  const form={method:'get',reportValidity:()=>!invalid,requestSubmit:()=>submits++,
    addEventListener:(type,fn)=>events[type]=fn,
    querySelectorAll:s=>s.startsWith('button')?[button]:s.startsWith('[name=')?[page,person]:[field]};
  const mutation={method:'post',querySelectorAll:()=>[],addEventListener:()=>assert.fail('POST bound')};
  const panel={addEventListener(){},querySelector:()=>({focus(){}})};
  const configure={}, source={}, scope={querySelector:()=>null};
  const destinations=[{href:'http://local/neostaffing/attendance'},
    {href:'http://local/neostaffing/timecards'}, {href:'http://local/neostaffing/people?sort_id=9'},
    {href:'https://other/neostaffing/attendance'}];
  const bar={dataset:{filterUser:'42'},querySelector:s=>({
    '[data-staffing-filter-panel]':panel,'[data-staffing-filter-controls]':{append:x=>moved.push(x)},
    '[data-staffing-filter-summary]':{append:x=>summary.push(x)},
    '[data-staffing-secondary-nav]':{append:x=>moved.push(x)},
    '[data-staffing-secondary-actions]':{append:x=>actions.push(x)}
  }[s])};
  vm.runInNewContext(fs.readFileSync('app/static/js/neostaffing_filters.js','utf8'),{
    URL, setTimeout:fn=>{timers.set(1,fn);return 1;},clearTimeout:id=>timers.delete(id),
    window:{location:{href:'http://local/neostaffing/people?sort_id=2&work_area_ids=4&work_area_ids=5&page=8&person_id=99'},
      matchMedia:()=>({matches:mobile,addEventListener(){}})},
    document:{querySelector:()=>bar,body:{classList:{add(){}}},
      createElement:()=>({append(){}}),querySelectorAll:s=>({
        'form[data-staffing-filter]':[form,mutation],'[data-staffing-scope]':[scope],
        '[data-staffing-secondary-source]':[source],'[data-staffing-configure]':[configure],
        'a[data-staffing-nav]':destinations
      }[s])},
    sessionStorage:{getItem:key=>{assert.equal(key,'neostaffing.filters.v1.42');if(denied)throw Error();return JSON.stringify({'/neostaffing/timecards':[['period','week'],['person_id','666']]});},
      setItem:(_,value)=>stored=JSON.parse(value)}
  });
  return {events,field,page,person,button,panel,moved,form,mutation,actions,configure,destinations,
    submits:()=>submits,flush:()=>{for(const fn of [...timers.values()])fn();},stored};
}
test('GET filters auto-apply once, reset pagination and keep mutations unbound',()=>{
  const f=setup();assert.ok(f.moved.includes(f.form));assert.ok(!f.moved.includes(f.mutation));
  assert.equal(f.button.hidden,true);assert.deepEqual(f.actions,[f.configure]);
  f.events.input({target:f.field});f.events.change();f.flush();assert.equal(f.submits(),1);
  assert.equal(f.page.disabled,true);assert.equal(f.person.disabled,true);
  assert.equal(f.panel.open,true);
});
test('invalid or composing input never navigates',()=>{
  const f=setup({invalid:true,mobile:true});f.events.change();assert.equal(f.submits(),0);
  f.events.input({target:f.field,isComposing:true});f.flush();assert.equal(f.submits(),0);
  assert.equal(f.panel.open,false);
});
test('scope follows nav only on compatible pages with allowlisted state',()=>{
  const f=setup();assert.equal(f.destinations[0].href,'/neostaffing/attendance?sort_id=2&work_area_ids=4&work_area_ids=5');
  assert.equal(f.destinations[1].href,'/neostaffing/timecards?period=week');
  assert.equal(f.destinations[2].href,'http://local/neostaffing/people?sort_id=9');
  assert.equal(f.destinations[3].href,'https://other/neostaffing/attendance');
  assert.ok(!JSON.stringify(f.stored['/neostaffing/people']).includes('person_id'));
});
test('storage denial leaves ordinary links usable',()=>{
  const f=setup({denied:true});assert.equal(f.destinations[0].href,'http://local/neostaffing/attendance');
  f.events.change();assert.equal(f.submits(),1);
});
