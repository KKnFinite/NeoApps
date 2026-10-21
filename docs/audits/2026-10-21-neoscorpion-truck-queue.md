# NeoScorpion continuation queue and independent completion

Starting main: `e3bee4bb9e317fc1794cbcec129f3bda0cbf8af5`.

## Confirmed causes

`NeoScorpionPendingUplift` had a partial unique index on mission/cycle for all
outstanding plans. `plan_continuation()` selected one outstanding record, and
the Dispatch presentation disabled the planner whenever that record existed.
Plans appeared only inside DETAILS rather than as independent visible lines.

There is now an ordered, operation-locked continuation queue with no fixed
truck-count limit. An expected queue-tail token rejects stale/concurrent create
requests. Exact plan ID/version protects edits and cancellation. Legacy pending
post-completion uplifts retain their original single-plan uniqueness and timing.

Successful Fueler submission activates only the head plan. Later plans are
audited/rebound to that new visit and wait for its own submission. The exact
verified source Actuals become its Remaining; Actual/APU/T/F/ON/OFF remain fresh.
No transfer or physical ON is posted by planning/activation. Unnecessary plans
remain explicit Dispatcher review/cancellation, never automatic cancellation.

SPEAR includes every queue identity in its revision fingerprint and future-work
evidence. Only the head has grounded demand; subsequent demand/timing remains
explicitly unknown until preceding measurements exist, rather than repeating
the same fuel demand across every future truck.

## Read-only production incident evidence

Operation 76, sort 2026-10-20; rechecked 2026-10-21 around 07:21 UTC.

| Flight | Assignment | Earlier visit | Latest visit | Evidence |
|---|---:|---:|---:|---|
| UPS0988 / N472UP | 534 | 491, truck 6 | 495, truck 6 | 491 submitted 06:31:36 UTC, not verified, no event. 495 verified 06:46:50 UTC; event 457, T/F 949. |
| UPS0910 / N379UP | 533 | 490, truck 1 | 496, truck 4 | 490 submitted 06:36:31 UTC, not verified, no event. 496 verified 06:48:59 UTC; event 458, T/F 573. |

The latest COMPLETE actions succeeded. `reconcile_completion()` correctly kept
the overall mission unfinished because the earlier visits remained unverified.
Current-visit presentation inherited that overall state and still showed OFF;
previous visits lacked always-visible independent status lines. The existing
earlier-visit verification controls could consequently be missed.

The release exposes each visit's own COMPLETE timestamp, submission, physical
milestones, tank readings and transfer, and points to its exact Action Center
verification. The mission summary explicitly identifies earlier verification
still required. Repeated verification of the exact finalized current visit is
idempotent even while the parent mission is unfinished; no repeated posting or
timestamp change. Overall completion still requires all mandatory work/reviews.

`_attempt_promotion()` is the legacy post-completion uplift path; these two plans
were `increase` and `continuation`, already promoted to their respective latest
visits. No evidence of legacy uplift promotion creating another cycle was found.

No production operational record was changed. No historical repair is indicated
by this evidence: a Dispatcher must verify the genuine earlier submissions
through the existing independently addressed action, subject to its safety
validation. Do not mark those visits complete automatically or manufacture
their missing verification timestamps.

## Schema and compatibility

Only the legacy single-slot index is replaced. Existing plan/work/event data is
not rewritten. A partial unique index still protects legacy `uplift` plans.
Migration is idempotent and runs through existing bounded bootstrap/schema-sync
paths. Existing operation locks serialize queue and physical mutations.

No changes to fuel calculations, inventory posting mathematics, ON/OFF actions,
Fueler submission validation, authorization, or START UPLIFT semantics.
Existing resume, tail-swap, source-correction and defuel protections remain.

## Validation

- 109 focused Python checks: queue, existing continuation and pending uplift,
  DETAILS control inventory, resumed visits and SPEAR compatibility.
- 8 disposable PostgreSQL checks: five visits with reverse verification,
  last-verified-first incident regression, cancellation, concurrent create/edit,
  duplicate source submission/verification, idempotent index migration.
- 21 JavaScript checks: critical receipts while drafts remain protected,
  completion scroll, grouping/secondary sort, assignment receipts and Fueler data.
- Chromium and WebKit, separate authenticated desktop Dispatcher/mobile Fueler:
  four future trucks planned before ON, five independent visits, Actual→Remaining
  at every handoff, A→B→A reuse, earlier verification while next truck is ON,
  final completion, Board View, read-only mobile, widths 1920/1440/390/375.
- Python compilation, JavaScript syntax and `git diff --check`.

Evidence is local under `instance/browser-evidence/truck-queue/` and
`instance/truck-queue-*-final.log`. Synthetic fixtures only; production checks
were read-only. Deliberately injected SPEAR fallback warnings are expected tests.

Release SHA and exact LIVE deployment must be checked after the push; this audit
does not itself assert production deployment or verification of the two records.
