const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../../app/static/js/neoraiden.js"),"utf8");

test("refresh serializes requests, skips hidden tabs, and honors Retry-After", async () => {
    let pending, calls=0, now=0;
    const timers = new Map();
    const tick = () => document.visibilitychange();
    const elements = {};
    function element(name) {
        return elements[name] ||= {
            addEventListener(event,listener) { this[event] = listener; },
            options: [{value:"30"}], value:"30",
            replaceChildren() {this.innerHTML="";}
        };
    }
    const root={ dataset:{revision:"initial",state:"/neoraiden/state",windows:"[[0,130000]]",intervalMs:"15000"}, querySelector:s=>element(s.slice(6,-1)) };
    const document={hidden:false,querySelector:()=>root,addEventListener(event,listener){this[event]=listener;}};
    const fetch = (_url, args) => {
        calls++;
        assert.equal(args.headers["If-None-Match"], '"initial"');
        assert.equal(args.headers["X-Raiden-Automatic"], "1");
        return new Promise(resolve=>{pending=resolve;});
    };
    class Clock extends Date { static now() {return now;} }
    const window = {addEventListener(event, listener) { this[event] = listener; }};
    const flush = () => new Promise(resolve => setImmediate(resolve));
    vm.runInNewContext(source, {document, window, fetch,
        Date:Clock, setTimeout(fn, ms){timers.set(fn,ms);return fn;}, clearTimeout(fn){timers.delete(fn);}, AbortController});
    assert.equal(elements.countdown,undefined);
    assert.equal(elements.refresh, undefined);
    assert.equal(elements.interval, undefined);
    assert.deepEqual([...timers.values()],[15000]);
    now=14999;
    window.pageshow({persisted:true});
    assert.equal(calls,0);
    now=15000;
    tick();
    tick();
    assert.equal(calls,1);
    pending({status:429,ok:false,headers:{get:key=>key==="Retry-After"?"90":null}});
    await flush();
    now=104999;
    tick();
    assert.equal(calls,1);
    now=105000;
    document.hidden=true;
    tick();
    assert.equal(calls,1);
    document.hidden=false;
    document.visibilitychange();
    assert.equal(calls,2);
    pending({status:304,headers:{get:key=>key==="ETag"?'"initial"':null}});
    await flush();
    assert.equal(element("stale").hidden,true);
    now = 150000;
    tick();
    document.visibilitychange();
    assert.equal(calls, 2);
    assert.equal(element("refresh-status").textContent, "Outside Sort hours");
    assert.equal(timers.size,0);
});
