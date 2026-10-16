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
