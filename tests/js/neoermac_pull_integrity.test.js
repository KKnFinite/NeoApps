const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('app/static/js/neoermac_door_live.js', 'utf8');
function harness() {
    const input = {value: '01:45', dataset: {localDirty: 'true'}};
    const toggle = {checked: false, dataset: {}};
    const original = {value: 'old'};
    const mission = {value: '10'}, operation = {value: '7'};
    const card = {
        dataset: {}, classList: {add() {}, remove() {}, contains() { return false; }},
        querySelector(selector) {
            if (selector === '[data-pull-mission]') return mission;
            if (selector === '[data-pull-operation]') return operation;
            return selector.includes('data-pull-original') ? original : null;
        },
    };
    const context = {
        window: {}, pullKeys: ['pure'],
        findActualInput: () => input, findNoPullToggle: () => toggle,
        findPullControl: (_card, selector) => selector.includes('hhmm') ? input : toggle,
        controlIsProtected: (control) => Boolean(control?.dataset.localDirty),
        cardHasProtectedInput: () => Boolean(input.dataset.localDirty),
        applyPullAlerts() {}, updatePullFormCompleteState() {}, setCardError() {}, setCardStatus() {},
        summaryText: () => '',
    };
    const display = html.slice(html.indexOf('    const applyDoorCardState ='),
                               html.indexOf('    const renderEvents ='));
    const acknowledge = html.slice(html.indexOf('    const applyCardState ='),
                                   html.indexOf('    const savePull ='));
    vm.runInNewContext(display + acknowledge + '\nwindow.ack = applyCardState;', context);
    const state = {operation_id: 7, mission_id: 10, actual: {pure: '01:45'}, original: {pure: 'new'}};
    const submitted = {original: 'old', operation_id: '7', mission_id: '10', value: '01:45', no_pull: false};
    return {context, card, input, original, mission, state, submitted};
}

test('autosave submits displayed identity and signed per-field original', () => {
    for (const key of ['operation_id', 'mission_id', 'original']) {
        assert.ok(html.includes(`body.set("${key}"`));
    }
    const fragment = fs.readFileSync('app/templates/neonodes/neoermac/_door_pull_content.html', 'utf8');
    assert.ok(fragment.includes('name="mission_id_{{ destination_index }}"'));
    assert.ok(fragment.includes('name="original_{{ key }}_{{ destination_index }}"'));
});

test('poll never advances the original for a dirty field or rebinds its mission', () => {
    const h = harness();
    h.context.window.neoErmacApplyDoorCardState(h.card, h.state);
    assert.equal(h.original.value, 'old');
    h.context.window.neoErmacApplyDoorCardState(h.card, {...h.state, mission_id: 11});
    assert.equal(h.mission.value, '10');
    assert.equal(h.original.value, 'old');
});

test('successful save advances its original; an older acknowledgement cannot regress it', () => {
    const h = harness();
    h.context.window.ack(h.card, h.state, 'pure', h.submitted);
    assert.equal(h.original.value, 'new');
    h.context.window.ack(h.card, {...h.state, original: {pure: 'older'}}, 'pure', h.submitted);
    assert.equal(h.original.value, 'new');
});

test('acknowledgement preserves an edit typed while the request was outstanding', () => {
    const h = harness();
    h.input.value = '01:55';
    h.context.window.ack(h.card, h.state, 'pure', h.submitted);
    assert.equal(h.input.value, '01:55');
    assert.equal(h.input.dataset.localDirty, 'true');
    assert.equal(h.original.value, 'new');
});
