# SPEAR decision observations (phase 1)

Dispatch uses `build_spear_plan()` exclusively. Legacy resource-calendar utilities
remain available to their independent callers; Dispatch does not call their
recommendation engine. Dropdowns, mission explanations and truck cards share the
same plan step. A resource-release-dependent future step is not labeled as an
immediately available dropdown recommendation. Invalid partial assignments are
reported rather than silently replacing the selected resource.

When Learning Capture is enabled and the durable Vault is configured, the rendered
page carries an authenticated, actor/gateway/mission-bound reference. It includes
the actual original plan token, algorithm version, UTC projection, evaluated
alternatives (at most three, excluding the selected pair), operational conditions,
cycle/visit identity and operational fingerprint. No recommendation is reconstructed
after a decision. A stale, expired, invalid or missing reference is identified;
partial selections and missing recommendations are never classified as rejection.

`neoscorpion_spear_observations` is an additive outbox. Its evidence is immutable;
delivery/reconciliation metadata can change. Resource mutation services enqueue
after a successful canonical flush, inside an isolated savepoint. They perform no
Vault I/O. A background worker is triggered by existing authenticated Scorpion
traffic, at most once per gateway per 30 seconds and one worker per process. It
claims at most three exports, persists a retry lease before provider I/O, and uses
bounded exponential backoff. OFF disables new captures and delivery. Restarts
retain pending evidence; retries resume with subsequent traffic. There is no new
browser polling or operational workflow dependency.

The separate validated `spear-decision/v1` envelope has record types `decision`,
`outcome` and `reason`; a UUID, gateway/operation/mission/assignment/cycle identity;
UTC capture time; and a bounded, type-specific payload (64 KB maximum). R2 keys are
`decision-observation/v1/<gateway>/<operation>/<uuid>/<sha256>.json.gz`. Content
hashes make exports idempotent. Existing `spear-learning/v1`, `/v2`, `/v3` completed
outcomes and their Vault readers are unchanged. Decision objects are a separate
prefix, not displayed by the older completed-outcome Vault browser.

Reconciliation considers only captured decisions, matching actual events by
assignment, cycle, tail, physical work where known, resources, and the decision's
time interval before a subsequent decision. It preserves unqualified outcomes and
exclusion reasons. A qualifying observation is still `observed_only`; this release
does not label a Dispatcher as better/worse or enable ranking/training. All decision
exports have `training_eligible=false`. Optional reasons are separate supplementary
records referring to an exact decision, never ground truth or mandatory input.

TOP OFF is never an automatically eligible SPEAR step. Executing it requires a
current, signed, actor/operation/plan/truck-bound rendered Dispatcher approval as
well as normal route authorization and CSRF. Manual authorized TOP OFF remains
available through existing controls. A client automatic-mode flag cannot approve it.

Limits: provider delivery is eventually consistent and traffic-driven; permanent
Vault/DB outages can leave evidence queued or (if the local capture savepoint itself
fails) unavailable, while canonical Dispatch proceeds. No historical recommendations
are fabricated. Uncertain visits, interruptions or timing remain excluded from
qualified comparisons. Phase 1 does not change the fleet planning algorithm.


Approved TOP OFF decisions use `neoscorpion_spear_fleet_observations`, never a fabricated
FuelAssignment. The same bounded worker exports `fleet` and `fleet_outcome` variants
of the decision envelope (assignment/cycle are null when inapplicable). The original
rendered reference and canonical SPEAR audit are retained, along with the exact
append-only TOP OFF SENT event ID. Reconciliation joins RETURN by that SENT identity,
not truck/time guesses, and preserves the original prediction and measured outcome.

## Final physical-outcome reconciliation (audit N10)

`reconciled_at` is nullable local queue metadata, separate from `delivered_at`,
`verified_at`, the immutable JSON envelope, and R2 retry state. The canonical
bootstrap adds only that column and `ix_spear_decision_unresolved`, a partial
index on `(gateway_id, reconcile_after, id)` for unresolved decisions. It does
not rewrite old evidence or mark old archive receipts verified.

The worker selects at most 40 due, unresolved decisions with PostgreSQL
`FOR UPDATE SKIP LOCKED`. It also acquires nonblocking assignment locks before
reading decision boundaries, physical visits, and transfer events. A busy live
mutation is skipped without rescheduling writes. This prevents observing an
event before canonical completion and its physical seal after completion as
one inconsistent snapshot. Locks cover database work only; no provider I/O or
archive delivery occurs inside this transaction.

An outcome becomes final only for a sealed exact physical visit, or a completed/
rolled-over cycle with matching events and all known original-tail visits sealed.
An unsealed visit, missing physical evidence, or another open visit remains
eligible for late evidence. The existing five-minute unresolved retry and all
tail/resource/time-boundary matching and exclusion rules remain in place. A new
cycle is independently captured, not attached to the old physical outcome.

Outcome insertion/reuse and the parent's finalization metadata commit together.
There is no independent savepoint commit. A failed/disconnected transaction rolls
back both; another worker can retry. Parent row locks and the existing unique
event key prevent competing workers from creating duplicate outcomes. Historical
valid outcomes are reused without rewriting their content, even if unordered
event/exclusion sets were serialized in another order. Their parents acquire the
new marker once through ordinary bounded reconciliation. Provisional historical
outcomes do not prevent capturing later final evidence.

Once final, a decision is absent from the indexed reconciliation queue. Its
`reconcile_after` stays unchanged. R2 outages still update necessary delivery retry
metadata, but cannot reopen or reschedule reconciliation. Completed-fueling
capture, archive formats, content verification and five-minute delivery crash
leases are unchanged.

### Reproducible SQL measurement

One synthetic sealed decision was finalized, then reconciled ten more times with
a deterministic clock advancing six minutes between calls. Counts include all
SELECT/UPDATE statements inside those calls, with provider I/O absent. The
original implementation was loaded from commit
`ad48820810f93f22d4f2d625bce9c75ee2f82fb1` for the comparison.

| Isolated backend | Original SELECTs | New SELECTs | Original observation UPDATEs | New observation UPDATEs |
| --- | ---: | ---: | ---: | ---: |
| SQLite | 60 | 10 | 10 | 0 |
| PostgreSQL | 60 | 10 | 10 | 0 |

No INSERTs occurred in either repeated-pass workload. Each new pass performs one
indexed due-queue lookup; it does not hydrate assignment history. For unresolved
work, indexed next-decision boundary lookups replace whole-cohort hydration and
event retrieval is restricted to selected assignment/cycle identities. This is
an isolated SQL comparison, not a measured Neon CU-hour saving or production load
test. PostgreSQL tests also cover concurrent workers, a disconnected claim,
operator-held assignment locks, partial-index selection, and additive bootstrap.

Validation: 54 focused SQLite/Python tests, 18 reconciliation PostgreSQL tests,
12 existing archive PostgreSQL tests, and 22 affected JavaScript tests pass.
Fueler-to-Dispatcher completion passes in Chromium and WebKit. The separate
existing SPEAR browser test passes its desktop assignment/reason workflow, but
its mobile `button:visible` locator times out; this also reproduces with the
starting release loaded in memory. No production UI, fueling or archive test
writes are used to work around that pre-existing browser-test limitation.
