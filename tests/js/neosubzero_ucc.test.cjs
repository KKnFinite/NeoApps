/* Focused live-revision and rejected-save regression harness; real UI is covered
   separately by Chromium/WebKit against the Flask application. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture() {
    const listeners = {};
    const classList = {add(){}, remove(){}, toggle(){}};
    const feedback = {hidden:true, textContent:'', classList};
    const currentLink = {hidden:true};
    let replacements = 0, forecastReplacements = 0, poll, serverRevision = 'r2', getCount = 0;
    const board = {replaceWith(){ replacements++; }};
    const upcoming = {replaceWith(){ forecastReplacements++; }};
    const root = {
        dataset:{revision:'r1',revisionUrl:'/neosubzero/ucc/revision',refreshIntervalMs:'1000',refreshStatus:'{}'}, classList,
        querySelectorAll(){return [];},
        querySelector(selector){
            return {'[data-ucc-feedback]':feedback,'[data-ucc-current-link]':currentLink,'[data-ucc-live-board]':board,'[data-ucc-upcoming]':upcoming}[selector] || null;
        },
        addEventListener(name, callback){listeners[name] = callback;},
    };
    const next = {dataset:{revision:'r2'},querySelectorAll(){return [];},querySelector(selector){return ['[data-ucc-live-board]','[data-ucc-upcoming]'].includes(selector) ? {} : null;}};
    const document = {body:{classList},documentElement:{},querySelector(){return root;},addEventListener(){},activeElement:null};
    const window = {
        matchMedia(){return {matches:false};},localStorage:{getItem(){return null;}},
        setTimeout,clearTimeout,scrollY:100,scrollTo(){},location:{origin:'http://fixture.test',href:'http://fixture.test/neosubzero/ucc'},
        NeoLiveUpdates:{create(options){poll=options.poll;return {setServerStatus(){}};}},
    };
    const calls=[];
    const context = {
        window,document,URL,CSS:{escape: String},AbortController,
        FormData:class {set(){}},
        DOMParser:class {parseFromString(){return {querySelector(){next.dataset.revision=serverRevision;return next;}};}},
        fetch:async (url,options={}) => {
            calls.push({url:String(url), options});
            if(options.method === 'POST')return {ok:false,json:async()=>({ok:false,error:'Staffing changed. Review current values.'})};
            getCount++;
            if(String(url).includes('/revision'))return {ok:true,json:async()=>({ok:true,revision:serverRevision,changed:true,refresh:{}})};
            return {ok:true,text:async()=>'<isolated UCC markup>'};
        },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../app/static/js/neosubzero_ucc.js'),'utf8'),context);
    return {root,feedback,currentLink,calls,listeners,poll:()=>poll(),get replacements(){return replacements;},get forecastReplacements(){return forecastReplacements;},get getCount(){return getCount;}};
}

(async () => {
    const f = fixture();
    f.root.dataset.dirty='true';
    await f.poll();
    assert.equal(f.root.dataset.revision,'r1','A deferred draft must not consume the server revision');
    assert.equal(f.replacements,0,'Staffing forms are not replaced during a draft');
    assert.equal(f.forecastReplacements,1,'Relocated forecasts stay live without replacing focused staffing forms');
    delete f.root.dataset.dirty;
    await f.poll();
    assert.equal(f.root.dataset.revision,'r2','The same pending revision reconciles after editing');
    assert.equal(f.replacements,1);
    await f.poll();
    assert.equal(f.replacements,1,'Unchanged data does not rerender forms');
    const form={dataset:{dirty:'true'},action:'http://fixture.test/neosubzero/ucc',closest(){return f.root;},querySelectorAll(){return [];},contains(){return false;}};
    let prevented=false;
    await f.listeners.submit({target:form,defaultPrevented:false,preventDefault(){prevented=true;},submitter:null});
    assert.ok(prevented,'UCC mutations stay on the current page');
    assert.equal(f.root.dataset.saving,undefined,'Rejected saves always release the saving latch');
    assert.equal(form.dataset.dirty,'true','Rejected saves preserve the draft');
    assert.match(f.feedback.textContent,/Staffing changed/);
    assert.equal(f.currentLink.hidden,false,'Conflict recovery is visible');
    assert.equal(f.calls.filter(c=>c.options.method==='POST').length,1,'No silent conflict retries');
    console.log('UCC live revision, draft preservation, visible conflict, and no-retry checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
