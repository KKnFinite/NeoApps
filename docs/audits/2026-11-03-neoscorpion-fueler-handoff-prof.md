# NeoScorpion: Fueler handoff and per-cycle PROF

Date: 2026-11-03. Starting main/origin/main: `099d96818f7a7df2b44e5ad36e10b93fe93c70f2`.

Scope: repair the existing Dispatcher FUELER SWAP and derive PROF eligibility from actual participation in each fueling cycle. No new operational action, physical visit, database schema, background service, dependency, hosting setting or inventory rule is introduced.

## Confirmed causes and reproduction

The starting release did **not** delete tank rows during an ordinary active-visit swap. Canonical reproduction retained the WorkState and readings. The reproducible ownership defect was in retained physical visits: `fueler_context()` and `_locked_source_segment()` used the immutable original physical Fueler as the current editing owner. After a handoff followed by a tail/visit transition, the replacement lost the source card while the original operator could retain source access. Source-card reproduction against the starting implementations failed with `StopIteration` for the replacement's missing card.

A separate starting defect prohibited swapping after physical OFF with `REOPEN OFF before swapping the assigned fueler.` The requested OFF-before-submission takeover therefore could not occur without changing the physical clock. Both starting implementations were exercised in an isolated process using functions extracted from the exact starting commit; the working tree and production records were not reverted or modified.

The existing PROF calculation assessed individual event/resource snapshots, without consistently assessing every actual resource in the entire cycle. A broad historical swap exclusion also did not distinguish planning from actual contribution.

## Handoff correction

- Reuse the existing SWAP RESOURCES / SWAP FUELER controls, endpoint and `swap_assignment_fueler()`. The same control is available before ON and after OFF. Truck Swap keeps its previous visibility and lifecycle rules.
- Existing operation, assignment and WorkState locks serialize handoff, autosave and submission. Submitted form identity is checked against work, assigned Fueler, truck, cycle and tail. A stale Dispatcher baseline is rejected through the existing route failure handling.
- Swap changes the assigned editing owner and records the existing linked `swap_fueler` audit. It retains the same WorkState ID, visit, sequence, cycle, truck, tanks, APU settings/allowances, T/F, notes, original ON/OFF times and original physical actor. It creates no fueling event or inventory transaction.
- Current access and immutable physical attribution are separate. Current and retained-source views authorize the replacement; the original physical Fueler remains recorded in historical evidence. A bounded, read-only lookup of exact assignment/WorkState/sort-linked legacy handoff audits recovers previously audited unfinished source handoffs without a data backfill. Unlinked or ambiguous legacy evidence grants no inferred replacement access.
- The existing assignment-update acknowledgment announces the handoff without replacing saved data. Former-user autosaves fail safely and retain their local unsaved draft; only persisted values are visible to the replacement. No mutation replay or cross-user draft transfer was added.
- The original ON remains sufficient after active takeover. OFF takeover preserves OFF and permits final readings and Fueler submission without another ON/OFF. The existing explicit accidental-OFF resume action remains available to the current authorized operator and preserves original physical attribution.
- A non-null successful Fueler submission timestamp locks Fueler Swap even while Dispatcher verification is pending. A finalized visit is also locked. The UI explains the submission lock; server enforcement is authoritative.

## Participation and PROF

`neoscorpion_participation.py` maintains small versioned participation facts in existing WorkState `performance_context` JSON. Existing append-only audits record actor, prior/new assignee, visit and time. No migration or mass permission/history update is needed.

Actual Fueler saves record the contributor. Confirmed physical ON records the actual truck and Fueler. A Dispatcher handoff after ON/OFF records both personnel as participants; a replacement's editing ownership is stored separately from the participant list. Before work, transient A → B → C assignment changes do not manufacture contributors. Persisted Fueler preparation is work; inherited Remaining alone is not a new Fueler's action. Planned trucks without actual physical use are not contributors.

Eligibility is derived by `(assignment_id, cycle_number)` from all relevant WorkStates and physical events, including peer visits outside a report's event page/time window. Exactly one verified actual Fueler and one truck are necessary. Reasons are `MULTIPLE FUELERS`, `MULTIPLE TRUCKS`, and `PARTICIPATION UNVERIFIED`. Existing timing, pump-rate, hold, source and resource-availability exclusions still apply. DEFUEL uses the same single-resource gate as FUEL/UPLIFT.

The gate is shared by PROF reports/totals, observations, scorecards, calibration observations, decision-outcome evidence and newly captured learning timelines. Excluded samples retain their normal operational/event/archive records, with null PROF measurements and explicit reasons. No model is trained or approved by this release.

Original FUEL and later UPLIFT are assessed independently. Tests prove both eligible; eligible original/excluded UPLIFT; and excluded original/eligible UPLIFT, including archive-backed scorecards. New immutable learning captures carry optional per-cycle proof in timeline facts. Assignment-level training eligibility remains conservative when any contained outcome is invalid; per-cycle scorecard evidence does not discard a valid original merely because another cycle is excluded.

Historical WorkStates/events/receipts are not rewritten. Existing trustworthy matching ON/resource/visit snapshots can support read-only eligibility; ambiguous legacy personnel changes and archive records lacking whole-cycle proof are excluded/unverifiable. No historical re-export or backfill is triggered. This can conservatively reduce derived historical PROF coverage; it does not alter stored physical performance snapshots.

## Database and resource bounds

The cycle assessment uses two bounded batch SELECTs (WorkState scalar/JSON projections and peer events), rather than per-event queries or full forecast hydration. Each is capped at 20,001 rows to detect overflow; overflow fails closed as unverified. Historical handoff-audit lookup is similarly bounded. Report loaders perform three SELECTs total (one existing event query plus two evidence queries), with zero writes; tested for one and multiple visits. The prior single-query observation assertion was updated to assert this constant three-query contract. No production CU, latency or memory savings are claimed.

## Validation

All mutations used synthetic SQLite/disposable loopback PostgreSQL data, mocked providers and a test network guard. No production fueling, Google or archive test writes occurred.

| Run | Result |
| --- | --- |
| Focused handoff, cycle eligibility, resource PROF/integration and learning capture | **61 passed**, 13 passing subtests |
| Focused PostgreSQL handoff/resource tests | **9 passed** |
| Curated NeoScorpion core runner | **299 passed / 1 existing failure** (300 executed) |
| Curated PostgreSQL runner | **55 passed / 1 existing failure** (56 executed) |
| NeoScorpion JavaScript | **95 passed**, no failures/skips |
| Chromium/WebKit handoff, existing Fueler lifecycle and shared header workflows | **5 passed** |
| Existing shared Fueler screen workflows in both engines | **2 passed** |
| Final handoff-only rerun after legacy ownership refinement | **2 passed** (included in browser coverage above; not additional unique tests) |
| Python compilation and `git diff --check` | Passed |

Counts overlap across focused/curated runs and are not a sum of unique tests. Existing widespread `datetime.utcnow()` deprecation warnings remain visible; no warning suppression was introduced.

The unchanged curated failure is `OperationalTimelineTest.test_same_tail_pending_actuals_survive_a_new_visit_and_stale_form`: it expects HTTP 400 where the established stale-identity response is HTTP 409. PostgreSQL's `PhysicalSegmentPostgresTest.test_pending_same_tail_visit_preserves_old_actuals` executes the same assertion. It was also present in the preceding header release's before/after artifacts. These assertions were neither removed nor weakened. The unrelated full original R03 inventory was not rerun in this package.

Two old resource-PROF PostgreSQL fixtures omitted Fueler COMPLETE ASSIGNMENT before Dispatcher COMPLETE; they now perform the canonical handoff. Pure arithmetic fixtures explicitly supply single-resource proof; serialization fixtures supply matching physical identity. A negative test proves a standalone event claim cannot establish missing participation. No production eligibility guard was relaxed to pass a fixture.

Covered contracts include partial Remaining/Actual/APU/manual allowance/T/F/notes; pre-ON, active and OFF takeovers; successive A → B → C handoffs; old-owner denial and stale autosaves; submitted/finalized locking; same-visit resume; legacy source-owner recovery and foreign-sort rejection; actual multi-truck A → B → A visits; unused planned trucks; independent FUEL/UPLIFT; DEFUEL; immutable snapshots and exactly-once inventory. PostgreSQL independent-transaction races cover autosave versus swap, two swaps sharing a stale baseline, submission versus swap, and former/replacement source edits.

Browser reproduction uses three authenticated synthetic sessions: Fueler A saves → Dispatcher invokes existing SWAP FUELER → A's subsequent autosave is denied without losing its local draft → B sees the persisted values → B submits the same visit → Dispatcher verifies → PROF displays MULTIPLE FUELERS. Chromium covers active ON takeover; WebKit covers takeover after OFF. Fueler view is 390px and Dispatch is 1440px. Existing shared-header checks cover 320, 375, 390, 430, 768, 1024 and 1440px in both engines; existing shared Fueler/Dispatcher modes and single-resource lifecycle also pass. Browser runs report no unhandled page JavaScript errors.

Synthetic browser evidence:

- [Chromium: A's saved data](assets/fueler-handoff/chromium-a-saved.png)
- [Chromium: B inherits the active visit](assets/fueler-handoff/chromium-b-inherited.png)
- [WebKit: B inherits the OFF visit](assets/fueler-handoff/webkit-b-inherited.png)
- [Dispatcher submission lock](assets/fueler-handoff/chromium-submission-locked.png)
- [PROF exclusion](assets/fueler-handoff/chromium-prof-exclusion.png)
- [Chromium verification receipt](assets/fueler-handoff/chromium-receipt.json), [WebKit verification receipt](assets/fueler-handoff/webkit-receipt.json)

The disposable PostgreSQL server was stopped after testing; its synthetic files were preserved. No temporary-directory deletion was attempted.

## Changed source/test files

Application: `app/services/neoscorpion.py`, `neoscorpion_participation.py`, `neoscorpion_segments.py`, `neoscorpion_resume.py`, `neoscorpion_prof.py`, `neoscorpion_spear_calibration.py`, `neoscorpion_spear_decisions.py`, `neoscorpion_spear_learning_capture.py`, `neoscorpion_spear_scorecard.py`, `neoscorpion_learning_vault.py`; `app/neonodes/neoscorpion/routes.py`; shared `_dispatch_secondary.html` and `_fueler_card.html` templates.

Tests: new `tests/test_neoscorpion_fueler_handoff.py`, `test_neoscorpion_cycle_participation.py`, `test_neoscorpion_fueler_handoff_postgres.py`, `tests/browser/test_neoscorpion_fueler_handoff.py`; affected existing `test_neoscorpion_dispatch_details.py`, `test_neoscorpion_operational_timeline.py`, `test_neoscorpion_resource_prof.py`, `test_neoscorpion_resource_prof_postgres.py`, `test_neoscorpion_spear_learning_capture.py`. This report and its synthetic evidence accompany the release.

## Release and limitations

Release procedure: commit only these files, push main, allow existing Render AutoDeploy, then confirm the exact commit/deployment status, bootstrap/worker logs, bounded health/login and static-content checks, and read-only authorized Fueler/Dispatch/PROF views. Exact final SHA, Render deployment ID and post-release observations are reported in the completion response; this pre-commit report does not claim a deployment already happened.

No production data repair, archive rewrite, permission change or operational test submission is authorized or performed by this package. Unrelated untracked artwork/prototype/audit files are excluded. CSS/JavaScript asset contents, CSP/CSRF infrastructure, Google authority, FROST, staffing recovery and truck inventory rules are unchanged. Read-only post-release checks cannot demonstrate a real live-aircraft handoff; that complete mutation workflow is demonstrated only with isolated synthetic browser data.
