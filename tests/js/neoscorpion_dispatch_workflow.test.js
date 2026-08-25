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
    const template = fs.readFileSync(
        path.join(__dirname, "..", "..", "app", "templates", "neonodes", "neoscorpion", "fuel_dispatch.html"),
        "utf8"
    );

    assert.match(script, /const adoptFingerprint/);
    assert.match(script, /revision = Number\(payload\.revision/);
    assert.match(script, /input\.dataset\.missionId/);
    assert.doesNotMatch(script, /input\.closest\("form"\)/);
    assert.match(template, /data-autosave-field="required_fuel" data-mission-id="\{\{ row\.mission\.id \}\}"/);
    assert.match(template, /data-autosave-field="inbound_fuel" data-mission-id="\{\{ row\.mission\.id \}\}"/);
    assert.match(script, /:not\(\[data-dispatch-autosave\]\)/);
    assert.match(script, /data-autosave-failed/);
    assert.match(script, /event\.preventDefault\(\)/);
    assert.match(script, /data-dispatch-assignment-submit/);
    assert.match(script, /window\.location\.reload\(\)/);
});


test("dispatch compact rows retain authoritative copy data and exceptional fuel badges", () => {
    const template = fs.readFileSync(
        path.join(__dirname, "..", "..", "app", "templates", "neonodes", "neoscorpion", "fuel_dispatch.html"),
        "utf8"
    );

    assert.doesNotMatch(template, /<th>Truck Fuel<\/th>/);
    assert.match(template, /neoscorpion-mission-truck-bars/);
    assert.match(template, /row\.cycle_type != 'fuel'/);
    assert.match(template, /data-copy-value="\{\{ row\.load_planning_output \}\}"/);
    assert.match(template, />COPY<\/button>/);
    assert.doesNotMatch(template, /COPY LOAD PLANNING/);
    assert.doesNotMatch(template, /load_planning_note/);
    assert.match(template, /row\.aircraft_type && row\.aircraft_type != "UNKNOWN"/);
    assert.match(template, /row\.apu_allowance_lbs is none/);
    assert.match(template, /load_planning_placeholder/);
});


test("dispatch live refresh is clean except for changed assignment resource selections", () => {
    const script = readScript("neoscorpion_fuel_dispatch_live.js");
    const dispatchTemplate = fs.readFileSync(
        path.join(__dirname, "..", "..", "app", "templates", "neonodes", "neoscorpion", "fuel_dispatch.html"),
        "utf8"
    );
    const assetsTemplate = fs.readFileSync(
        path.join(__dirname, "..", "..", "app", "templates", "neonodes", "neoscorpion", "_nightly_assets.html"),
        "utf8"
    );

    assert.match(script, /select\[name='assigned_fueler_user_id'\]/);
    assert.match(script, /select\[name='assigned_truck_id'\]/);
    assert.doesNotMatch(script, /LIVE UPDATE WAITING|data-assignment-live-waiting/);
    assert.doesNotMatch(script, /data-fuel-dispatch-update-banner/);
    assert.doesNotMatch(dispatchTemplate, /UPDATES AVAILABLE|REFRESH NOW/);
    assert.match(dispatchTemplate, /TOP OFF/);
    assert.match(dispatchTemplate, /RETURN/);
    assert.doesNotMatch(assetsTemplate, /Top Off Complete|value="mark_topping_off"|value="complete_top_off"/);
    assert.doesNotMatch(assetsTemplate, /value="topping_off">Topping Off/);
});


test("dispatch truck card actions stay on Fuel Dispatch and guard rapid submits", () => {
    const script = readScript("neoscorpion_fuel_dispatch_live.js");
    const template = fs.readFileSync(
        path.join(__dirname, "..", "..", "app", "templates", "neonodes", "neoscorpion", "fuel_dispatch.html"),
        "utf8"
    );

    assert.match(template, /data-dispatch-truck-card-form/);
    assert.match(template, /name="dispatch_truck_card" value="1"/);
    assert.match(script, /const submitTruckCardAction/);
    assert.match(script, /form\.dataset\.truckCardBusy === "true"/);
    assert.match(script, /form\.dataset\.truckCardBusy = "true"/);
    assert.match(script, /button\.disabled = true/);
    assert.match(script, /event\.preventDefault\(\)/);
    assert.match(script, /form\.getAttribute\("action"\)/);
    assert.match(script, /const actionUrl = form\.getAttribute\("action"\);/);
    assert.match(script, /const response = await fetch\(actionUrl, \{/);
    assert.match(script, /X-Requested-With": "XMLHttpRequest"/);
    assert.match(script, /reloadPage\(\)/);
    assert.doesNotMatch(template, /assets=open|manage-tonights-assets/);
});


test("dispatch assignment controls remain compact while silent dirty protection remains", () => {
    const script = readScript("neoscorpion_fuel_dispatch_live.js");
    const template = fs.readFileSync(
        path.join(__dirname, "..", "..", "app", "templates", "neonodes", "neoscorpion", "fuel_dispatch.html"),
        "utf8"
    );
    const css = fs.readFileSync(
        path.join(__dirname, "..", "..", "app", "static", "css", "base.css"),
        "utf8"
    );

    assert.match(script, /if \(!hasUnsavedControls\(\)\)/);
    assert.match(script, /window\.location\.reload\(\)/);
    assert.match(template, /data-dispatch-assignment-submit/);
    assert.match(template, /neoscorpion-dispatch-assignment-cell/);
    assert.match(css, /neoscorpion-dispatch-assignment-action/);
    assert.match(css, /white-space: nowrap/);
    assert.match(css, /\.neoscorpion-dispatch-save-status \{/);
    assert.doesNotMatch(css, /neoscorpion-assignment-live-waiting/);
});


test("fueler live refresh silently protects unsaved entries and reconciles when clean", () => {
    const script = readScript("neoscorpion_fuel_assignments_live.js");

    assert.match(script, /const hasUnsavedFuelEntry/);
    assert.match(script, /if \(hasUnsavedFuelEntry\(\)\)/);
    assert.match(script, /const reconcileWhenClean/);
    assert.doesNotMatch(script, /data-fuel-assignments-update-banner|REFRESH ASSIGNMENTS/);
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
