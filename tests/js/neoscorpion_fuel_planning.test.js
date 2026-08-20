const test = require("node:test");
const assert = require("node:assert/strict");

const {
    calculateApuAllowance,
    planFuelByTank,
    remainingReadingsComplete,
} = require("../../app/static/js/neoscorpion_fuel_planning.js");


test("immediate planner reacts to center Remaining and APU source", () => {
    const centerSource = planFuelByTank({
        aircraftType: "B757",
        required: 50,
        remaining: {ctr: 5},
        actual: {},
        apuRunning: true,
        apuAllowance: 0.5,
        apuSource: "ctr",
    });
    const leftSource = planFuelByTank({
        aircraftType: "B757",
        required: 50,
        remaining: {ctr: 5},
        actual: {},
        apuRunning: true,
        apuAllowance: 0.5,
        apuSource: "left",
    });

    assert.deepEqual(
        Object.fromEntries(
            Object.entries(centerSource).map(([code, value]) => [code, value.toFixed(1)])
        ),
        {left: "14.6", ctr: "21.3", right: "14.6"}
    );
    assert.deepEqual(
        Object.fromEntries(
            Object.entries(leftSource).map(([code, value]) => [code, value.toFixed(1)])
        ),
        {left: "15.1", ctr: "20.8", right: "14.6"}
    );
    assert.equal(
        Object.values(centerSource).reduce((sum, value) => sum + value, 0).toFixed(1),
        "50.5"
    );
});


test("preview APU allowance uses the server rounding rule", () => {
    const plannedDepartureUtc = "2026-08-18T05:00:00Z";
    const examples = [
        [Date.parse("2026-08-18T03:45:00Z"), 0.4],
        [Date.parse("2026-08-18T03:30:00Z"), 0.45],
        [Date.parse("2026-08-18T02:30:00Z"), 0.75],
    ];
    for (const [confirmedAtMs, expected] of examples) {
        assert.equal(
            calculateApuAllowance({
                plannedDepartureUtc,
                windowMinutes: 0,
                confirmedAtMs,
                rate: 0.30,
            }),
            expected
        );
    }
});


test("unconfirmed or missing APU source keeps planned values incomplete", () => {
    assert.equal(planFuelByTank({
        aircraftType: "B757",
        required: 50,
        remaining: {},
        actual: {},
        apuRunning: null,
        apuAllowance: null,
        apuSource: "",
    }), null);
    assert.equal(planFuelByTank({
        aircraftType: "B757",
        required: 50,
        remaining: {},
        actual: {},
        apuRunning: true,
        apuAllowance: 0.4,
        apuSource: "",
    }), null);
});


test("747 preview uses current Actual dependencies", () => {
    const plan = planFuelByTank({
        aircraftType: "B747-8",
        required: 180,
        remaining: {},
        actual: {main_l_out: 20, main_l_in: 50},
        apuRunning: false,
        apuAllowance: 0,
        apuSource: "",
    });

    assert.equal(plan.main_l_out, 20);
    assert.equal(plan.main_l_in, 50);
    assert.equal(plan.center_wing, 19.4);
});


test("planned preview waits for every Remaining reading and accepts explicit zero", () => {
    const tanks = ["left", "ctr", "right"];
    const remaining = {left: 0, ctr: null, right: 10};
    assert.equal(remainingReadingsComplete(tanks, remaining), false);
    remaining.ctr = 0;
    assert.equal(remainingReadingsComplete(tanks, remaining), true);
    const plan = planFuelByTank({
        aircraftType: "B757",
        required: 20,
        remaining,
        actual: {},
        apuRunning: false,
        apuAllowance: 0,
        apuSource: "",
    });
    assert.equal(plan.left, 10);
    assert.equal(plan.right, 10);
});
