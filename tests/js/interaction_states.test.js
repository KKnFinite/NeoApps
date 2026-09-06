"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const script = name => fs.readFileSync(path.join(__dirname, "../../app/static/js", name), "utf8");
const settle = () => new Promise(resolve => setImmediate(resolve));

function harness() {
    const listeners = {};
    class Element {
        constructor() { this.attributes = new Map(); this.dataset = {}; this.children = []; }
        getAttribute(key) { return this.attributes.get(key) ?? null; }
        setAttribute(key, value) { this.attributes.set(key, value); }
        removeAttribute(key) { this.attributes.delete(key); }
        closest() { return null; }
        appendChild(child) { this.children.push(child); }
        addEventListener() {}
        querySelector() { return null; }
        querySelectorAll() { return []; }
    }
    class Form extends Element {
        constructor() { super(); this.elements = []; this.method = "POST"; this.action = "/existing-action"; }
        matches(selector) { return selector === "form[data-interaction-form]" || selector === "[data-live-action]"; }
    }
    const document = {
        addEventListener(type, fn, capture = false) { (listeners[`${type}:${capture}`] ??= []).push(fn); },
        createElement() { return new Element(); },
    };
    const window = {addEventListener(type, fn) { listeners[type] = [fn]; }};
    const context = {window, document, HTMLFormElement: Form, FormData: class {},
        sessionStorage: {getItem: () => null}, fetch: () => { throw Error("unexpected request"); }};
    vm.createContext(context);
    vm.runInContext(script("interaction_states.js"), context);
    const form = new Form(), submit = new Element();
    Object.assign(submit, {type: "submit", name: "decision", value: "approve", disabled: false, form});
    form.elements.push(submit);
    return {context, window, listeners, form, submit, run: name => vm.runInContext(script(name), context)};
}

test("pending is immediate; submitter remains successful and duplicate starts are rejected", () => {
    const h = harness(), api = h.window.NeoInteraction;
    const action = api.begin(h.form, h.submit);
    assert.equal(h.form.dataset.interactionState, "pending");
    assert.equal(h.form.getAttribute("aria-busy"), "true");
    assert.equal(h.submit.getAttribute("aria-disabled"), "true");
    assert.equal(h.submit.disabled, false);
    assert.equal(h.submit.name, "decision");
    assert.equal(h.submit.value, "approve");
    assert.equal(api.begin(h.form), null);
    let prevented = false, stopped = false;
    h.listeners["submit:true"][0]({target:h.form, preventDefault(){prevented=true;}, stopImmediatePropagation(){stopped=true;}});
    assert.ok(prevented && stopped);
    action.confirmed();
    assert.equal(h.form.dataset.interactionState, "confirmed");
    assert.equal(h.form.getAttribute("aria-busy"), null);
    assert.equal(h.submit.getAttribute("aria-disabled"), null);
});

test("failure and BFCache restore original accessibility attributes without changing values", () => {
    const h = harness(), api = h.window.NeoInteraction;
    h.submit.setAttribute("aria-disabled", "false");
    api.begin(h.form).failed();
    assert.equal(h.form.dataset.interactionState, "failed");
    assert.equal(h.submit.getAttribute("aria-disabled"), "false");
    api.begin(h.form);
    h.listeners.pageshow[0]();
    assert.equal(api.isPending(h.form), false);
    assert.equal(h.form.dataset.interactionState, undefined);
    assert.equal(h.submit.value, "approve");
});

test("cancelled native submissions do not lock and repeated initialization does not stack handlers", () => {
    const h = harness();
    h.listeners["submit:false"][0]({target:h.form, defaultPrevented:true});
    assert.equal(h.window.NeoInteraction.isPending(h.form), false);
    h.run("interaction_states.js");
    assert.equal(h.listeners["submit:true"].length, 1);
    h.listeners["submit:false"][0]({target:h.form, submitter:h.submit, defaultPrevented:false});
    assert.equal(h.window.NeoInteraction.isPending(h.form), true);
});

for (const outcome of ["success", "rejected", "conflict", "network"]) {
    test(`live action ${outcome} waits for authority, never retries, and releases pending`, async () => {
        const h = harness(); let resolve, reject, requests = 0, refreshes = 0, onSubmit;
        h.context.fetch = () => { requests++; return new Promise((yes,no) => {resolve=yes; reject=no;}); };
        h.run("live_updates.js");
        h.window.NeoLiveUpdates.bindConflictSafeForms({addEventListener(type, fn){if(type==='submit') onSubmit=fn;}}, async () => {refreshes++;});
        const event = {target:h.form, preventDefault(){}};
        onSubmit(event); onSubmit(event);
        assert.equal(requests, 1);
        assert.equal(h.form.dataset.interactionState, "pending");
        assert.equal(refreshes, 0);
        if (outcome === "network") reject(Error("private transport detail"));
        else resolve({status:outcome === "success" ? 200 : outcome === "conflict" ? 409 : 422, ok:outcome === "success",
            json:async()=>({ok:outcome === "success", error:"Validated action error", conflict:{can_overwrite:false}})});
        await settle();
        assert.equal(h.form.dataset.interactionState, outcome === "success" ? "confirmed" : "failed");
        assert.equal(refreshes, outcome === "success" ? 1 : 0);
        assert.equal(h.form.dataset.liveSubmitting, "false");
        assert.equal(h.submit.getAttribute("aria-disabled"), null);
        assert.equal(requests, 1);
        assert.ok(!h.form.children.some(x => x.textContent?.includes("private transport")));
        if (outcome === "conflict") {
            const panel=h.form.children.find(x => x.className === "live-conflict-panel");
            assert.equal(panel.children[1].children.length,1);
            assert.equal(panel.children[1].children[0].textContent,"USE LATEST");
        }
    });
}
