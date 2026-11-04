# W1 — Explicit flight identity and live authorization

## Decision and scope

**W1's 141 original IDs all pass**, with 54 passing subtests and no skipped original case. Starting main: `c296669262d63eacbd656a96fa2002d6bfd153ef`. Fetch/fast-forward and a second pre-release fetch confirmed that SHA. Starting tracked tree was clean; unrelated untracked artwork/prototype/audit files and ignored synthetic PostgreSQL directories were preserved.

The fresh unchanged-start run had **140 failing unique IDs / 1 passing ID**, with **156 failure events**. This differs from an assumption that every original ID must still fail after W2. The final exact-ID run reports **141 passed**, 79.17 s. The [machine-readable register](2026-11-03-w1-flight-identity-evidence.json) lists every original ID, first baseline rejection, verified cause/repair, owner and final result. Subtests and failure events are not counted as additional original IDs.

**No new application defect was demonstrated in this scope. No application code, schema, permission policy or operational behavior changes are included.** This is a consolidated repair of test contracts plus additional authority acceptance coverage. It preserves completed W2/W4 application corrections. It is not a full-system green or final audit sign-off. The complete 367-ID inventory was intentionally not rerun.

## Verified contracts and corrections

### One Master → current Mission → signed slot fixture

`tests/neoermac_flight_fixture.py` creates/reuses an exact synthetic Master by gateway, Sort, flight and destination, binds its ID to the intended current mission, and selects that ID through the real signed lineup mutation service. Callers can pass an explicit Master ID. Unique destination labels are a test-only abbreviation for choosing a real option; zero/multiple matches cannot guess a mission. Invalid form values stay invalid. Production destination fallback is untouched and remains prohibited.

The affected route, geometry, linked-pull, aggregation and presence fixtures use this API. The Master and Mission exist before selecting their slot. Master-only placeholders are explicit and do not invent a mission. Missing/duplicate current Mission links and legacy unbound slots retain negative coverage. A later return flight gets its own Master rather than renaming a mission still bound to another flight.

The canonical flight-identity suite verifies two LAX flights with different tails, parking and pull times; same-door and other-door separation; no automatic advance; stale pull/slot conflict; missing/duplicate Mission refusal; inactive Master handling; legacy backfill safety; and linked sides requiring the same Master, rather than destination text. Historical door pulls retain their Mission ownership.

Three older tests expected a finished flight to advance implicitly to another same-destination departure. They now assert that the selected flight remains selected until an explicit signed reassignment, then verify that old identity requests fail and prior evidence is preserved. No-pull aggregation fixtures clear the corresponding Master planned pull as well as the Mission field when testing a genuinely unplanned pull; otherwise the valid Master fallback still requires that pull.

`tests/neoermac_html_contracts.py` verifies actual ID-valued options, versioned owned scripts and configuration. Current destination labels remain labels in geometry/display APIs; request identity is the Master ID. It scopes upcoming-side assertions to their actual DOM subtree, excluding the adjacent mobile All section. Current HHMM acceptance, autosave controls, missing-Mission presentation and external CSP controllers replace retired markup/source literals. No script trust or CSP rule changes.

### MotherBrain HOT/ALP identity

The first rejection was `A mission with this flight number already exists in this operation.`, followed by the old test querying a nonexistent HOT tail state. This is the intended canonical normalized cross-direction identity guard, not a missing permission or a request to reintroduce duplicate flights. The [October 14 requirements](2026-10-14-neorain-issues-3-7.md) and [October 20 HOT repair](2026-10-20-ups9329-hot-import.md) explicitly require this invariant. `mission_identity.py` serializes the parent operation and PostgreSQL enforces the same rule for raw writers.

The original test ID is retained for inventory continuity, with an explanatory legacy-name comment. It now sends the colliding HOT request twice, verifies rejection, one unchanged arrival and no tail-state/parking effects, then creates a distinct HOT flight through the actual route and retains its positive tail, Block-Out, parking, presentation and repeat-request assertions. It does not waive the guard or merge opposite-direction missions.

### Rain invalid time and current Staffing authority

The real inbound override route rejects `24:00` with HTTP 400 and `{ok: false, error: 'BLOCK_IN is not a valid time.'}`. The old `invalid_block_in` code assertion predated this route contract. The test now asserts the exact controlled error, unchanged timestamp/source/version/update time and zero override/audit records. R07/R08/R03A authoritative milestone protections run unchanged alongside it.

The Ermac attendance error-sanitization fixture previously had an app role but no linked employee, management assignment or selected Door scope. It never reached the injected database failure. The fixture now uses the canonical W4 management helper and a real selected Door, retaining rollback, safe flash and credential/SQL redaction assertions. The attendance presentation fixture uses the real mapping/snapshot contract and autosave status rather than a namespace lacking `.get()` or a retired SAVE button. No current management guard was weakened.

### Scorpion live-assignment authority and revisions

New retained acceptance coverage in `test_w1_live_authority.py` and its browser counterpart verifies:

* NO CURRENT SORT returns null operation/zero revision and no-store without operational writes; current independent tail-note/tail fingerprints remain present.
* Approved active node readers may read the non-admin views under the existing policy. An Operator read grant does not confer Dispatcher mutation authority (403). Changing a configurable view minimum is not a substitute for testing actual node revocation.
* Inactive node grants remain inactive, all four live/revision paths deny, and anonymous sessions redirect.
* Another gateway, prior Sort or another Fueler's assignment returns 404 on exact GET. Forged ON requests return the specific current-Sort/owner error with zero database writes, rather than failing only because a request parameter is absent.
* A caller-supplied operation ID cannot select a foreign revision scope. Full and compact panels reject stale operation IDs with 409. Fresh revision reads scale constantly at one/fifty missions, without seeding missing asset state.

Original exact-schema assertions now include `tail_notes_revision` and Dispatch `tail_revision`; current Fueler warning/submission elements replace an obsolete status-chip assumption. Required security/tail reads are attributed, not removed. All existing physical ON/OFF/submission/event/inventory services remain unchanged.

## Original W1 results by module

| Module | Original IDs | Final |
| --- | ---: | --- |
| `test_motherbrain_routes.py` | 1 | All pass |
| `test_neoermac_building_lineup_geometry.py` | 6 | All pass |
| `test_neoermac_door_supervision.py` | 3 | All pass |
| `test_neoermac_linked_door_pulls.py` | 18 | All pass |
| `test_neoermac_pull_aggregation.py` | 9 | All pass |
| `test_neoermac_pull_integrity.py` | 12 | All pass |
| `test_neoermac_routes.py` | 66 | All pass |
| `test_neoermac_schema_sync.py` | 1 | All pass |
| `test_neoermac_tail_presence.py` | 14 | All pass |
| `test_neoermac_transport.py` | 5 | All pass |
| `test_neoermac_workspace_presentation.py` | 1 | All pass |
| `test_neorain_outbound.py` | 1 | All pass |
| `test_neoscorpion_live_assignments.py` | 4 | All pass |

## SQL and transport measurements

Fresh synthetic SQLite measurements, uncompressed response bytes; these are not measured production Neon savings. The application is unchanged, so this release claims no runtime performance improvement. The measured current contracts replace obsolete expectations:

| Workload | SELECTs | Writes/commits | Bytes |
| --- | ---: | --- | ---: |
| Lineup unchanged revision | 8 operational + 1 separately asserted vendor read | 0 / 0 | 367 |
| Lineup changed snapshot | 11 operational + 1 vendor | 0 / 0 | 16,552 |
| Lineup common-data omission | same bounded path | 0 / 0 | 16,282 |
| Door initial HTML | not this byte sample's SQL claim | no measured operational writes | 52,478 |
| Outbound full, small fixture | 14 operational + 1 vendor | 0 / 0 | 11,708 |
| Outbound one-row delta, small fixture | 14 operational + 1 vendor | 0 / 0 | 3,623 |
| Outbound full, large fixture | 14 operational + 1 vendor | 0 / 0 | 157,839 |
| Outbound one-row delta, large fixture | 14 operational + 1 vendor | 0 / 0 | 5,475 |

The profile has 2 then 50 operation Missions; the intentionally missing third Master remains a separate placeholder. Delta requests replace exactly one desktop/mobile row pair; query manifests are 286/2,439 bytes. The complete measured snapshots, repeated-destination IDs, removal/reappearance and manifest scope checks remain asserted. Delta response ceilings (3,800/5,800) reflect the actual current row/manifest representation, with an additional less-than-half-full-response assertion; SQL, zero-write and two-replaced-row assertions remain exact. No compacting application change was needed.

The original Dispatch revision fixture records 10 SELECTs: eight original reads plus the mandatory vendor boundary and tail-update fingerprint. It also independently reads the tail-note fingerprint. A fully expired-user fresh-request fixture records 11 at both one and fifty Missions, zero writes. These fixtures have different user hydration costs and are not combined into a universal budget. The Ermac pull autosave has 19 original reads plus one vendor read, with unchanged one insert/two update/one commit transaction assertions. Established warmed GETs do not write operational records; selected-door navigation may intentionally persist the user's changed preference.

## Validation and remaining failures

* Exact original W1 batch: **141 passed**, 54 passing subtests, 79.17 s; no skips. Additional strengthened Rain/authority run: **5 passed**, 4.42 s.
* Disposable PostgreSQL: **28 methods passed**, 8 passing subtests, 73.08 s. Executed `test_neoermac_postgres`, `test_neoermac_flight_identity_postgres`, `test_neorain_mission_identity_postgres`, `test_authorization_provisioning_postgres` and `test_dispatch_refresh_postgres`. Covers independent connections, first creation, conflicting same-field updates, different-door aggregation, ULD increments, identity migration/idempotence, normalized alias races and immutable historical collision compatibility, concurrent provisioning and compact/read-only Dispatch behavior. Only loopback synthetic databases/isolated schemas were used.
* JavaScript: **68 passed**, zero failed/skipped, 0.18 s. Ermac pull/live integrity, outbound transport, upcoming pulls, live inactivity and CSRF/draft tests. Affected controllers also pass `node --check`.
* Browser: **three methods**, each exercising Chromium and WebKit. Identity/mobile-desktop plus Sort rollover run: **2 passed**, 2 passing subtests, 56.11 s. Session/CSRF/node revocation run: **1 browser method passed** (with four Python acceptance methods), both engines, 30.06 s total. Real isolated HTTP sessions retain CSRF and owned CSP scripts. Browser evidence: `instance/w1/browser/ermac-flight-identity-{chromium,webkit}.png` and `w1-authority-{chromium,webkit}.png`. No production operational mutation.
* Every changed/new Python file compiles; JavaScript syntax and `git diff --check` pass. Existing datetime/ORM deprecation warnings remain visible; none were indiscriminately suppressed.
* Broader affected parent, identity, security, W2 public boundary/read budgets, W4 timecard and milestone suites were executed separately. Final result: **347 passed / 4 failed**, 382 passing subtests, 183.15 s, zero skips. All four failures are the outside-W1 cases below; no W1 or new shared-fixture regression remains. This is not the 367-case inventory.

Outside-W1 failures intentionally retained:

| Exact case | Verified first cause | Owner / next action |
| --- | --- | --- |
| `test_neoscorpion_live_assignments.py::NeoScorpionLiveAssignmentsTest::test_dispatch_performance_aggregates_events_and_refreshes_with_dispatch` | Raw fabricated event lacks immutable settings/verified participation; expects PROF inclusion despite the current evidence gate. | W3: canonical physical-event/participation fixture and independent PROF assessment. |
| `test_neoscorpion_live_assignments.py::NeoScorpionLiveAssignmentsTest::test_dispatch_performance_excludes_unreliable_events_and_other_sorts` | Same unverified raw-event setup; no eligible sample rather than the expected one. | W3; no fabricated attribution or timing. |
| `test_staffing_live_ux.py::LiveUxTest::test_current_cleanup_mobile_and_time_only_contracts` | Retired `type=time` source/presentation expectation. Current retained records/authority suites pass independently. | W5: verify current time-entry UI before updating presentation checks. |
| `test_staffing_live_ux.py::LiveUxTest::test_dashboard_order_and_normal_half_width_live_counts` | Exact old dashboard list omits the current Stats tile/order. | W5: semantic controls and current navigation verification. |

No W1 ID remains unresolved. The baseline register and these outside cases are not deleted or marked expected-failure. Recommended next package: **W3**, the plan's physical evidence/resource/PROF/timing/reporting group; then W5 presentation/asset integrity and the reserved full-inventory reconciliation. No full-system green or newly calculated 367 totals are claimed.

## Exact changed files

* `tests/test_motherbrain_routes.py`
* `tests/test_neoermac_building_lineup_geometry.py`
* `tests/test_neoermac_door_supervision.py`
* `tests/test_neoermac_linked_door_pulls.py`
* `tests/test_neoermac_pull_aggregation.py`
* `tests/test_neoermac_pull_integrity.py`
* `tests/test_neoermac_routes.py`
* `tests/test_neoermac_schema_sync.py`
* `tests/test_neoermac_tail_presence.py`
* `tests/test_neoermac_transport.py`
* `tests/test_neoermac_workspace_presentation.py`
* `tests/test_neorain_outbound.py`
* `tests/test_neoscorpion_live_assignments.py`
* `tests/neoermac_flight_fixture.py`
* `tests/neoermac_html_contracts.py`
* `tests/test_w1_live_authority.py`
* `tests/browser/test_w1_live_authority.py`
* `docs/audits/2026-11-03-w1-flight-identity-and-authorization.md`
* `docs/audits/2026-11-03-w1-flight-identity-evidence.json`

## Release and preservation

The pre-release Render LIVE identity was `c296669262d63eacbd656a96fa2002d6bfd153ef`, deployment `dep-dbl8nd8473hc73b13n4g`; W4/W2 commits remain ancestors. Only this package's verified tests/supporting documentation are staged. The release uses main AutoDeploy, with exact final SHA/deployment identity and post-release health/authorized page/log results supplied in the completion receipt. No manual deployment, hosting/credential change or production data repair is authorized or performed.

Production verification is bounded, read-only: bootstrap/Gunicorn logs, health/login, authorized Ermac/Fueler/Dispatch/Rain pages, unchanged asset identities, CSP/HSTS and rollout errors. It is not an intrusive penetration test or proof of indefinite future reliability. Production employees, flights, fueling, inventory, retained timecards, Google Sheets, historical records, R2 and FROST are preserved. Ignored synthetic PostgreSQL files remain in place; the local test server is stopped after testing without directory deletion.
