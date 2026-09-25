const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));

function setup() {
    const pending = [], rootEvents = {}, boardEvents = {}, timers = [];
    const doc = {activeElement: null};
    const buttons = ['east', 'west'].map(side => ({dataset: {upcomingSideButton: side},
        setAttribute(name, value) { this[name] = value; }, closest() {return this;}}));
    const panels = ['west', 'east'].map(side => ({dataset: {upcomingSide: side}, hidden: side === 'east'}));
    const status = {textContent: ''};
    const input = {value: '', dataset: {}, matches: s => s === '[data-upcoming-actual]',
        closest: () => form, blur() {if(doc.activeElement === this) doc.activeElement = null;}};
    const form = {matches: s => s === '[data-upcoming-pull-form]',
        querySelector: s => s === '[data-upcoming-actual]' ? input : status};
    const board = {replacements: [],
        querySelectorAll: s => s === '[data-upcoming-actual]' ? [input] : panels,
        addEventListener: (name, fn) => {boardEvents[name] = fn;},
        replaceChildren(next) {this.replacements.push(next); panels.forEach(p => p.hidden = false);}};
    const root = {dataset: {upcomingRevision: 'r0', refreshStatus: '{}', refreshIntervalMs: '10000',
        stateUrl: '/state', saveUrl: '/save'},
        querySelector: s => s === '[data-upcoming-pulls-board-host]' ? board : null,
        querySelectorAll: () => buttons, addEventListener: (name, fn) => {rootEvents[name] = fn;}};
    doc.querySelector = () => root;
    let poll;
    const context = {document: doc, URL, setTimeout: fn => timers.push(fn),
        FormData: class {constructor() {this.value = input.value;}},
        DOMParser: class {parseFromString(html) {return {querySelector: () => ({html})};}},
        window: {location: {origin: 'http://test'}, NeoLiveUpdates: {create(options) {
            poll = options.poll; return {setServerStatus() {}};
        }}},
        fetch(url, options) {return new Promise(resolve => pending.push({url: String(url), options,
            resolve: (payload, ok=true) => resolve({ok, json: async () => payload})}));}};
    vm.runInNewContext(fs.readFileSync('app/static/js/neoermac_upcoming_pulls.js', 'utf8'), context);
    return {pending, doc, input, status, board, panels, buttons, timers, poll,
        side(name) {rootEvents.click({target:buttons.find(b=>b.dataset.upcomingSideButton === name)});},
        change(value) {input.value=value; boardEvents.change({target:input});},
        key(key) {boardEvents.keydown({target:input,key,preventDefault(){}});},
        blur() {doc.activeElement=null;boardEvents.focusout({target:input});timers.splice(0).forEach(fn=>fn());}};
}
const state = revision => ({ok:true,changed:true,revision,board_html:'board-'+revision,refresh:{}});

test('WEST defaults; EAST toggle survives replacement and can switch back', async () => {
    const h=setup(); assert.equal(h.panels[0].hidden,false); assert.equal(h.panels[1].hidden,true);
    h.side('east'); assert.equal(h.buttons[0]['aria-pressed'],'true');
    const p=h.poll();h.pending[0].resolve(state('r1'));await p;
    assert.equal(h.board.replacements.length,1);
    assert.equal(h.panels[0].hidden,true);assert.equal(h.panels[1].hidden,false);
    h.side('west');assert.equal(h.panels[0].hidden,false);
});

test('focused or dirty inputs suppress polling, including hidden-side edits', async () => {
    const h=setup();h.doc.activeElement=h.input;await h.poll();assert.equal(h.pending.length,0);
    h.input.value='12:';h.doc.activeElement=null;h.side('east');await h.poll();
    assert.equal(h.input.value,'12:');assert.equal(h.pending.length,0);
    h.key('Escape');assert.equal(h.input.value,'');assert.equal(h.pending.length,1);
    h.pending[0].resolve(state('r1'));await tick();assert.equal(h.board.replacements.length,1);
});

test('poll already in flight cannot erase a new edit or acknowledge its revision', async () => {
    const h=setup();const p=h.poll();h.input.value='12:18';h.doc.activeElement=h.input;
    h.pending[0].resolve(state('r1'));await p;assert.equal(h.board.replacements.length,0);
    h.input.value='';h.doc.activeElement=null;const next=h.poll();
    assert.equal(new URL(h.pending[1].url).searchParams.get('revision'),'r0');
    h.pending[1].resolve(state('r1'));await next;assert.equal(h.board.replacements.length,1);
});

test('valid change autosaves once then immediately refreshes and retains side', async () => {
    const h=setup();h.side('east');h.doc.activeElement=h.input;h.change('12:18');h.key('Enter');
    assert.equal(h.pending.length,1);assert.equal(h.pending[0].options.method,'POST');
    assert.equal(h.pending[0].options.body.value,'12:18');assert.equal(h.status.textContent,'Saving…');
    h.pending[0].resolve({ok:true,filled:3});await tick();
    assert.equal(h.input.value,'');assert.equal(h.pending.length,2);
    h.pending[1].resolve(state('r2'));await tick();
    assert.equal(h.board.replacements.length,1);assert.equal(h.panels[1].hidden,false);
});

test('successful no-op Enter save refreshes too; late pre-save poll is ignored', async () => {
    const h=setup();const old=h.poll();h.input.value='12:18';h.key('Enter');
    h.pending[1].resolve({ok:true,filled:0});await tick();
    h.pending[0].resolve(state('old'));await old;assert.equal(h.board.replacements.length,0);
    h.pending[2].resolve(state('new'));await tick();assert.equal(h.board.replacements[0].html,'board-new');
});

test('invalid and failed saves retain typed value until retry or abandonment', async () => {
    const h=setup();h.change('24:00');assert.equal(h.pending.length,0);assert.equal(h.status.textContent,'Use HH:MM');
    h.change('12:18');h.pending[0].resolve({ok:false,error:'Current sort changed'},false);await tick();
    assert.equal(h.input.value,'12:18');assert.equal(h.status.textContent,'Current sort changed');
    await h.poll();assert.equal(h.pending.length,1);h.key('Enter');assert.equal(h.pending.length,2);
});

test('blank blur abandons an edit and resumes normal polling', async () => {
    const h=setup();h.doc.activeElement=h.input;h.blur();assert.equal(h.pending.length,1);
    h.pending[0].resolve(state('r1'));await tick();assert.equal(h.board.replacements.length,1);
});
