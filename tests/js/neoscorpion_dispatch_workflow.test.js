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
