const test = require('node:test');
const assert = require('node:assert/strict');
const {createRefreshGate, acceptSaveResponse} = require('../../app/static/js/neoscorpion_fuel_data.js');

test('clean revision changes refresh; dirty typing defers and resumes the latest revision', async () => {
    let dirty = false;
    const refreshed = [];
    const gate = createRefreshGate({isDirty: () => dirty, refresh: async revision => refreshed.push(revision)});
    await gate.changed(2);
    dirty = true;
    await gate.changed(3);
    await gate.changed(4);
    assert.deepEqual(refreshed, [2]);
    dirty = false;
    await gate.resume();
    assert.deepEqual(refreshed, [2, 4]);
    await gate.resume();
    assert.deepEqual(refreshed, [2, 4]);
});

test('typing during a refresh keeps it pending; close discards pending refresh', async () => {
    let apply = false;
    let reads = 0;
    const gate = createRefreshGate({isDirty: () => false, refresh: async () => { reads++; return apply; }});
    await gate.changed(2);
    apply = true;
    await gate.resume();
    assert.equal(reads, 2);
    apply = false;
    await gate.changed(3);
    gate.reset();
    await gate.resume();
    assert.equal(reads, 3);
});

test('successful saves and 409 replace with canonical state; stale mutation is never retried', () => {
    const rendered = [];
    const render = (state, message) => rendered.push({state, message});
    const saved = {ok: true, html: 'canonical saved card'};
    acceptSaveResponse({ok: true, status: 200}, saved, render);
    const conflict = {ok: false, html: 'current server card'};
    acceptSaveResponse({ok: false, status: 409}, conflict, render);
    assert.deepEqual(rendered, [
        {state: saved, message: 'Saved'},
        {state: conflict, message: 'DATA CHANGED · REVIEW CURRENT VALUES'},
    ]);
    assert.throws(() => acceptSaveResponse({ok: false, status: 400}, {error: 'Invalid'}, render), /Invalid/);
    assert.equal(rendered.length, 2);
});

test('closing and reopening during an old refresh does not swallow the new load', async () => {
    let finishOld;
    const reads = [];
    const gate = createRefreshGate({isDirty: () => false, refresh: revision => {
        reads.push(revision);
        return reads.length === 1 ? new Promise(resolve => { finishOld = resolve; }) : true;
    }});
    const old = gate.changed(-1);
    gate.reset();
    await gate.changed(-1);
    finishOld(true);
    await old;
    assert.deepEqual(reads, [-1, -1]);
});
