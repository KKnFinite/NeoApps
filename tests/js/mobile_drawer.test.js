const { test } = require("node:test");
const assert = require("node:assert/strict");
const { isDismissSwipe } = require("../../app/static/js/mobile_drawer.js");
test("only deliberate horizontal right swipes dismiss", () => {
    assert.equal(isDismissSwipe(100, 8, 400), true);
    for (const args of [[5, 2, 100], [-100, 0, 300], [90, 100, 300], [100, 20, 1500]]) {
        assert.equal(isDismissSwipe(...args), false);
    }
});
