const test = require("node:test");
const assert = require("node:assert/strict");
const {colorFor, estimateText, travelText} = require("../../app/static/js/neoscorpion_fuel_status.js");
const now = Date.parse("2026-10-09T03:00:00Z");
test("urgency crosses threshold as the clock passes without changing stage", () => {
    for (const stage of ["pending", "ready", "assigned", "off", "fob-ready"]) {
        assert.notEqual(colorFor(stage, now+31*60000, 30, NaN, now), "red");
        assert.equal(colorFor(stage, now+31*60000, 30, NaN, now+60000), "red");
        assert.equal(colorFor(stage, now-60000, 30, NaN, now), "red");
    }
});
test("fueling stays yellow without reliable timing and matches SPEAR risk boundary", () => {
    assert.equal(colorFor("fueling", now-60000, 30, NaN, now), "yellow");
    assert.equal(colorFor("fueling", now+40*60000, 30, now+20*60000, now), "yellow");
    assert.equal(colorFor("fueling", now+40*60000, 30, now+21*60000, now), "red");
    assert.equal(colorFor("fueling", now+20*60000, 30, now-60000, now+1000), "red");
});
test("terminal states stay green and review stays red regardless of ETD", () => {
    for (const etd of [NaN, now-60000, now+60000]) {
        assert.equal(colorFor("complete", etd, 30, NaN, now), "green");
        assert.equal(colorFor("fob", etd, 30, NaN, now), "green");
        assert.equal(colorFor("review", etd, 30, NaN, now), "red");
    }
});
test("physical OFF countdown changes with the clock and never shows negative minutes", () => {
    const finish = now + 12 * 60000;
    assert.equal(estimateText(finish, "23:46", now,3,"QUALIFIED SERVICE HISTORY"), "EST OFF 23:46 (in 12m) +3m PROF");
    assert.equal(estimateText(finish, "23:46", now+60001,-2,"QUALIFIED SERVICE HISTORY"), "EST OFF 23:46 (in 11m) -2m PROF");
    assert.equal(estimateText(finish, "23:46", finish-1,0,"QUALIFIED SERVICE HISTORY"), "EST OFF 23:46 (in 1m) 0m PROF");
    assert.equal(estimateText(finish, "23:46", finish), "EST OFF 23:46 (overdue 0m) · BASELINE");
    assert.equal(estimateText(finish, "23:46", finish+60000), "EST OFF 23:46 (overdue 1m) · BASELINE");
    assert.equal(estimateText(NaN, "", now), "TIMING UNKNOWN");
});
test("travel countdown never confirms physical arrival or creates ON", () => {
    assert.equal(travelText(now+120000, "ALPHA", now), "COMING FROM ALPHA · ~2 MIN");
    assert.equal(travelText(now+120000, "ALPHA", now+60001), "COMING FROM ALPHA · ~1 MIN");
    assert.equal(travelText(now, "ALPHA", now), "ARRIVAL UNCONFIRMED");
    assert.equal(travelText(now, "ALPHA", now+60000), "ARRIVAL UNCONFIRMED");
    assert.equal(travelText(NaN, "", now), "TRUCK LOCATION UNKNOWN");
});
