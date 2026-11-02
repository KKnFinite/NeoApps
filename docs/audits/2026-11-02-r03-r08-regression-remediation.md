# R03 / R08 regression remediation — November 2, 2026

## Decision and scope

**Partial remediation. R08 is corrected and independently tested; R03 remains open.**
Nine original failing case IDs now pass. A newly reproduced ALP defect that
replaced canonical manual Block-Out is corrected. Hundreds of other failures
remain visible. This release does **not** establish full-system regression
sign-off or show that every remaining failure is harmless.

Starting source, `main` and fetched `origin/main` were
`4bc6d5c2d7b8a688d53f210528cfdc73fd1e71f7`. Starting Render deployment was
`dep-dbk4uauq1p3s73bdev2g`, LIVE at that same SHA. The release SHA is the commit
containing this report; its exact LIVE identity is checked after AutoDeploy and
reported in the completion response, rather than predicted here.

All original failure IDs were rerun against an immutable Git archive of that
starting SHA, separate from the edited working tree. No branch reset or
production data repair was used. Six unrelated untracked artwork/prototype
items present at the start are excluded from this commit and from test selection.

The machine-readable [evidence register](2026-11-02-r03-r08-regression-evidence.json)
contains a classification, confidence, before/after status, capped diagnostic
and supporting explanation for **every one of the 367 original case IDs**.
An `Unresolved` classification is intentionally not a claim of obsolescence.

## Test environment and accounting

Primary Python validation uses the existing `.venv`: Python 3.13.14,
pytest 9.1.1 and Playwright 1.63.0, consistent with `tests/requirements-dev.txt`.
No dependency installation or production dependency change was needed.

An initial diagnostic run accidentally used the system pytest 8.4.2. It
reproduced all 367 unique failures but did not report subtests equivalently.
Its broad attempt was deliberately stopped after 858.49 seconds. Those logs
remain under `instance/r03`; they are **not** the primary comparison below.
The older-runtime browser security launch was also superseded. Correct-runtime
reruns preserve failures and warnings rather than translating them into passes.

| Original failing inventory only | Starting SHA | Corrected tree |
|---|---:|---:|
| Selected original case IDs | 367 | 367 |
| Unique IDs with at least one failure | 367 | 358 |
| Failure events, including subtests | 799 | 647 |
| Original IDs now wholly passing | 0 | 9 |
| New failing IDs within this inventory | — | 0 |
| Skips / collection errors | 0 / 0 | 0 / 0 |
| Pytest-reported runtime | 254.09 s | 244.57 s |
| Warnings | 132,252 | 132,438 |

Verbatim summaries are retained in the register. Pytest reported 34 passing
outcomes plus 77 passing subtests before, and 41 plus 350 after. A method can
contain both passing and failing subtests; these numbers must not be added to
the number of wholly passing methods. Likewise, JUnit's suite `tests` total
is event accounting, not the number of distinct `<testcase>` elements.

Classification totals for the original 367 IDs:

| Category | IDs | Interpretation |
|---|---:|---|
| Broken test fixture | 34 | Includes repaired fixtures and remaining precondition/mock deficiencies; remaining cases still fail. |
| Query-budget regression | 17 | R08 repaired; other reproduced count differences still require SQL attribution. |
| Outdated assertion | 5 | Explicitly verified contracts; remaining physical-visit and Remaining-only tests still need replacement coverage. |
| Actual application defect | 1 | Stale CSS checksum metadata, repaired; the additional ALP integrity defect has new dedicated tests. |
| Unresolved | 310 | Requires contract-specific investigation; not silently classified as safe. |

No test-order, timing or environment explanation was assigned to an original
Python failure without evidence. The local HTTP browser-cookie problem is
separately confirmed as a test-environment issue.

## Verified repairs

| Area | Original failure and correction | Coverage retained |
|---|---|---|
| Google Rain workbook fixture | Two fake batches omitted required `Outbound!Q3:Q50`. Added explicit removal-checkbox evidence and expected `removed` values. | Locked workbook/range validation, batching and row filtering are unchanged. |
| Completion commit-count test | Synthetic positive T/F had no truck; it also lacked trusted physical ON and Fueler handoff. The test now creates a truck and calls canonical ON, save, OFF and submission APIs before Dispatcher completion. | Exactly one completion-route commit remains asserted; no operational guard was bypassed. Eight other legacy completion cases still fail and are not declared repaired. |
| Schema instrumentation | Mocking every `Table.create()` rejected harmless `checkfirst` probes. Instrument actual SQL instead. | Zero second-pass `CREATE TABLE` and exactly two employee-recovery immutable-delete trigger reinstalls are required. |
| NeoSektor navigation | Old literal snapshot omitted the current permission-protected Stats page. Updated explicit labels/order/permissions and page count. | Every one of 256 permission subsets is exercised, including denied states, active item and preload ordering. No application permissions changed. |
| CSRF asset expectation | Test searched for the old inline fetch wrapper in `base.html`. Verify synchronous owned `csrf.js` inclusion and its current `token()` header call. | No CSRF implementation, nonce or security policy changed. |
| CSS sequence metadata | Seventeen byte-count/hash rows were stale against both Git blobs and normalized LF source. Recomputed only those row values. | Source CSS, cascade order/scopes and historical `original_*` provenance remain unchanged. Exact digest assertions remain. |
| HTTP browser sessions | Rain report and Scorpion report fixtures inherited secure-cookie defaults while serving HTTP loopback. Explicit test-only `SESSION_COOKIE_SECURE=False`. | Production HTTPS secure cookies are unchanged. The Scorpion WebKit workflow is exercised; Rain's later obsolete editor selector remains a reported failure. |

The nine repaired original IDs are enumerated in the JSON, rather than counted
as nine separate application defects. No tests were deleted, broadly skipped
or warning-suppressed.

## R08 and the ALP authority defect

`apply_alp_paste()` previously called `ensure_tail_state_for_mission()` per
aircraft. Each helper call queried and flushed. Every flush invoked the existing
mission/NeoRain-override protection listener, repeating its legitimate locked
authority reads. Those protections are required and were not removed.

The correction:

1. Match using the existing operation/direction/flight rules.
2. Refresh and lock the exact matched mission rows in stable ID order.
3. Reject changed flight/tail/origin/destination identity before mutation.
4. Prefetch tail states once, keyed by date, gateway, sort and physical tail.
5. Apply inside `no_autoflush`, retaining a single final flush/commit and the
   existing explicit-authority listener and locks.

The shared tail-state helper's default behavior is unchanged for all existing
callers. Only the batch supplies its scoped cache and defers its flush.

While investigating R08, a canonical-writer reproduction established an actual
integrity defect: MotherBrain manual **07:00 / manual** became **08:00 / alp**
after an ALP departure paste. This was not a fabricated source-flag-only test.
ALP now preserves a populated native/manual/Rain-authoritative Block-Out through
the existing R07 source guard, and also preserves primary Google ownership.
It can still populate an empty value or revise its own ALP-owned value. An
ordinary imported row cannot authorize an override. Canonical manual editing
and the independently authorized NeoRain override/reset paths remain available.

### Measured query floor

Measurements use identical synthetic arrival batches, excluding fixture setup.
The baseline implementation is loaded from the recorded starting Git blob.
Both SQLite and disposable loopback PostgreSQL produce these SELECT counts:

| Aircraft | Total SELECTs before → after | Mission SELECTs before → after |
|---:|---:|---:|
| 1 | 5 → 6 | 2 → 3 |
| 6 | 25 → 6 | 7 → 3 |
| 18 | 73 → 6 | 19 → 3 |
| 50 | 201 → 6 | 51 → 3 |

The six-query floor includes initial matching, refreshed locked matching,
tail-state prefetch and the existing three authority-listener reads. The
one-aircraft cost increases by one read to establish the stronger concurrency
boundary. A one-mission-SELECT assertion would be unsafe and incorrect here.

Individual SQLite/PostgreSQL timings and INSERT/UPDATE/COMMIT counts are retained
in the register. These are single local observations, not production latency or
Neon CU savings. Batched UPDATE/INSERT statement counts do not mean fewer
canonical aircraft rows are changed. Legitimate per-event departure/FROST
evidence work is retained; the six-query claim is specifically the measured
arrival-import path, not every possible departure workload.

Real PostgreSQL tests use separate transactions and inspect `pg_stat_activity`
to prove ALP waits on the canonical editor's lock. After release, it preserves
the freshly committed manual Block-Out. A concurrent physical-tail identity
change instead aborts the stale import. Scoped tail-state creation, duplicate
paste rejection, foreign-sort isolation, native sources, ALP-owned revisions,
R07 Google protection and explicit override/reset behavior are covered.

## Broad validation

The completed full run, before the final defensive ID-filter correction below,
reported:

```
648 failed, 4511 passed, 759 skipped, 1351768 warnings,
3212 subtests passed in 2609.67s (0:43:29)
```

There were **359 unique failing IDs**, 5,597 `<testcase>` elements, zero collection/
error events and 9,130 JUnit accounting events. Launcher wall time was 2,641.12
seconds. The prior audited full run reported 799 failure events, 4,475 passing
outcomes and 746 skips, but had fewer test modules. Passing-count differences
therefore include added coverage and must not all be credited to this patch.

The full run caught **one additional regression introduced by the first R08
patch**, `test_alp_id_map_skips_foreign_missing_and_wrong_direction_in_original_order`.
It passes on the unchanged starting snapshot. The new lock-ID construction had
indexed foreign/missing preview IDs before the original scope guard, raising
`KeyError`; repeated eligible IDs also needed deduplication for the lock set.
The final correction restores scoped ID filtering, locks each eligible ID once,
and retains the original application order, including repeated eligible rows.
The existing regression assertion was retained. It now passes in the final
**50-test focused group**. Complete original-inventory, affected ALP-route and
PostgreSQL reruns follow that correction; the 44-minute full suite was not
repeated after it. Consequently this report does not present 648 as a measured
final-tree count, or invent a fully green result by subtracting the repaired case.
The boundary test deliberately supplies malformed preview membership; no
production occurrence of that newly introduced exception was observed.

Of the 759 full-run skips, **758 require isolated PostgreSQL configuration and
one requires Linux Gunicorn**. They remain skips, not passes. Forty scoped
PostgreSQL cases were executed separately; all skipped PostgreSQL suites were
not executed during this remediation.

The corrected browser run finished with **14 passed,
1 failed, 42 passing subtests**, 9,230 warnings, 598.80 seconds. Its only failing
case is the retained Rain report legacy editor selector described below.

Final focused group: **50 passed plus 300 passing subtests**, 7,702 warnings,
15.10 seconds. Final PostgreSQL group: **40 passed plus 16 passing subtests**,
9,210 warnings, 90.91 seconds, no skips or failures. Final affected ALP routes:
**34 passed, one unchanged baseline HOT-case failure**, 449 deselected,
eight passing subtests, 22.32 seconds. Final-source Chromium/WebKit authority
rerun: **two passed**, 18.02 seconds. The final 367-case inventory reproduced
647 events across the same 358 remaining IDs, with no additional failing ID.
JavaScript:
**396 passed**, no failures/skips, 71 files, 1.726 seconds. Compilation covered
887 tracked/new Python files and syntax checking covered 144 JavaScript files;
`git diff --check` passed.

The broad run selects tracked non-browser modules and excludes the unrelated
untracked prototype. New ALP tests are additionally run explicitly, since they
were untracked at manifest creation. All provider sockets are blocked except
loopback; SQLite and disposable PostgreSQL schemas contain synthetic data only.
Browser integrations are
mocked. No production email, Google, R2, employee or aircraft mutations are test actions.

Tests run at below-normal priority with a 60-minute bound, 6 GiB test-tree RSS
ceiling and at least 4 GiB system headroom. Windows virtualenv launchers create a
second interpreter: the process-tree monitor covers both, rather than quoting
the launcher's roughly 14 MiB as pytest's memory. Tree sampling was attached
approximately two minutes into the corrected runs; system headroom was monitored
from launch. Initial inventory sampled tree peaks were 850.3 and 858.1 MiB;
the final inventory peak was 853.6 MiB. Full-suite
sampled peak tree RSS was **4,724.3 MiB (4.61 GiB)**. The resource guard did not
trigger; normal test exit released the owned process tree. These are
sampled local test measurements, not Render measurements or a leak diagnosis.

Warnings remain principally deprecated `datetime.utcnow()`/legacy ORM APIs.
They are not globally suppressed or treated as application failures. Broad
warning cleanup is outside this narrow authority/harness correction.

## Remaining risks and next work

- **R03 remains open.** Unresolved fueling cancellation/physical-visit and
  cross-mission measurement assertions need authority-specific investigation
  before operational integrity sign-off. A red legacy test is not automatically
  a production defect, nor automatically harmless.
- The pre-existing ALP manual-time overwrite may have affected historical
  records; no affected-production count was measured and none were repaired.
  Read-only review of persisted mission/audit or departure-evidence revisions,
  where available, can identify candidates. An `alp` source alone does not
  prove that a manual value was overwritten and cannot justify automatic repair.
- NeoErmac failures include fixtures that submit destination strings where
  `_set_slot_flight()` now requires explicit Master Schedule flight IDs. One
  isolated real-route reproduction returns the expected controlled error
  `Select an available Master Schedule departure flight.` Existing fixture
  helpers also assign legacy destination fields without flight identity. Do not
  restore ambiguous destination matching to satisfy them. Other Ermac failures
  remain unresolved; this diagnosis does not cover the entire group.
- The combined Google native-milestones legacy test currently fails on an
  **UNKNOWN-source Ramp Load** timestamp, before its Block-Out assertion. R07
  canonical manual Block-Out is independently verified; broader milestone
  authority must be assessed separately.
- Remaining query budgets reproduce count differences. Required security and
  authority reads have not been removed, and budgets have not been increased
  without SQL attribution.
- The legacy fuel-work constraint test expects uniqueness by assignment/tail.
  Current immutable physical visits use assignment/visit-number and permit
  NULL legacy containers. Replacement tests must cover explicit visit uniqueness
  and negative fuel, not merely remove the failing assertion.
- A canonical reproduction of the cross-mission tail-confirmation case finds
  proven **same-physical-tail** readings in Remaining only. Actual and transfer
  gallons stay NULL and FOB READY stays false. The failing empty-list assertion
  needs meaningful provenance/Actual/FOB replacement coverage; no application
  rule or assertion was changed on that basis during this run.
- Rain's report browser case targets the removed `data-rain-override-open`
  control after successful login. Current controls edit inline. Its complete
  intended workflow still needs a verified test update; no UI change was made.
- Local Windows/Python 3.13/PostgreSQL 17 is not identical to Linux/Python 3.14/
  Neon PostgreSQL 18 production. Synthetic timings do not establish production
  capacity or cost savings.

Synthetic source snapshots, logs and stopped PostgreSQL files remain ignored
locally. No blocked temporary-directory deletion was retried. No hosting,
worker-count, permission, archive-format, FROST-model, source credential or
production operational record changes are included.

## Changed files

- `app/services/alp_import.py`
- `app/services/sort_date_operations.py`
- `app/static/css/sequence.json`
- `tests/test_alp_import_batching.py`
- `tests/test_alp_import_batching_postgres.py`
- `tests/test_database_bootstrap.py`
- `tests/test_db_query_batching.py`
- `tests/test_google_rain_sheets.py`
- `tests/test_neoscorpion_dispatcher_complete.py`
- `tests/test_neosektor_navigation.py`
- `tests/test_page_assets.py`
- `tests/browser/test_neorain_reports_browser.py`
- `tests/browser/test_neoscorpion_report_actions.py`
- This report and its JSON evidence register.
