const test = require('node:test');
const assert = require('node:assert/strict');
const {ownedInlineScript} = require('./template_scripts.cjs');

test('template harness accepts explicitly nonced owned controller only', () => {
    const source = '<script nonce="{{ csp_nonce() }}">owned();</script>';
    assert.equal(ownedInlineScript(source, 'owned()'), 'owned();');
});
test('template harness rejects missing nonce rather than executing arbitrary markup', () => {
    assert.throws(() => ownedInlineScript('<script>owned();</script>', 'owned()'), /CSP nonce/);
});
test('template harness rejects missing or ambiguous controller', () => {
    assert.throws(() => ownedInlineScript('<p>owned()</p>', 'owned()'), /Expected one/);
    const script = '<script nonce="{{ csp_nonce() }}">owned();</script>';
    assert.throws(() => ownedInlineScript(script + script, 'owned()'), /Expected one/);
});
