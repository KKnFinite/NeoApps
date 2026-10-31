'use strict';
const test=require('node:test'), assert=require('node:assert/strict');
const vm=require('node:vm'), fs=require('node:fs');
const source=fs.readFileSync('app/static/js/csrf.js','utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function harness() {
    let now=1800000000000, renewHandler=null, mutationHandler=null;
    const events={},winEvents={},calls=[],timers=new Map();let nextTimer=0,notice=null;
    class Element {
        constructor(){this.attrs={};this.dataset={};this.children=[];this.value='';this.disabled=false;}
        getAttribute(key){return this.attrs[key]??null;} setAttribute(key,value){this.attrs[key]=value;}
        removeAttribute(key){delete this.attrs[key];} remove(){notice=null;}
    }
    class Form extends Element {
        constructor(){super();this.attrs={method:'post',action:'/save'};this.fields=[];this.isConnected=true;this.submissions=0;this.handlers=0;}
        querySelectorAll(selector){return selector.includes('type=') ? this.fields.filter(field=>field.type==='hidden'&&field.name!=='csrf_token') : this.fields.filter(field=>field.name==='csrf_token');}
        prepend(field){this.fields.unshift(field);}
        requestSubmit(submitter){dispatch(this,submitter);}
    }
    const meta={content:`${now/1000}.nonce.signature`,dataset:{ttl:'7200',authenticated:'true',renewUrl:'/csrf-token'}};
    const forms=[];
    const document={hidden:false,forms,body:{prepend(el){notice=el;}},
        querySelector(selector){return selector.startsWith('meta')?meta:notice;},
        createElement(){return new Element();},addEventListener(type,fn){events[type]=fn;}};
    const response=(status,data,headers={})=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json',...headers}});
    const window={addEventListener(type,fn){winEvents[type]=fn;},fetch:async(input,init={})=>{
        const url=input instanceof Request?input.url:String(input);calls.push({url,init,input});
        if(url.includes('/csrf-token'))return renewHandler?renewHandler():response(200,{csrf_token:`${now/1000}.nonce.renewed`,expires_in:7200});
        return mutationHandler?mutationHandler():response(200,{ok:true});
    }};
    const context={window,document,location:{href:'http://neo.test/board',origin:'http://neo.test'},
        HTMLFormElement:Form,Request,Response,Headers,URL,DOMException,AbortController,
        Date:{now:()=>now},setTimeout(fn,ms){const id=++nextTimer;timers.set(id,{fn,ms});return id;},clearTimeout(id){timers.delete(id);}};
    vm.runInNewContext(source,context);
    function dispatch(form,submitter){
        const event={target:form,submitter,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},stopImmediatePropagation(){this.stopped=true;}};
        events.submit(event);
        if(!event.stopped)form.handlers++;
        if(!event.defaultPrevented)form.submissions++;
        return event;
    }
    function form(){const form=new Form();forms.push(form);const csrf=new Element();csrf.name='csrf_token';csrf.value=meta.content;form.fields.push(csrf);
        const draft=new Element();draft.name='notes';draft.value='Keep operator draft';form.fields.push(draft);
        const button=new Element();button.form=form;button.name='decision';button.value='complete';return {form,draft,button};}
    return {window,meta,calls,events,winEvents,timers,forms,response,form,dispatch,
        advance(ms){now+=ms;},renew(fn){renewHandler=fn;},mutate(fn){mutationHandler=fn;},notice:()=>notice};
}

test('fresh fetch uses current meta token and preserves request options',async()=>{
    const h=harness();h.meta.content='current.token.signature';const body='original-body';
    await h.window.fetch('/save',{method:'PATCH',body,headers:{'X-CSRF-Token':'stale','If-Match':'revision-7'}});
    assert.equal(h.calls.length,1);assert.equal(h.calls[0].init.headers.get('X-CSRF-Token'),h.meta.content);
    assert.equal(h.calls[0].init.headers.get('If-Match'),'revision-7');assert.equal(h.calls[0].init.body,body);
});
test('proactive renewal before expiry is sparse, no-store and session bound',async()=>{
    const h=harness();const initialTimer=[...h.timers.values()][0];assert.equal(initialTimer.ms,6900000);
    h.advance(6900000);h.winEvents.focus();await settle();
    assert.equal(h.calls.length,1);const sent=h.calls[0].init;
    assert.equal(sent.cache,'no-store');assert.equal(sent.credentials,'same-origin');assert.equal(sent.redirect,'error');
    assert.equal(sent.headers['X-CSRF-Renewal'],'1');assert.match(h.window.neoAppsCsrfToken,/renewed/);
});
test('expired fetch waits for renewal and sends exactly one mutation',async()=>{
    const h=harness();h.advance(3*3600000);const {draft}=h.form();
    await h.window.fetch('/save',{method:'POST',body:'draft'});
    assert.equal(h.calls.length,2);assert.equal(h.calls[1].init.headers.get('X-CSRF-Token'),h.meta.content);
    assert.equal(draft.value,'Keep operator draft');assert.match(h.forms[0].fields[0].value,/renewed/);
});
test('concurrent autosaves share renewal without losing independent bodies',async()=>{
    const h=harness();h.advance(3*3600000);let finish;
    h.renew(()=>new Promise(resolve=>{finish=resolve;}));
    const a=h.window.fetch('/a',{method:'POST',body:'A'}),b=h.window.fetch('/b',{method:'POST',body:'B'});
    await settle();assert.equal(h.calls.length,1);
    finish(h.response(200,{csrf_token:'new.shared.token',expires_in:7200}));await Promise.all([a,b]);
    assert.equal(h.calls.length,3);assert.deepEqual(h.calls.slice(1).map(call=>call.init.body),['A','B']);
});
test('native expired submit preserves submitter and drafts and blocks duplicate clicks',async()=>{
    const h=harness();h.advance(3*3600000);const {form,draft,button}=h.form();
    const event=h.dispatch(form,button);h.dispatch(form,button);
    assert.equal(event.defaultPrevented,true);assert.equal(form.handlers,0);await settle();
    assert.equal(form.submissions,1);assert.equal(form.handlers,1);assert.equal(button.value,'complete');
    assert.equal(button.disabled,false);assert.equal(draft.value,'Keep operator draft');assert.equal(h.calls.length,1);
});
test('new dynamic form receives a current hidden token',()=>{
    const h=harness();const {form,button}=h.form();form.fields=[];h.dispatch(form,button);
    assert.equal(form.fields[0].name,'csrf_token');assert.equal(form.fields[0].value,h.meta.content);assert.equal(form.submissions,1);
});
test('overnight focus renews without a page reload',async()=>{
    const h=harness();h.advance(14*3600000);h.winEvents.pageshow();await settle();
    assert.equal(h.calls.length,1);assert.match(h.meta.content,/renewed/);
});
test('renewal network failure keeps drafts, sends no mutation and backs off',async()=>{
    const h=harness();h.advance(3*3600000);const {form,draft,button}=h.form();h.renew(()=>Promise.reject(Error('offline')));
    h.dispatch(form,button);await settle();assert.equal(form.submissions,0);assert.equal(draft.value,'Keep operator draft');
    assert.match(h.notice().textContent,/Nothing was submitted/);
    await assert.rejects(h.window.fetch('/save',{method:'POST'}));assert.equal(h.calls.length,1);
});
test('expired authentication stops automatic retries',async()=>{
    const h=harness();h.advance(3*3600000);h.renew(()=>h.response(401,{error:'Sign in again'}));
    await assert.rejects(h.window.fetch('/save',{method:'POST'}),/Sign in again/);
    h.advance(3*3600000);h.winEvents.focus();await settle();assert.equal(h.calls.length,1);
});
for(const status of [403,409,422])test(`ordinary ${status} is returned unchanged without retry`,async()=>{
    const h=harness();h.mutate(()=>h.response(status,{error:'Authority or validation'}));
    const response=await h.window.fetch('/save',{method:'POST'});assert.equal(response.status,status);assert.equal(h.calls.length,1);assert.equal(h.notice(),null);
});
test('explicit CSRF rejection is identifiable but never replayed',async()=>{
    const h=harness();h.mutate(()=>h.response(400,{code:'csrf_rejected',request_executed:false},{'X-NeoApps-CSRF-Rejected':'1'}));
    const response=await h.window.fetch('/save',{method:'POST'});assert.equal(response.status,400);assert.equal(h.calls.length,1);
    assert.match(h.notice().textContent,/rejected before execution/);
});
test('ambiguous mutation network failure never retries',async()=>{
    const h=harness();h.mutate(()=>Promise.reject(Error('connection lost')));
    await assert.rejects(h.window.fetch('/save',{method:'POST'}));assert.equal(h.calls.length,1);
});
test('external requests receive neither token nor renewal',async()=>{
    const h=harness();h.advance(3*3600000);await h.window.fetch('https://outside.test/send',{method:'POST'});
    assert.equal(h.calls.length,1);assert.equal(h.calls[0].init.headers,undefined);
});
test('Request objects keep their original identity and body',async()=>{
    const h=harness(),request=new Request('http://neo.test/save',{method:'PUT',body:'request-body',headers:{'If-Match':'original-revision'}});
    await h.window.fetch(request);assert.equal(h.calls[0].input,request);assert.equal(h.calls[0].init.headers.get('If-Match'),'original-revision');
});
test('a caller abort during renewal prevents sending its mutation',async()=>{
    const h=harness();h.advance(3*3600000);const controller=new AbortController();controller.abort();
    await assert.rejects(h.window.fetch('/save',{method:'POST',signal:controller.signal}),{name:'AbortError'});
    assert.equal(h.calls.length,1);
});

test('native revision changes during renewal require review rather than submitting',async()=>{
    const h=harness();h.advance(3*3600000);const {form,button,draft}=h.form();
    const revision={type:'hidden',name:'expected_revision',value:'old'};form.fields.push(revision);
    let finish;h.renew(()=>new Promise(resolve=>{finish=resolve;}));h.dispatch(form,button);await settle();
    revision.value='new';finish(h.response(200,{csrf_token:'new.shared.token',expires_in:7200}));await settle();
    assert.equal(form.submissions,0);assert.match(h.notice().textContent,/form changed/);assert.equal(draft.value,'Keep operator draft');
});
