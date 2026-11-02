"use strict";

const assert = require("node:assert/strict");

// Extract trusted template-owned code, never arbitrary rendered/user HTML.
// A markup change must fail the harness instead of executing a slice of Jinja.
function ownedInlineScript(template, marker) {
    const scripts = [...template.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/g)]
        .filter((match) => !/\bsrc\s*=/.test(match[1]) && match[2].includes(marker));
    assert.equal(scripts.length, 1, `Expected one inline controller containing ${marker}`);
    assert.match(scripts[0][1], /\bnonce="{{\s*csp_nonce\(\)\s*}}"/,
        "Trusted controller must retain an explicit application-owned CSP nonce");
    return scripts[0][2];
}

module.exports = {ownedInlineScript};
