const test = require("node:test");
const assert = require("node:assert/strict");
const {colorFor} = require("../../app/static/js/neoscorpion_fuel_status.js");
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
