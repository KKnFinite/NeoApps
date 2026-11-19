const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

test('both wave overrides update with canonical routes underneath and never intercept the middle bar',()=>{
    const template=fs.readFileSync('app/templates/neonodes/neosektor/driver_routing.html','utf8');
    const start=template.indexOf('    const applyRoute =');
    const end=template.indexOf('    const setRefreshStatus =',start);
    assert.ok(start>0 && end>start);
    const field=()=>({hidden:false,textContent:'',setAttribute(){},replaceChildren(...nodes){this.children=nodes;}});
    const waves={};
    for(const key of ['first','second']) {
        const elements={'[data-driver-target]':field(),'[data-driver-arrow]':field(),'[data-driver-stop]':field()};
        waves[key]={dataset:{},elements,querySelector:s=>elements[s]};
    }
    const cards=Array.from({length:3},()=>({dataset:{},hidden:false,fields:{},querySelector(s){return this.fields[s] ||= field();}}));
    const cut=field();
    const context={routingWatch:null,document:{createElement:()=>field()},root:{
        querySelector:s=>s==='[data-driver-discharge-cut]'?cut:waves[s.includes('first')?'first':'second'],
        querySelectorAll:()=>cards}};
    vm.runInNewContext(template.slice(start,end)+';this.apply=applyState',context);
    const route=(direction,state='route')=>({direction,arrow:direction==='east'?'right':'left',display_state:state,display_message:state==='all_in'?'ALL IN':'NOT ARRIVED'});
    const apply=(stop,first,second,bays=[],isCut=false)=>context.apply({routing:{stop_sign:stop,routes:{first,second},bay_priority:bays,cut_discharge:isCut}});
    apply(false,route('east'),route('west'));
    assert.equal(waves.first.elements['[data-driver-stop]'].hidden,true);
    const east={pickup:'back',side:'east',bay_name:'',status:''};
    apply(true,route('west'),route('east'),[east]);
    for(const wave of Object.values(waves)){assert.equal(wave.dataset.stopSign,'true');assert.equal(wave.elements['[data-driver-stop]'].hidden,false);}
    assert.equal(waves.first.elements['[data-driver-target]'].children[0].textContent,'West Ballmat');
    assert.equal(cards[0].fields['[data-driver-pickup]'].textContent,'DISCHARGE EAST');
    assert.equal(cut.hidden,true);
    apply(true,route('east','all_in'),route('west','not_arrived'),[],true);
    assert.equal(cut.hidden,false);assert.ok(cards.every(c=>c.hidden));
    assert.equal(waves.first.elements['[data-driver-target]'].children[0].textContent,'ALL IN');
    apply(false,route('west'),route('east'),[east]);
    assert.equal(waves.first.dataset.stopSign,'false');
    assert.equal(waves.first.elements['[data-driver-target]'].children[0].textContent,'West Ballmat');
    assert.equal(waves.second.elements['[data-driver-target]'].children[0].textContent,'East Ballmat');
    assert.equal(waves.first.elements['[data-driver-stop]'].hidden,true);
    assert.equal(waves.first.elements['[data-driver-arrow]'].hidden,false);
    assert.equal(cut.hidden,true);assert.equal(cards[0].hidden,false);
});
