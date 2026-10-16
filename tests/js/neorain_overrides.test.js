const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('app/static/js/neorain_overrides.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function board(field = 'etd') {
  const events = {}, requests = [];
  const roots = [];
  for (let i = 0; i < 2; i++) {
    const message = {textContent:''};
    const root = {dataset:{rainOverrideMission:'12',rainOverrideField:field,rainOverrideVersion:'v1'},
      isConnected:true,classList:{toggle(){}},getAttribute:() => 'ETD',
      querySelector:selector => selector === '[data-rain-override-time]' ? input :
        selector === '[data-rain-override-message]' ? message : null};
    const input = {value:'01:25',disabled:false,readOnly:false,select(){},
      matches:selector => selector.includes('[data-rain-override-time]'),
      closest:selector => selector === '[data-rain-override-mission]' ? root : null};
    roots.push({root,input,message});
  }
  const button = {disabled:false,closest:selector => selector === '[data-rain-reset-selected]' ? button : null};
  const tools = {querySelector:selector => selector === '[data-rain-reset-selected]' ? button : {textContent:''}};
  vm.runInNewContext(code, {
    document:{addEventListener:(name,fn) => {events[name]=fn;},querySelector:() => tools,
      querySelectorAll:selector => selector === '[data-rain-override-mission]' ? roots.map(r => r.root) : []},
    window:{confirm:() => true},
    fetch:(_url,options) => new Promise(resolve => requests.push({body:JSON.parse(options.body),resolve}))
  });
  const emit = (name,target=roots[0].input,extra={}) => events[name]({target,...extra});
  const input = value => {roots[0].input.value=value;emit('input');};
  const reply = async (index,changes={}) => {
    const payload = {ok:true,value:'',cleared:true,version:'v2',source:'NEO',row:{etd:'06:00'},...changes};
    requests[index].resolve({ok:payload.ok,json:async () => payload});
    await tick();
  };
  return {roots,requests,emit,input,reply,button};
}

test('only an intentional empty edit clears on blur; partial and invalid entries never save',async () => {
  const b=board();
  b.roots[0].input.value='';b.emit('focusout');await tick();
  assert.equal(b.requests.length,0);
  for(const raw of ['12','2400','12:99']){b.input(raw);b.emit('focusout');await tick();}
  assert.equal(b.requests.length,0);
  b.input('');await tick();assert.equal(b.requests.length,0);
  b.emit('focusout');await tick();
  assert.equal(b.requests.length,1);assert.equal(b.requests[0].body.action,'clear');
  assert.equal(b.requests[0].body.value,null);
  b.emit('focusout');await tick();assert.equal(b.requests.length,1);
});

test('clear suppresses ETD planned fallback, updates mirrors and releases refresh protection',async () => {
  const b=board();b.input('');b.emit('focusout');await tick();await b.reply(0);
  assert.equal(b.roots[0].input.value,'');assert.equal(b.roots[1].input.value,'');
  assert.equal(b.roots[0].root.dataset.rainOverrideDirty,undefined);
  assert.equal(b.roots[0].message.textContent,'Cleared.');
});

test('queued clear uses prior save version and an old save response cannot resurrect the draft',async () => {
  const b=board();b.input('0125');await tick();
  b.input('');b.emit('focusout');
  await b.reply(0,{cleared:false,value:'01:25',row:{etd:'01:25'}});
  assert.equal(b.roots[0].input.value,'');assert.equal(b.requests.length,2);
  assert.equal(b.requests[1].body.expected_version,'v2');
  await b.reply(1,{version:'v3'});
  assert.equal(b.roots[0].input.value,'');assert.equal(b.roots[0].root.dataset.rainOverrideDirty,undefined);
});

test('clear replies preserve newer partial drafts and later valid saves normalize normally',async () => {
  const b=board();b.input('');b.emit('focusout');await tick();
  b.input('12');await b.reply(0);
  assert.equal(b.roots[0].input.value,'12');assert.equal(b.roots[0].root.dataset.rainOverrideDirty,'true');
  b.input('1430');await tick();
  assert.equal(b.requests[1].body.expected_version,'v2');
  await b.reply(1,{cleared:false,value:'14:30',version:'v3',row:{etd:'14:30'}});
  assert.equal(b.roots[0].input.value,'14:30');
});

test('failed or conflicted clear stays visibly unsaved and can be retried',async () => {
  const b=board();b.input('');b.emit('focusout');await tick();
  await b.reply(0,{ok:false,error:'Changed by another operator.'});
  assert.equal(b.roots[0].input.value,'');assert.equal(b.roots[0].root.dataset.rainOverrideDirty,'true');
  assert.equal(b.roots[0].message.textContent,'Changed by another operator.');
  b.emit('focusout');await tick();assert.equal(b.requests.length,2);
});

test('pointer Reset remains a separate single action and restores upstream/planned fallback',async () => {
  const b=board();b.emit('focusin');b.input('');
  b.emit('pointerdown',b.button);b.emit('focusout',b.roots[0].input,{relatedTarget:null});
  b.emit('click',b.button);await tick();
  assert.equal(b.requests.length,1);assert.equal(b.requests[0].body.action,'reset');
  await b.reply(0,{cleared:false,value:'',row:{etd:'06:00'}});
  assert.equal(b.roots[0].input.value,'06:00');
});

test('canceling a Reset pointer gesture saves the intentional clear',async () => {
  const b=board();b.emit('focusin');b.input('');b.emit('pointerdown',b.button);
  b.emit('focusout');b.emit('pointerup',b.roots[0].input);await tick();
  assert.equal(b.requests.length,1);assert.equal(b.requests[0].body.action,'clear');
});
