# SPEAR rolling 30-calendar-day learning

Starting main: `26aa6b949f19614764e56ae29dc9858ff4f9e005`. Scope: existing SPEAR calibration, physical-OFF forecasts and TOP OFF policy. No schema migration, historical cleanup, new worker, polling change or automatic assignment action.

## Window and evidence

- The window contains today and the preceding 29 calendar dates in the existing configured gateway timezone. It begins at local midnight converted to UTC and ends at the current UTC instant. The DST test proves the correct Chicago cutoff. Future observations/completions are excluded.
- The compact history query is gateway-isolated, newest-first and capped at 2,000 events. It requires matching permanent Work/assignment/cycle/sequence, a finalized visit with an actor, and completed current-cycle authority or a previously sealed completed-cycle snapshot. A later UPLIFT does not erase a prior eligible cycle. Existing event/whole-cycle participation verification still excludes multiple/unverifiable contributors and invalid physical evidence.
- Test/simulation flags, explicitly ineligible data, invalid Actual/completion chronology, unsupported timing, incomplete cycles and unreliable measurements are excluded. New canonical test-mode policy snapshots carry `learning_origin=test`; production snapshots carry `production`. Existing history is not relabeled or rewritten. Unmarked historical records still require canonical completion and participation proof; employee names/roles are not guessed as test markers.
- Physical visits are counted once. The learner never invents ON/OFF, gallons, Actual readings, resources, travel, or historical measurements. Historical PROF/report queries retain their existing scope; the 30-day filter is specific to learning.

## Weighting, application and limits

Recent observations use a deterministic **7-calendar-day half-life**. Outliers outside a robust median band (4.5 median absolute deviations, with conservative relative/absolute floors) are excluded from derived learning, not deleted. Configured baselines contribute weight **3**. Fueling-service learning needs at least **3 distinct qualifying physical visits**; the applied service ratio is bounded to **0.5–2.0**. Existing resource-specific relevance weights remain in physical-OFF forecasts.

| Area | Behavior |
|---|---|
| Fueling | Learns verified ON→OFF service relative to the frozen configured service expectation. APPLY uses it in existing physical-OFF forecasts and SPEAR scheduling. This is service throughput, not a fabricated mechanical pump-rate measurement. |
| Completion | Learns OFF→validated final Fueler-data wrap-up. APPLY can refine the existing finishing allowance; candidates are bounded to 0–60 minutes. Dispatcher administrative delay is not trained as Fueler work. |
| Preparation | Recency-weighted available-opportunity→readiness observations remain review-only. They include waiting and cannot safely replace active setup labor or alter physical-start authority. |
| Travel | Configured allowances remain in use. Existing evidence lacks independently measured departure/arrival trip boundaries. SPEAR explicitly reports that limitation rather than treating expected travel as an actual measurement. |
| TOP OFF | Uses exact linked, actor-attributed, inventory-verified SENT/RETURN pairs from the same 30-day window. Requires **10 reliable pairs across 2 sorts** after outlier exclusion, preferring qualified truck history then gateway history. Uses recency-weighted p80 with baseline weight 3, bounded to 2–240 minutes. Both APPLY and the existing learned-TOP-OFF control are required. OBSERVE displays a candidate but uses configured timing. |

OBSERVE never applies candidates. APPLY never enables automation or performs a mutation. SPEAR recommendations remain enabled and automation remains off; production settings are verified read-only at release. No configured pump rate, physical lifecycle rule, inventory calculation, participation rule or historical event is changed by learning.

The existing calibration reset stays scoped to the current sort and excludes pre-reset observations from the corresponding estimate, including active OFF forecast paths. Summary/WHY/TOP OFF text shows qualifying/excluded counts, existing confidence levels, candidates and fallback reasons. Preparation and travel limitations are explicit. Each metric's detailed display is capped at 25 qualifying and 25 excluded observations while counts use the bounded full sample population.

## Efficiency and verification

Dispatch calibration and OFF timing share one compact history projection instead of separately reading current-sort and long-term history. Only the latest archived forecast object is projected, not its complete array. The isolated history/calibration measurement used **four SELECTs**, including batch participation and reset lookup, with **zero writes**. This is not a claim that an entire Dispatch request uses four queries. Day-based weights stay stable within a day, preserving existing computation caches and small SPEAR fragment responses. TOP OFF discovery is bounded at 1,000 pairs and reuses the current reconciliation cadence.

**94 focused Python cases passed**, with 15 passing subtests reported separately. Coverage included calendar/DST cutoff, recent weighting, outliers, duplicate visits, old/future/test/incomplete/unverifiable samples, baseline fallback, OBSERVE/APPLY, TOP OFF opt-in, reset, stable intraday estimates, canonical synthetic completion, unchanged automation settings, immutable capture and existing small-response behavior.

**One disposable PostgreSQL case passed** for the changed joined JSON/history projection, original mission identity and test-origin/gateway exclusion. It used a new local schema; no production database mutation or migration was performed. Compilation and `git diff --check` passed. Existing UTC deprecation warnings remain visible. No broad suite or browser sweep was run.

Four existing focused tests needed contract updates: the legacy calibration serialization fixture has no sealed physical identity and must not qualify; TOP OFF examples now use an explicit historical clock/APPLY; OBSERVE retains manual TOP OFF timing; and the physical-history math fixture now provides final measurement/completion provenance. No tests were deleted, skipped or marked expected failure.

Release uses main and existing Render AutoDeploy. The final release response supplies the exact deployed SHA/deployment ID and read-only health, startup and settings verification. Historical evidence, current operations, unrelated artwork/files, existing jobs and synthetic database directories are preserved.
