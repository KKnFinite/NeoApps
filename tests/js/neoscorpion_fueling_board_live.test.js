const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness() {
    const cards = (ids, suffix = '') => ids.map(id => new Card(id, `${id}${suffix}`));
    class Card {
        constructor(id, text) { this.dataset = {boardAssignmentId: id}; this.text = text; this.parent = null; }
        isEqualNode(other) { return this.text === other.text; }
        remove() { if (this.parent) { const p = this.parent; p.children.splice(p.children.indexOf(this), 1); this.parent = null; } }
        replaceWith(next) { const p = this.parent, index = p.children.indexOf(this); this.parent = null; p.children[index] = next; next.parent = p; }
    }
    class List {
        constructor(children) { this.children = []; children.forEach(child => this.appendChild(child)); }
        querySelectorAll() { return this.children.filter(child => child.dataset?.boardAssignmentId); }
        querySelector() { return null; }
        appendChild(child) { child.remove?.(); this.children.push(child); child.parent = this; }
        insertBefore(child, before) { child.remove?.(); const index = before ? this.children.indexOf(before) : this.children.length;
            assert.ok(index >= 0); this.children.splice(index, 0, child); child.parent = this; }
    }
    const currentList = new List(cards(['a', 'b']));
    const stableA = currentList.children[0];
    const currentHead = {innerHTML: 'NIGHT', replaceWith() { throw new Error('Unexpected header replacement'); }};
    const currentPanel = {querySelector(selector) { return selector === '[data-board-list]' ? currentList : currentHead; }};
    const nextList = new List([new Card('b', 'b-updated'), new Card('a', 'a'), new Card('c', 'c')]);
    const nextPanel = {querySelector(selector) { return selector === '[data-board-list]' ? nextList : {innerHTML: 'NIGHT'}; }};
    const requests = [];
    let controller;
    const root = {dataset: {revisionUrl: '/revision', panelUrl: '/panel', operationId: '7', revision: '1', refreshIntervalMs: '10000'},
        querySelector(selector) { return selector === '[data-fueling-board-panel]' ? currentPanel : null; }};
    const context = {document: {querySelector: () => root, createElement() { return {set innerHTML(value) {this.html = value;},
        get content() { return {querySelector: () => nextPanel}; }}; }},
        NeoLiveUpdates: {create(options) { controller = options; return {setServerStatus() {}}; }},
        fetch(url) { return new Promise(resolve => requests.push({url, reply(payload) {resolve({ok: true, json: async () => payload});}})); },
        requestAnimationFrame(callback) {callback();}, scrollX: 0, scrollY: 80,
        scrollTo(x, y) {this.scrollX = x; this.scrollY = y;}};
    context.window = context;
    vm.runInNewContext(fs.readFileSync('app/static/js/neoscorpion_fueling_board_live.js', 'utf8'), context);
    return {currentList, stableA, requests, context, get controller() {return controller;}};
}

test('unchanged revision avoids a panel GET; changed revision reconciles only changed cards', async () => {
    const h = harness();
    const unchanged = h.controller.poll();
    h.requests[0].reply({ok: true, operation_id: 7, revision: 1});
    await unchanged;
    assert.equal(h.requests.length, 1);
    assert.equal(h.currentList.children[0], h.stableA);

    const changed = h.controller.poll();
    h.requests[1].reply({ok: true, operation_id: 7, revision: 2});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.requests[2].url, '/panel');
    h.requests[2].reply({ok: true, operation_id: 7, revision: 2, html: 'updated'});
    await changed;
    assert.deepEqual(h.currentList.children.map(card => card.dataset.boardAssignmentId), ['b', 'a', 'c']);
    assert.equal(h.currentList.children[1], h.stableA);
    assert.equal(h.currentList.children[0].text, 'b-updated');
    assert.equal(h.context.scrollY, 80);
    assert.equal(h.context.window.location, undefined);

    const stale = h.controller.poll();
    h.requests[3].reply({ok: true, operation_id: 7, revision: 3});
    await new Promise(resolve => setImmediate(resolve));
    h.requests[4].reply({ok: true, operation_id: 7, revision: 2, html: 'stale'});
    await stale;
    assert.equal(h.context.document.querySelector().dataset.revision, '2');
    assert.equal(h.currentList.children[1], h.stableA);
});
