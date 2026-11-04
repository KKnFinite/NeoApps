const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function boot(classification, value = '') {
  const events = new Map();
  const date = {value, required:true, attributes:{}, setAttribute(name,value) { this.attributes[name] = value; }};
  const choice = {value:classification, name:'classification'};
  const form = {querySelector(selector) { return selector === '[name="classification"]' ? choice : date; }};
  choice.form = form;
  const document = {readyState:'complete', querySelector() { return null; },
    querySelectorAll(selector) { return selector === 'form' ? [form] : []; },
    addEventListener(name,handler) { events.set(name,handler); }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../app/static/js/staffing_people.js'),'utf8'),{document,window:{}});
  return {date,choice,change() { events.get('change')({target:choice}); }};
}

test('Seasonal immediately makes date optional without fabricating a date', () => {
  const page=boot('part_time');assert.equal(page.date.required,true);
  page.choice.value='seasonal';page.change();
  assert.equal(page.date.required,false);assert.equal(page.date.value,'');
  assert.equal(page.date.attributes['aria-required'],'false');
});
test('Leaving Seasonal restores the requirement and preserves the actual entered value', () => {
  const page=boot('seasonal','2020-01-02');assert.equal(page.date.required,false);
  page.choice.value='full_time_combo';page.change();
  assert.equal(page.date.required,true);assert.equal(page.date.value,'2020-01-02');
  assert.equal(page.date.attributes['aria-required'],'true');
});

test('transfer POST carries explicit confirmation/current CSRF and never replays uncertain writes', async () => {
  const editor=fs.readFileSync(path.join(__dirname,'../../app/static/js/neostaffing_employee_editor.js'),'utf8');
  const fragment=editor.slice(editor.indexOf('  const post = async'),editor.indexOf('  const open = async'));
  let calls=0,submitted;
  const context={AbortController,setTimeout,clearTimeout,document:{querySelector:()=>({content:'current-csrf'})},
    FormData:class extends Map {constructor(form){super(Object.entries(form));}},
    fetch:async(url,options)=>{calls++;submitted=options;return {ok:true,json:async()=>({ok:true})};}};
  vm.runInNewContext(fragment+';this.send=post;',context);
  const draft={source_assignment_id:'11',expected_assignment_version:'4',destination_work_area_id:'20'};
  await context.send('/employee/transfer',draft,{confirm_transfer:'yes'});
  assert.equal(submitted.body.get('confirm_transfer'),'yes');
  assert.equal(submitted.headers['X-CSRFToken'],'current-csrf');
  assert.equal(draft.confirm_transfer,undefined);assert.equal(draft.expected_assignment_version,'4');
  context.fetch=async()=>{calls++;throw new TypeError('lost response');};
  await assert.rejects(context.send('/employee/transfer',draft,{confirm_transfer:'yes'}),/could not be confirmed/);
  assert.equal(calls,2);
});
