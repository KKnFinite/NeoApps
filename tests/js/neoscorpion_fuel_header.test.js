const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/neoscorpion_fuel_header.js', 'utf8');

function harness(withResize = true, fluid = false) {
    const frames = [], callbacks = {}, observed = new Set();
    function header(width = 250, text = 'N919UP · UPS0910 · RFD · LAX') {
        let box;
        const line = {textContent:text, style:{}, getBoundingClientRect() {
            const style = this.style.fontSize || '36px';
            const size = style.startsWith('min(') ? Math.min(36, box.clientWidth * Number(style.match(/\* ([\d.]+)\)/)[1])) : parseFloat(style);
            return {width:size * this.textContent.length * .6};
        }};
        box = {nodeType:1,clientWidth:width,isConnected:true,line,
            matches:()=>true,querySelector:()=>line,closest() { return this; }};
        return box;
    }
    const first = header();
    const root = {nodeType:1,matches:()=>false,querySelectorAll:()=>[first],closest:()=>null};
    const context = {document:{documentElement:root, fonts:{ready:{then(fn){callbacks.fontReady=fn;}},
        addEventListener(_,fn){callbacks.fontLoaded=fn;}}},
        window:{CSS:fluid ? {supports:()=>true} : undefined,addEventListener(name,fn){callbacks[name]=fn;}},
        getComputedStyle:()=>({getPropertyValue:()=> '36px'}),
        requestAnimationFrame(fn){frames.push(fn);return frames.length;},
        MutationObserver:class {constructor(fn){callbacks.mutation=fn;}observe(){}},
        ResizeObserver:withResize ? class {constructor(fn){callbacks.resize=fn;}observe(h){observed.add(h);}unobserve(h){observed.delete(h);}} : undefined};
    vm.runInNewContext(source, context);
    function flush(){while(frames.length) frames.shift()();}
    return {first,header,callbacks,observed,flush,
        add(h){callbacks.mutation([{addedNodes:[h],target:root}]);flush();}};
}

test('shrinks complete text without clipping, then grows to preferred maximum', () => {
    const h=harness();h.flush();
    assert.ok(h.first.line.getBoundingClientRect().width <= 250);
    assert.equal(h.first.line.textContent, 'N919UP · UPS0910 · RFD · LAX');
    h.first.clientWidth=1000;h.callbacks.resize([{target:h.first}]);h.flush();
    assert.equal(h.first.line.style.fontSize, '36px');
});
test('sizes a dynamically inserted card and releases detached cards', () => {
    const h=harness(), added=h.header(190);h.add(added);
    assert.ok(added.line.getBoundingClientRect().width <= 190);
    added.isConnected=false;h.callbacks.mutation([{addedNodes:[],target:{nodeType:1,closest:()=>null}}]);
    assert.equal(h.observed.has(added), false);
});
test('closed dialog is fitted when it becomes visible', () => {
    const h=harness(), hidden=h.header(0);h.add(hidden);
    assert.equal(hidden.line.style.fontSize, undefined);
    hidden.clientWidth=220;h.callbacks.resize([{target:hidden}]);h.flush();
    assert.ok(hidden.line.getBoundingClientRect().width <= 220);
});
test('text changes and late font loading refit without touching form data', () => {
    const h=harness();h.flush();const before=h.first.line.style.fontSize;
    h.first.line.textContent+='LONGER';
    h.callbacks.mutation([{addedNodes:[],target:h.first}]);h.flush();
    assert.ok(parseFloat(h.first.line.style.fontSize)<parseFloat(before));
    h.callbacks.fontReady();h.callbacks.fontLoaded();h.flush();
    assert.ok(h.first.line.getBoundingClientRect().width <= 250);
});
test('window resize remains a safe fallback without ResizeObserver', () => {
    const h=harness(false);h.flush();h.first.clientWidth=175;h.callbacks.resize();h.flush();
    assert.ok(h.first.line.getBoundingClientRect().width <= 175);
});
test('container sizing fits immediately during resize before an animation frame', () => {
    const h=harness(true,true);h.flush();
    h.first.clientWidth=175;
    assert.ok(h.first.line.getBoundingClientRect().width <= 175);
    assert.match(h.first.line.style.fontSize, /100cqw/);
});
