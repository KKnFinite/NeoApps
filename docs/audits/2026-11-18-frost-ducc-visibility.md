# FROST toggle and DUCC forecast visibility

Starting source: `339d6d8fb0bdb224635931bd7be73aacee4fe1a3`.

## Cause and correction

The Intelligence setting rejected activation because the operational inference
runtime has no independently validated physical-frost model. Independently, DUCC
never read the committed historical-model capture scores. Thus the switch could
neither be enabled for research display nor control forecast visibility.

The existing switch now permits an explicitly confirmed platform-Grandmaster
opt-in to display existing frozen historical-model scores. Actual operational
inference remains independently blocked in `frost_intelligence.operational_prediction`;
no automatic decisions, actions, learning or retraining are enabled by this change.

`frost_ducc` projects only committed READY captures for the current gateway and
compatible KRFD frozen baseline. It validates all eight model identities/hashes,
horizons, target times, commit times and numerical scores. Each matching forecast
hour shows its logistic/forest pair, taking the latest genuine capture for that
hour. Missing members remain missing; no heuristic LOW/MEDIUM/HIGH is substituted.
The KRFD models are not applied to other gateways.

New captures retain a compact display projection in their existing immutable
receipt, using the same successful archive-verification transaction. No additional
write, model calculation, weather request or historical backfill is introduced.
Existing READY archives remain unchanged: a bounded, checksum-verified read-only
compatibility path caches immutable weather projections, not permissions or HTML.
It respects memory admission, limits cached entries to 16 and backs off failed
reads for 60 seconds. Only captures within the maximum six-hour forecast lead are
queried. Turning OFF skips capture/archive reads entirely.

Fresh setting revision and READY-capture identities join the existing weather
revision. Existing normal DUCC and TV polling update the existing forecast text;
there is no new polling, CSS, layout, badge, warning panel or extra control. Normal
and TV refresh intervals are unchanged. Mobile Ramp View is preserved.

## Focused verification

- 24 Python cases passed: Intelligence (10), display/capture/weather-revision
  integration (8), existing weather (6). Includes confirmed ON/OFF, unavailable,
  corrupt, future, stale and foreign evidence; no cross-capture model mixing;
  archive checksum/cache/backoff/memory guards; no scoring on GET; unchanged
  capture idempotency and scientific operational-inference rejection.
- 3 isolated PostgreSQL cases passed: competing setting writers, transaction
  rollback and fresh platform-authority revocation; immutable security audit.
- Chromium and WebKit passed one scenario each: default OFF, confirmed GM ON,
  live normal DUCC scores, TV projection, live OFF removal, mobile overflow and
  node-Master read-only settings. Synthetic records/providers only. The fixture
  uses one-second normal/TV polling; production cadence is not changed.
- Existing DUCC JavaScript revision/draft/conflict harness passed; eight Python
  files parsed and `git diff --check` passed. Existing datetime/SQLAlchemy and
  local-browser file-close warnings were not suppressed.
- Screenshots: `instance/browser-evidence/frost-intelligence/` (local, ignored).

## Production verification and limits

Read-only pre-release evidence: RFD has no Intelligence control row (all three
defaults OFF). Latest READY slot `20261118T090000Z` has 8 scored / 0 unavailable;
slot `20261118T100000Z` retains its prior retry receipt. There is no fresh prediction
for the current evening outside the configured eligible window. Old scores are
not replayed into tonight's forecast and no synthetic production capture is run.

Release procedure: AutoDeploy this commit, verify LIVE/health/startup, then use the
authenticated platform-GM Settings form to verify both switch states and leave
display ON, learning OFF and retraining OFF. Record the exact deployment and the
authorized setting/audit result in the completion report. No direct database
setting writes, production operational mutations or historical rewrites.

Availability still depends on successful real hourly acquisition/scoring. Passing
display tests does not establish sustained overnight scoring reliability or
production cost savings. Frozen models, scientific decision gates and memory
safeguards are unchanged.
