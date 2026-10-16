# SPEAR whole-sort v2

Rollout defaults to **SHADOW**. Current SPEAR remains the only visible source
until an authorized Settings editor explicitly selects ACTIVE. Activation is
rejected once current-sort assignments or TOP OFF operations exist. Automation,
Learning Capture and OBSERVE/APPLY remain independent and unchanged.

## Scheduling contract

The optimizer inserts reservations in lexicographic authoritative ETD order
(`eta_datetime_utc`, falling back to `planned_datetime_utc`), with mission ID as
a stable tie-breaker. Predicted delay never changes priority. Each earlier
mission's best feasible physical OFF time is protected before considering later
work. Eight alternative equally best calendar states retain different resource
pairings and immediate versus delayed TOP OFF sequences. Later missions can fill
gaps only if earlier travel, timing and truck reserve remain feasible. An earlier
unknown readiness/timing requirement prevents claiming a safe later schedule.

Truck and Fueler calendars are separate. Truck physical OFF and proven Fueler
work-end evidence are used independently; administrative COMPLETE is never a
release. Confirmed current resources and Dispatcher commitments remain fixed.
Current physical work, interrupted/unproven releases, holds, OOS, sump and
unverified inventory are protected. Trusted physical OFF predictions allow
conditional future schedules; expired or unavailable predictions do not slide
forward or release resources. Aircraft setup/pump/finishing settings and existing
qualified OBSERVE/APPLY calibration are reused once.

Truck demand/reserve/capacity is checked across the entire resulting calendar,
including later work inserted before an earlier reservation. DEFUEL requires
empty capacity. A TOP OFF reset forecasts the individual truck's full configured
capacity, but never writes gallons, inventory epochs, assignments or milestones.
Only actual RETURN changes verified availability. Conditional assignments have
no automatic action, appear as advisory NEXT, and cannot be executed through the
SPEAR action endpoint before verified release/RETURN. Ordinary manual assignment
controls and their canonical safeguards remain intact.

The bound is 80 jobs (including grounded pending uplifts), 8 beam states, 16,000
pair/sequence evaluations and 0.8 seconds. A budget/history/solver failure returns
to existing safe SPEAR without changing canonical operations. The tested 40-flight,
three-truck/three-Fueler scenario completes within the bound. This is a bounded
multi-mission search, not a guarantee of a mathematically exhaustive optimum.

## TOP OFF evidence

Manual roundtrip defaults to 40 positive minutes and includes the full farm trip
back to Remote. Only subsequent Remote-to-aircraft travel is added. Learned timing
defaults OFF. It uses nearest-rank p80 after 10 qualified exact SENT/RETURN pairs
across at least two sorts, preferring sufficient truck-specific evidence, otherwise
gateway evidence. The latest 1,000 linked pairs are bounded in one history query.
Pairs require matching operation/truck, human actors, verified RETURN gallons,
ordered timestamps and a 2–240-minute duration. Sample count, sorts, source and
confidence/fallback are shown in the existing SPEAR details.

New SENT events freeze their policy and original predicted return in nullable
`spear_roundtrip_policy` and `spear_predicted_return_at_utc` fields. Append-only
history protections remain unchanged. Later settings/history changes cannot hide
an overdue trip. Legacy SENT events without captured prediction provenance show
TIMING UNKNOWN and stay unavailable, rather than inventing their original ETA.
Forecast failure is isolated from canonical SENT. Dispatcher approval remains
required for every physical SENT, including with Automation enabled.

## Replanning and observations

Relevant mapped mission, arrival, tail/type, fuel, work/cycle, parking, pending
uplift, resource, settings/calibration and inventory inputs form the cache key.
This detects external mission updates independently of the fueling revision.
Operational revision changes use the existing live panel path. The existing
configured SPEAR reconciliation interval also reevaluates active overruns and
external updates. No new browser timer, request or polling cadence was added.
At most two fixed TOP OFF history queries occur on a cache miss; physical visit
evidence reuses the existing resource-timing query. Notes/checkbox UI state does
not independently trigger optimization. Existing dirty-input, scroll and DETAILS
preservation remains in the unchanged live JavaScript.

Pending uplifts retain their exact parent cycle/tail/version identity. Added
demand is grounded only in complete saved Actual tank totals and gateway density;
missing/finally unverified quantities and REVIEW remain explicitly uncertain.
Grounded children enter forecast calendars after the parent's projected Fueler
release, conditional on actual Dispatcher completion and authorized promotion.
They never become executable steps or physical inventory events. Promotion,
cancellation or edits naturally invalidate the input fingerprint.

`neoscorpion_spear_plan_snapshots` retains immutable `spear-plan/v2` JSON versions
and material change explanations. Background persistence is bounded to 32 queued
plans, with short database lock/statement timeouts; snapshot failure cannot block
an operational action. Plan evidence is best effort during database/process
failure. Original Dispatcher decision evidence remains separately signed and
durably retried through Phase 1's `spear-decision/v1` outbox/Vault contract. The
actual chosen engine version and immutable SENT prediction are included.

Production is intentionally left in SHADOW. ACTIVE is available after authorized
review, before operational work begins. Browser validation uses isolated fixtures;
production health/deploy verification does not claim an authenticated live-data
browser session. Historical missing release/prediction/measurement evidence is
never reconstructed.

## Focused validation and changed files

Validation covers 25 pure scheduling/policy cases, 7 mapped integration cases,
9 PostgreSQL replanning/bootstrap/SENT cases and 4 existing PostgreSQL submission /
exactly-once inventory cases. Phase 1's 17 decision-contract checks also pass with
the additive fleet prediction evidence. Chromium desktop and WebKit mobile cover
actual assignment saves, immutable original recommendations, optional notes,
16/five visible columns, missing-data/missing-ETD rows, live panel replacement
without reload, Fueler and Settings rendering, and no document overflow. Five
existing targeted JS scroll/sorting checks pass; application compilation and
`git diff --check` pass.

Legacy nightly-asset/roll-down suites have two assertion failures and seven old
completion-fixture errors reproduced unchanged against verified Phase 1 main.
They expect obsolete starting-inventory behavior or omit required Fueler
submission. The focused PostgreSQL submission/inventory and concurrent SENT /
RETURN checks pass. These legacy failures are not represented as a green suite.

Phase 2 changes:
- Models: `neoscorpion_fuel.py`, `neoscorpion_top_off.py`, new
  `neoscorpion_spear_plan.py`, model registration.
- Services: new `neoscorpion_spear_optimizer.py` and
  `neoscorpion_spear_replanning.py`; adapters/settings/evidence in
  `neoscorpion.py`, `neoscorpion_spear.py`, `neoscorpion_spear_decisions.py`,
  `neoscorpion_assets.py`, `neoscorpion_resource_timing.py`.
- Additive bootstrap contracts: `schema_sync.py`, `neoscorpion_schema.py`,
  `neoscorpion_spear_schema.py`.
- Dispatch route approval guard and existing pending-context attachment.
- Templates: `_fuel_dispatch_panel.html`, `_pending_uplift.html`,
  `spear_settings.html`.
- Four focused test modules and this document. No NeoRain code or JS polling
  changes. No feature branch or manual Render deploy.
