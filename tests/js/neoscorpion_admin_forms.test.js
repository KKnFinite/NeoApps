const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const script=fs.readFileSync('app/static/js/neoscorpion_admin_forms.js','utf8');
function run(draft,scope='save_settings'){
    const controls=[{name:'fuel_density_lbs_per_gallon',type:'text',value:'6.8'},
        {name:'settings_baseline',type:'hidden',value:'new-token'},
        {name:'csrf_token',type:'hidden',value:'fresh-csrf'},
        {name:'automation_enabled',type:'checkbox',value:'1',checked:true}];
    const other={name:'fuel_density_lbs_per_gallon',value:'unrelated'};
    const events={},calls=[];
    const form={dataset:{adminForm:scope},elements:controls,querySelector:()=>null};
    const error={focus:()=>calls.push('focus'),scrollIntoView:opts=>calls.push(opts.block)};
    vm.runInNewContext(script,{Object,document:{querySelectorAll:()=>[form,{dataset:{adminForm:'other'},elements:[other]}],
        querySelector:selector=>selector==='[data-admin-form-draft]'?(draft===null?null:{textContent:typeof draft==='string'?draft:JSON.stringify(draft)}):error},
        window:{addEventListener:(name,fn)=>events[name]=fn,requestAnimationFrame:fn=>fn()}});
    return {controls,other,events,calls};
}
test('failed draft retains original baseline and entries, never CSRF or other forms',()=>{
    const h=run({scope:'save_settings',values:{fuel_density_lbs_per_gallon:'6.7',settings_baseline:'old-token',csrf_token:'bad'}});
    assert.equal(h.controls[0].value,'6.7');assert.equal(h.controls[1].value,'old-token');
    assert.equal(h.controls[2].value,'fresh-csrf');assert.equal(h.other.value,'unrelated');
    assert.equal(h.controls[3].checked,false);
});
test('admin rejection scrolls feedback into view after shared navigation restoration',()=>{
    const h=run({scope:'save_settings',values:{}});assert.deepEqual(h.calls,[]);
    h.events.pageshow();assert.deepEqual(h.calls,['focus','start']);
});
test('successful navigation preserves existing scroll behavior',()=>{
    const h=run(null);h.events.pageshow();assert.deepEqual(h.calls,[]);
});
test('invalid draft JSON leaves server-rendered evidence and current values intact',()=>{
    const h=run('not JSON');assert.equal(h.controls[0].value,'6.8');h.events.pageshow();assert.deepEqual(h.calls,['focus','start']);
});
