const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");


const readScript = (name) => fs.readFileSync(
    path.join(__dirname, "..", "..", "app", "static", "js", name),
    "utf8"
);


test("dispatch autosave adopts its own revision and excludes autosave fields from dirty controls", () => {
    const script = readScript("neoscorpion_fuel_dispatch_live.js");

    assert.match(script, /const adoptFingerprint/);
    assert.match(script, /revision = Number\(payload\.revision/);
    assert.match(script, /:not\(\[data-dispatch-autosave\]\)/);
    assert.match(script, /data-autosave-failed/);
    assert.match(script, /event\.preventDefault\(\)/);
    assert.match(script, /data-dispatch-assignment-submit/);
    assert.match(script, /window\.location\.reload\(\)/);
});


test("fueler live refresh protects unsaved entries and supports persistent acknowledgment", () => {
    const script = readScript("neoscorpion_fuel_assignments_live.js");

    assert.match(script, /const hasUnsavedFuelEntry/);
    assert.match(script, /if \(!force && hasUnsavedFuelEntry\(\)\)/);
    assert.match(script, /updateBanner\.hidden = false/);
    assert.match(script, /data-acknowledge-assignment-update/);
    assert.match(script, /acknowledgeUpdate/);
    assert.match(script, /continuousWhileVisible: true/);
});


test("Hanzo live refresh uses the shared revision endpoint only while visible", () => {
    const script = readScript("neoscorpion_hanzo_live.js");

    assert.match(script, /data-hanzo-live/);
    assert.match(script, /cache: "no-store"/);
    assert.match(script, /continuousWhileVisible: true/);
    assert.match(script, /immediate: true/);
    assert.match(script, /window\.location\.reload\(\)/);
    assert.doesNotMatch(script, /method:\s*"POST"/);
});


test("APU allowance UI is collapsed to the effective value and keeps inline edit controls", () => {
    const dispatch = readScript("neoscorpion_fuel_dispatch_live.js");
    const editor = readScript("neoscorpion_apu_editor.js");
    const dispatchTemplate = fs.readFileSync(
        path.join(__dirname, "..", "..", "app", "templates", "neonodes", "neoscorpion", "fuel_dispatch.html"),
        "utf8"
    );
    const fuelerTemplate = fs.readFileSync(
        path.join(__dirname, "..", "..", "app", "templates", "neonodes", "neoscorpion", "fueler.html"),
        "utf8"
    );

    assert.match(dispatchTemplate, /data-dispatch-apu-effective/);
    assert.match(fuelerTemplate, /data-apu-allowance-output/);
    assert.match(dispatchTemplate, /<summary>EDIT<\/summary>/);
    assert.match(fuelerTemplate, /<summary>EDIT<\/summary>/);
    assert.doesNotMatch(dispatchTemplate, /Effective Auto/);
    assert.doesNotMatch(fuelerTemplate, /Effective Auto/);
    assert.doesNotMatch(fuelerTemplate, /Override APU/);
    assert.doesNotMatch(fuelerTemplate, /Override Allowance/);
    assert.match(dispatch, /data-dispatch-apu-reset/);
    assert.match(dispatch, /updateApuAllowanceDisplay/);
    assert.match(editor, /data-apu-editor-save/);
    assert.match(editor, /data-apu-reset/);
    assert.match(editor, /data-apu-editor-cancel/);
});
