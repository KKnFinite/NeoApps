# NeoScorpion bandwidth and SPEAR resource truth

2026-11-04. Starting/fetched main: `ffe42989d6adce6130aaa32096a03a99c596f9d9`.
Scope: focused performance and recommendation corrections, not the full-system audit.

## Confirmed causes and changes

1. Dispatch's configured SPEAR recalculation fetched the complete operational panel even without an operational revision. It now requests `spear_only=1` on the **same configured cadence**. Shared, server-rendered fragments update fleet status, individual mission explanations/badges and truck recommendations. Unchanged fragments are omitted using bounded fingerprints. Signed actor-bound learning references and plan tokens remain fresh; server action validation and existing automation behavior remain authoritative.
2. A clean operational change downloaded both critical and full copies of the panel. It now requests one full panel. Dirty inputs, an OPEN Fueler Data dialog or a personal save use the existing independent critical path. Typing during transit is rechecked before replacement and falls back to critical reconciliation. No POST is retried.
3. Fueler, Fueling Board and Truck Driver use a sort-wide revision, so an unrelated change can download identical markup. After fresh authorization, scope resolution and rendering, a private content digest omits unchanged HTML. No cross-user HTML cache or shared operational payload was introduced. Fueler mutation fingerprints invalidate its digest; deferred Driver drafts cannot adopt an unapplied panel digest.
4. Hanzo previously reloaded the entire document on a revision. Its unchanged read-only panel now uses the same private digest protocol; changed panels replace only that panel and preserve scroll. A changed sort retains the full-page fallback. Initial rendering is unchanged.

SPEAR-only responses omit performance-report and driver-management assembly. Operational full responses, permission checks and revision-first polling remain intact. Existing session/gateway/sort-scoped revision coordination, hidden-tab pausing, configured intervals and Retry-After handling are retained. No new timer, service, schema, cache pool or background worker was added.

## Measurements

Synthetic SQLite workload: 25 current-sort aircraft, one canonically prepared and submitted visit, 24 additional planned assignments. Same fixture and user roles before/after; no providers. Bytes below are actual uncompressed HTTP bodies, not estimated wire compression. Serial after timings are medians of seven requests; pre-change timings used three. All measured GETs made **zero INSERT/UPDATE/DELETE statements**.

| Request/workload | Before | After | SELECTs before → after | Local median ms before → after |
|---|---:|---:|---:|---:|
| Timed SPEAR refresh, without learning references | 537,671 B full panel | 25,057 B first fragments; 2,875 B unchanged | 85 → 65 | 49.72 → 31.29 |
| Same current workload, signed learning references enabled | 571,976 B traditional full response | 41,407 B fragments; 19,219 B unchanged | 90 → 70 | 51.08 → 34.91 |
| Clean operational change | 2 panel GETs / 1,059,363 B | 1 panel GET / 539,276 B | 156 → 85 | Separate request costs; no production latency claim |
| Unchanged Fueler panel after revision change | 490,556 B | 175 B | 45 → 45 | 25.61 → 24.77 |
| Unchanged Board panel after revision change | 90,546 B | 151 B | 44 → 44 | 21.20 → 17.70 |
| Unchanged Driver panel after revision change | 2,190 B | 151 B | 50 → 50 | 19.42 → 16.68 |
| Hanzo full document versus changed panel, same current workload | 151,738 B document | 126,203 B panel; 151 B unchanged | 40 → 38 | 18.45 → 15.60 |

The signed-reference row compares the traditional full and fragment responses on the same final implementation; it is not a reconstructed pre-release historical response. The Driver sample had no claimed truck and is not representative of a busy Driver's payload. Conditional responses still perform fresh SQL/rendering; their primary saving is bandwidth, not database work. A real full Dispatch response is slightly larger due to fragment markers, not smaller.

With signed learning references enabled, a first timed SPEAR update is **92.8% smaller** than its full response; an unchanged one is **96.6% smaller**. Request frequency is unchanged. The clean operational path removes one duplicate GET (about 49% of those two responses' bytes in this fixture).

Chromium measured 65.39 ms from requesting a timed refresh to completion, and 70.17 ms from requesting reconciliation to visible Dispatcher COMPLETE. These are local request-to-display measurements, excluding the configured poll interval. An unchanged poll downloaded zero panels. The browser proved that the operational row node survives SPEAR refresh, a focused Tail Notes draft survives critical completion updates, and Hanzo preserves the document.

Render's 2026-11-03 UTC-day service metrics showed an hourly bandwidth bucket of 845.88 provider `mb` at 07:00 UTC. Exact Dispatch-path metrics and historical request-log queries returned no rows, so the reported 70-response/40.7 MB sample could not be independently reproduced through this connector. Service-wide 429 metrics must not be attributed entirely to NeoScorpion. No production bandwidth, CU-hour, sustained memory or accuracy improvement is claimed from these isolated measurements.

## SPEAR quality corrections

All four regression scenarios were run against the unchanged planner first and failed for the asserted resource/inventory/explanation reasons; they pass after the narrow corrections:

| Scenario | Before | Corrected result |
|---|---|---|
| Canonical active A → B Fueler handoff | Both A and B reserved by the same visit | Current audited owner B reserved; immutable physical-ON actor A remains unchanged |
| Raw 2,000 gallons but canonical available balance 1,000; next demand 800; reserve 500 | Invalid advisory next job offered | No next job; reserve would be breached |
| Canonical balance 1,700 already includes current reservation; next demand 1,100 | Current demand subtracted again; valid next job hidden | Valid advisory next job, conditional on real resource release |
| Truck at Charlie, Fueler at Alpha, aircraft at Charlie | WHY incorrectly reported Fueler travel as zero | Correct independent two-minute Fueler travel; actual scheduling math unchanged |

Current and retained physical resource projections, plus the existing whole-sort optimizer calendar, now use the existing audited handoff owner. No visit, physical actor, timestamp, event, fuel quantity, inventory deduction or PROF participation is rewritten. Unknown legacy evidence remains conservative. The next-job forecast uses the same canonical inventory reservations as the executable planner. It remains advisory and cannot automatically release an occupied resource. WHY now displays independent Fueler location/source/confidence as well as Truck provenance.

Existing configured priority order, urgency rules, travel rates, pump rates, reserve, staging, confidence exclusions and learning eligibility were preserved. The production settings read during investigation were recommendations ON, automation OFF, whole-sort SHADOW, calibration OBSERVE and recalculation every two minutes; none was changed. No new scheduling policy, model training or automatic operational action was activated.

Read-only historical evaluation found 666 completed visits, 664 with positive transfer and ordered ON/OFF, and 28 with newer participation metadata. Of those 28, 12 had a usable latest forecast recorded before physical OFF; their existing median absolute LIKELY/OFF error was 2.0647 minutes. Sixteen correctly lacked a usable prediction. This limited coverage does not justify retraining, treating uncertain older records as verified, or claiming a historical accuracy gain from the controlled fixes. Historical records and forecasts were untouched.

## Focused verification

- **185 unique Python cases passed**: the 184-case focused planning, fragment, handoff, timeline, submission, live-authority and Driver selection, plus the added signed-reference case. The final changed planner/render/browser selection was rerun (95 passed, including one browser case). Passing subtests are separate from these case counts.
- **20 PostgreSQL cases passed**: 19 existing handoff/submission/replanning cases and one new two-reader committed-handoff projection case. They cover independent transactions, stale autosave/submission, exactly-once inventory, rollback and unchanged immutable evidence. Only newly allocated disposable local schemas were used.
- **49 JavaScript tests passed**, including dirty/stale SPEAR responses, clean-versus-critical request selection, shared revision coordination, cooldowns, card reconciliation and completion scroll.
- **Two essential Chromium workflows passed**: new desktop/mobile bandwidth/draft/COMPLETE/Hanzo proof and existing active Fueler handoff-through-Dispatcher verification. No browser JavaScript errors in the new proof. Evidence: ignored `instance/scorpion-performance-20261104/browser/` and existing handoff browser evidence.
- Changed Python compilation, four changed JavaScript syntax checks and `git diff --check` passed. Existing `utcnow()` deprecation warnings remain visible. The full-system suite was intentionally not run.

The original splash test's source assertion was updated to inspect the extracted permission-gated shared indicator partial; no security assertion was removed.

## Remaining concerns and release boundaries

- Genuine full Dispatch updates remain approximately 539 KB (572 KB with signed references) in this synthetic workload. A first dirty critical response remains approximately 523 KB; subsequent unchanged critical fragments already omit rows. Further per-mission operational deltas would require additional validation of all embedded controls, not removal of evidence or drafts.
- The separate vendor screen still has its existing visible-only, revision-first ten-second refresh path. It is not the demonstrated large Dispatch response; no speculative vendor permission/transport changes were made. Fuel-status clocks and quick-fuel input debounce do not create recurrent panel downloads.
- Existing bounded current-sort reconciliation, finalized-outcome exclusion, deadline-aware archive scheduling (600-second safety, hourly FROST), crash leases and bounded whole-sort computation caches were inspected and retained. No background activity was added to replace reduced browser traffic.
- The local measurements do not establish sustained production savings or broad forecast accuracy. Compare equivalent busy production windows after rollout before drawing those conclusions.

Release uses an ordinary main push and existing Render AutoDeploy. Exact deployed commit, deployment ID, startup/health, asset-byte checks and read-only authorized page results are reported with the release response. No production fueling, inventory, employee, Google, archive or configuration test mutation is authorized or performed. Existing jobs, synthetic database directories, artwork and unrelated untracked work are preserved.
