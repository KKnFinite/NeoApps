# R03C — remaining NeoScorpion contracts and original R03 reconciliation

## Decision and source

**The six-case scope passes. The broader R03 backlog remains open.**

Fetched `origin/main`; local main was already synchronized at
`0534e0ba5057f05b685029c21518d678d2fda957`. Render independently reported that
same commit LIVE as deployment `dep-dbkjm9l9el5s73ek0kj0`.
There were no tracked changes. The six existing untracked artwork/prototype/
documentation items and all ignored synthetic PostgreSQL files were preserved.
No applicable AGENTS.md was found in the inspected repository instructions.

The R03B report/register and original R03/R08 register were read as evidence,
not accepted as proof that every failure was harmless. All six remaining IDs
failed again on the starting source. Their current service, model, template,
console and alert contracts were independently inspected.

**No new application defect was established in these six cases. No application
code, calculation, permission, schema, configuration, asset or operational
behavior changes are included.** Only tests and audit documentation change.
The release is the commit containing this report; its final SHA and AutoDeploy
receipt are verified after pushing, recorded under ignored `instance/r03c`,
and given in the completion response.

## Six exact cases

| Original method | Reproduced failure and root cause | Verified contract and retained protection |
| --- | --- | --- |
| `TrustedFobInheritanceTest::test_explicit_trusted_lineage_uses_apu_adjusted_fuel_and_preserves_accounting` | Expected FOB READY from a constructed legacy WorkState and source audit, with no fresh APU confirmation. That is insufficient evidence. | Now completes a real source through ON/OFF/Fueler submission/Dispatcher verification, then acknowledges its same tail on another mission. Remaining carries the exact source event; Actual/APU/T/F remain absent and FOB is rejected. Changed Remaining invalidates lineage. Fresh operator Actual and APU evidence produces the exact net fuel; insufficient net fuel still rejects FOB. An explicit sufficient FOB confirmation creates no transfer event, invents no ON/OFF and preserves the source snapshots and truck inventory. |
| `OutstandingWorkTest::test_busy_truck_preparation_is_not_original_aircraft` | Expected the busy-truck warning as the compact secondary line even though the mission lacked parking. | Missing mission data retains priority. Busy-truck details/warnings and the server ON rejection remain. Providing valid parking makes the waiting message primary. Preparation neither creates ON nor becomes an outstanding physical transfer. |
| `OutstandingWorkTest::test_retained_same_tail_preparation_remains_reviewable_not_original` | Expected the retired `_outstanding_work.html` labels/selectors; directly allocated another work row with `new_work()`. | Canonical A→B→A acknowledgments retain three permanent visit identities with no fabricated physical facts. The current console displays PREPARING and an exact source-specific VISIT DATA link. The source data endpoint is authorized and readable; no physical-interruption action appears for preparation. A GET preserves the recorded values and timestamps. |
| `OutstandingWorkTest::test_genuine_earlier_tail_and_same_tail_physical_visits_keep_recovery` | Expected old ORIGINAL/PREVIOUS VISIT labels and manufactured OFF/new-visit state directly. | Uses canonical physical OFF and authorized Dispatcher `begin_physical_visit()`, then canonical tail acknowledgment. Current `data-console-visit` / `physical-<work ID>` controls expose the exact prior work, including END THIS VISIT EARLY with its immutable ID/sequence. Earlier visits retain their original tail, cycle, readings and milestones; they are not finalized or merged by display reads. |
| `CancelUnassignTest::test_dispatch_visual_contract_reserves_fob_slot_through_live_refresh` | Expected an obsolete blank spacer and exact `<small>` class layout. Current compact rows use fixed row geometry and optional measured FOB text. | A semantic HTML parser checks Inbound and Required cells before/after the existing live-panel GET, preserving the editable Inbound value. Measured FOB 30.0 appears only after entered readings, without becoming FOB READY or physical ON. Fixed row geometry, compact accents and reduced-motion behavior remain asserted. |
| `CancelUnassignTest::test_ready_assignment_alert_threshold_refresh` | Assumed scheduled arrival alone must prevent READY, conflicting with the shared canonical status contract. | Arrival is context, independent from data readiness and resource assignment. Missing required data and fully assigned resources still prevent READY. Configured 0/45-minute urgency changes the color, not READY itself. Settings persistence, revisions, live-panel threshold propagation, retired boolean compatibility and removal of the READY accent after assignment remain tested. |

All six retain their original IDs. No failing test was deleted, skipped,
permission-bypassed or converted into a no-assertion test. Fresh readings are
entered through canonical services; no source audit is fabricated to authorize
FOB. The old label-specific assertions are replaced with checks of current,
source-specific controls rather than adding an obsolete UI alongside the console.

The explicit no-truck FOB action still records the existing zero-transfer
projection. This is an authorized no-transfer completion, not a fabricated
physical fueling event or a consequence of Remaining inheritance alone.

## Validation

Existing `.venv` dependencies were reused. Synthetic SQLite and loopback-only
PostgreSQL fixtures were used, with the existing guard blocking external sockets.
No provider, email, Google Sheet or production R2 test action was performed.

| Run | Actual result |
| --- | --- |
| Six original IDs before corrections | 6 failed; 2,424 warnings; 7.27 s |
| Final six original IDs | 6 passed; 2,740 warnings; 7.76 s |
| R03B scoped suite plus lifecycle, submission, continuation and physical-evidence tests | 150 passed; 39 passing subtests; 60,261 warnings; 110.64 s |
| Isolated PostgreSQL | 35 passed; 11 passing subtests; 15,948 warnings; 113.04 s |
| Chromium/WebKit visit/handoff workflows | 2 test methods passed, each running both engines; 87.16 s |
| Chromium/WebKit two-account dirty-row/open-dialog handoff | 1 test method passed, running both engines; 44.42 s |
| Affected JavaScript lifecycle/completion/Fueler Data tests | 18 passed, zero failed |
| Compilation and whitespace checks | Changed Python modules compile; `git diff --check` passes |

The final six-case rerun and PostgreSQL run include the additional explicit
three-visit uniqueness assertions added after the 150-case run. Existing
R03B cancellation, source ownership, inventory discrepancy/rollback,
concurrent save/verification and exact-once delivery protections remain green.
The PostgreSQL wrappers assert the actual engine dialect so an inner fixture
cannot silently test SQLite instead. Its disposable server was stopped cleanly;
its original files were retained.

Warnings were not suppressed. The existing SQLAlchemy/deprecation warnings
remain visible in ignored logs. These tests establish the scoped contracts,
not a production capacity guarantee or a clean full-system suite.

## Full original R03 register reconciliation

The [updated machine-readable register](2026-11-02-r03c-backlog-register.json)
accounts for **every one of the 367 original failing IDs**, preserving each
original classification and diagnostic provenance. It adds current status,
current ID, fresh capped diagnostics, locations, confidence and investigation
priority. Original reports are unchanged.

* **48 original IDs now pass; 319 still reproducibly fail.**
* No previously reported R03/R08, R03A or R03B fix failed again.
* The single R03A rename is explicitly mapped to its verified replacement;
  the original ID is retained. No case was silently removed from the inventory.
* All 367 original IDs have an executed result; no missing IDs, skips or
  collection errors occurred.
* Pytest reported 608 failure events, 80 passing outcomes and 350 passing
  subtests in 247.25 seconds. Those are subtest/event counts, **not** 608 unique
  broken tests or 80 wholly passing original IDs. The per-ID totals above
  require every subtest of an ID to pass.
* The monitored process tree peaked at 896,589,824 bytes (855.1 MiB). The
  sequential inventory run had a 2 GiB test-tree limit, 4 GiB workstation
  headroom protection and a 20-minute deadline; none was reached.

This executed the original failing inventory, not the approximately 5,500+
application tests. No broad R03 green claim is made. The final extra preparation
assertions are additionally covered by the final six-case rerun; they do not
change a helper used by the other original IDs.

### Remaining risk and common-root-cause priorities

Priority identifies potential operational exposure and the next investigation,
not a confirmed production vulnerability inferred from a failing test count.
The register deliberately keeps unresolved cases unresolved.

1. **R03D recommended: NeoScorpion completion/correction and physical-start
   fixture contracts.** There are 21 failures whose first rejection is a
   verified canonical physical/handoff prerequisite. Eight are in
   `test_neoscorpion_dispatcher_complete.py`; the common fixture claims positive
   T/F without an assigned truck. Their later assertions about immutable
   events, inventory and correction ownership have not yet been reached.
   Rebuild those workflows with real ON/OFF/submission, then inspect the
   resulting assertions. Do not relax any guard. Additional SPEAR recorded
   release/location discrepancies (`Remote` versus recorded ramp) and current
   visit correction cases remain unresolved and should follow as independently
   bounded scopes, not be labeled cosmetic.
2. **NeoErmac mission identity and current-sort operational fixtures.** The
   remaining set includes 138 NeoErmac IDs, with separate empty-result,
   rejected-mutation and Master Schedule selection failures. Examples include
   `test_neoermac_pull_integrity`, linked-door pulls and tail-presence tests.
   Verify canonical Master Schedule flight/occurrence prerequisites before
   changing matching. A 400 response or an empty list alone does not prove a
   production integrity defect or an obsolete test. Avoid one large rewrite
   of all 138 cases.
3. **Google/NeoRain/MotherBrain authority and employee attendance/permission
   fixtures.** Examples include the HOT/ALP same-flight mission-creation
   expectation and management attendance actions rejected by the canonical
   authority service. Preserve R07/R08 precedence, occurrence identity,
   revocation and current-sort checks. Reproduce through actual authorized
   writers before changing policies or declaring these fixtures obsolete.
4. **SQL attribution before budget edits.** Sixteen old query-budget
   classifications remain; several NeoSektor/NeoStaffing cases differ by one
   SELECT. Determine whether that read is a required authority guard or a real
   redundant query. Do not simply increase budgets or remove validation.
5. **Presentation, static assets and CSP-era harnesses.** Source-string, layout,
   navigation and cache-version assertions remain, including the FROST asset
   identity string. Update only after verifying the deployed markup/asset
   contract, keeping nonce, cache invalidation, drafts and permission coverage.

No additional open application-integrity defect was confirmed by the six-case
investigation. The 319 other failures remain a material **verification gap**;
their existence neither proves every production workflow broken nor permits
final system sign-off. Historical R07/R08 and R03B defects remain documented as
previously corrected defects, not erased from the audit history.

## Changed files and production boundary

Only these seven files are included:

* `tests/test_neoscorpion_cancel_unassign.py`
* `tests/test_neoscorpion_fueling_status.py`
* `tests/test_neoscorpion_outstanding_work.py`
* `tests/test_neoscorpion_r03c_postgres.py`
* `docs/audits/2026-11-02-r03c-fueling-and-backlog.md`
* `docs/audits/2026-11-02-r03c-backlog-register.json`
* `docs/audits/2026-11-02-r03c-verification.json`

Deployment verification uses existing Render AutoDeploy and read-only checks.
No production fuel, tank, truck, employee, mission, permission, archive,
historical record or Google workbook test mutation is authorized or performed.
No hosting, worker, environment, database schema or scientific/model setting
changes are included. Unrelated files and ignored synthetic PostgreSQL files
remain intact.
