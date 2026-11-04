# NeoScorpion explicit assignment removal

Starting SHA: `c1f6b6cef90d7dbf7a865054b8e07ae66e87b68d` (fetched `origin/main`).

## Root cause and correction

Fuel assignments belong to a mission, not its mutable tail. A MotherBrain OOS/replacement action correctly retains the assignment, because it may be intended for the replacement aircraft. Additionally, retained unstarted old-tail preparation could still be selected through the physical-segment projection using its original Fueler. There was no explicit full-resource removal action in Dispatcher Fueling Operations; truck-only unassignment did not remove the Fueler.

The existing shared Details → Fueling Operations section now contains a compact REMOVE ASSIGNMENT disclosure. It requires a reason, confirmation checkbox and existing lifecycle confirmation. The new POST uses the existing Dispatcher edit permission, gateway gate, CSRF enforcement, lifecycle handler and revision-first refresh. No new JavaScript, schema, worker or movement engine is introduced.

Under the existing operation/assignment locks, removal verifies the exact current tail, cycle, WorkState reference, Fueler, truck and assignment timestamp. A stale or duplicate request returns HTTP 409. It clears only active resource links and their assignment timestamps. Mission identity, current tail, OOS state, WorkStates, measurements, planned fuel, physical attribution and inventory are retained. The existing audit table/action `unassign_truck`, distinguished by `field_name=resource_assignments`, stores previous resource IDs, actor, time and the supplied reason. SPEAR observes the existing unassignment action.

Unstarted preparation follows current assignment authority in both screen selection and explicit source-segment access. It cannot remain visible/editable for a removed Fueler or regain that former authority after reassignment to another Fueler. Genuine physical-source visits retain their established historical ownership.

Ordinary removal rejects physical ON, raw uncertain ON, OFF, transfer/review evidence, current-cycle physical events, Fueler submission and completed/FOB assignments. It checks old-tail/current-cycle and legacy work too, so replacing a tail cannot hide physical work. Such cases must use the existing OFF/review/correction process; removal never fabricates OFF, completion, an event or a deduction. No tail change automatically invokes removal.

## Focused validation

All mutations used synthetic local records and blocked external providers.

| Check | Final result |
| --- | --- |
| Removal, existing cancellation/unassignment and Fueler handoff modules | 47 passed; 16 passing subtests |
| Disposable loopback PostgreSQL, including inherited removal cases | 10 passed; 12 passing subtests |
| Existing lifecycle confirmation, Fueler data and autosave JavaScript | 14 passed |
| One OOS replacement/removal workflow in Chromium and WebKit | 1 method passed; both engine subtests passed |
| Changed Python compilation, reused live JavaScript syntax, `git diff --check` | Passed |

PostgreSQL independently exercises duplicate removals (one winner/audit), removal versus physical ON (only one succeeds), committed preparation versus removal, explicit rollback, stale identity, CSRF and Dispatcher authority. Synthetic schemas are unique; final tests retain them and dispose connections, without cleaning existing databases/directories.

The browser workflow uses a 390px Fueler and 1440px Dispatcher. It shows the original assignment, marks the original synthetic tail OOS, replaces the mission tail, cancels confirmation, then confirms removal. The existing shipped live controller removes the Fueler card without a page reload. Mission replacement/OOS and all three saved tank records remain; no ON/OFF or physical event appears. Screenshots and JUnit/log evidence are retained under ignored `instance/remove-assignment-20261104/`.

Initial reproduction failed because no removal service existed. The first PostgreSQL run exercised its assertions but failed in new test teardown: table removal left schema-owned trigger functions, so dropping that schema was rejected. Final teardown retains the synthetic schema instead. No old failed schema/directory cleanup was attempted. Existing datetime deprecation warnings remain visible; none were suppressed.

## Release and limits

Only the focused service, route, shared template, three new test files and this report are included. Production employee/aircraft records, Google Sheets, R2 archives, artwork, existing Codex work and synthetic directories are not modified by tests. No broad regression inventory or production operational mutation is part of this release.

Release verification must confirm the pushed commit through Render AutoDeploy, successful bootstrap/worker startup, health/login and rollout logs. The final deployment ID and exact SHA are reported in the delivery message. Removal intentionally does not automatically resolve physical work, cancel future truck plans or repair historical assignments.
