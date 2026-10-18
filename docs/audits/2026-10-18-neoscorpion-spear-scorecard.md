# SPEAR Intelligence Scorecard

Starting main: `14a5501557028d9096d363e6f5f2b04bbb68b3a4`.

## Scope and access

Read-only `/neoscorpion/spear/scorecard`, linked from SPEAR. Existing
`neoscorpion.history.view` permission and gateway-node boundary apply. Vendor
accounts remain denied. Sort choices are gateway-scoped (latest 200 operations).
Truck and Fueler choices come from recorded identities, not current assignments.
No schema, planner, physical-work, inventory, automation or calibration changes.
The scorecard GET does not schedule the existing learning reconciliation worker.

## Evidence and mathematics

* OFF accuracy: earliest valid saved `performance_snapshot.forecasts` entry per
  finalized physical visit, recorded at/after verified ON and strictly before
  OFF. Exact historical Work ID, physical tail, cycle, truck, Fueler and ON/OFF
  must agree. Existing PROF evidence exclusions are respected. Unknown provenance,
  interruptions, defuels and missing forecasts remain visible and unscored.
* Signed error = actual physical OFF minus predicted OFF, in UTC minutes.
  Positive means optimistic; negative means conservative. Summaries expose n,
  missingness, mean absolute error, mean signed error and count within ±5 minutes.
  Multiple forecast revisions do not multiply a visit's weight.
* Risk: immutable current decision reference, its original departure deadline and
  positive fuel requirement, an exact matched executed resource pair and a unique
  qualified outcome linked to a verified physical event. Changed identities or
  requirements, stale references, post-departure observations, missing outcomes
  and counterfactual alternative choices are not scored. Latest outcome evidence
  supersedes older qualifications. One earliest comparable decision per visit.
* AT RISK and LATE are exact, separate categories using the existing SPEAR `_risk`
  boundary: LATE after captured departure; AT RISK within the preceding 20 minutes;
  COVERED at least 20 minutes early. Actual category uses physical OFF, not flight
  departure performance or administrative COMPLETE. Display TP/FP/FN/TN,
  precision and recall with explicit denominators, not personnel rankings.
* TOP OFF: immutable SENT prediction/policy and a unique matching verified RETURN.
  Duration uses actual SENT→RETURN. Missing SENT/RETURN or verification is disclosed,
  never reconstructed from settings or truck availability. No Fueler attribution is
  invented for TOP OFF; those metrics are omitted under a Fueler filter.
* Trend: chronological verified halves, at least three visits per half. No automated
  calibration changes or causal claims about Dispatcher decisions.

## Vault and limits

Canonical observation outbox records are already the source of R2 decision copies.
Delivery presence is shown without network calls or counting a second outcome.
The existing Vault operational-record viewer links to the scorecard with one
explicit archive key. Existing bounded decompression, schema, checksum, key and
metadata verification apply before use; gateway/operation scope is checked again.
Archive-only completed events reuse frozen timelines; noneligible records remain
unscored. Canonical event IDs take precedence, preventing duplicate samples.
This is not a whole-bucket scan or an assumption that the Vault has complete history.

Each database evidence source is capped at 2,000 records with a visible PARTIAL
warning. No historical prediction is regenerated using current settings. Missing
capture, missing permissions/configuration and incomplete records can legitimately
leave accuracy unavailable. Existing capture delivery is not retriggered by viewing.

## Validation

* 17 new Python math/service/permission tests, including missing/malformed records,
  independent historical identities, archive deduplication, latest qualification,
  trend minimums, stale/mismatched decisions and vendor denial.
* 2 disposable PostgreSQL tests: repeated report queries in a database-enforced
  read-only transaction; A→B→A work/event/inventory preservation.
* 17 existing decision-observation and 20 Vault integrity/client tests.
* Chromium and WebKit authenticated browser workflows: genuine filter changes,
  2/3 verified mixed-history math, risk mismatch, three physical visit identities,
  missing forecasts, TOP OFF error, Fueler-filter omission, reload, no mutation
  forms, no console errors and 1440×900 / 1920×1080 / 390×844 / 375×568 bounds.
* Python compilation, existing Dispatch JavaScript syntax and git diff checks.

Initial browser tests found intrinsic filter-width overflow at 375px; fixed with
scorecard-only minimum sizing. Initial Vault regression lacked local `boto3`;
installed the existing requirements dependency locally and reran successfully.
No production flight records were modified or fabricated for tests. Existing
operational decisions and outcomes were not retrospectively rewritten.
