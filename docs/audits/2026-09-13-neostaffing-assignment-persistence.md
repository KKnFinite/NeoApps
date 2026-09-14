# Work Assignment persistence verification

Base: `40885fd832f707d095a626dde9e1f1d5f3e99e34`.

## Reproduced and fixed

Single Work Area POST and bulk move POST accepted an obsolete page after a
newer assignment committed. The person row lock serialized the requests but
did not compare the client's original state. The later stale request overwrote
the winner. Both HTTP regression tests failed against the base.

Interactive assignment/clear endpoints now require the existing person-level
`shift_flow_version`. It is checked after refreshing/locking the person and
before staging assignment changes. Bulk moves lock people in ID order and
validate every eligible selection before changing any row. Missing versions
are rejected, including old open pages. People bulk forms carry each rendered
employee's version. Internal bootstrap/initialization callers remain unchanged.
No schema, additional reads, or version-storage mechanism was introduced.

## Evidence

- Real assignment/legacy assignment POST, bulk move/clear, manual Home editor,
  and complete-route POST tested against persisted rows using fresh ORM sessions.
- Stale single, bulk, clear, manual Home and route edits preserve newer state.
- FT Night plus other-Sort assignments survive moves, reload and fresh login.
- People profile save preserves both FT assignments. The current People profile
  has no Work Area input; canonical Home editing is on the existing Shift Flow
  employee editor. No new profile controls were invented in this verification.
- Existing authority tests verify distinct-person counts, one assignment per
  Sort, PT limits, leaving Shift removes its plan, and returning starts clean.
- PostgreSQL 17 loopback/disposable schemas: concurrent single and bulk writers
  with the same original produce one success/one conflict; existing complete
  route concurrency and duplicate same-Sort insert protections pass.
- Local browser: People move D6 -> D9; Flow Map shows D9; manual Home -> East
  Ballmat; reload and logout/login retain East Ballmat and Final Door D9.
- Existing JS tests cover single atomic route submission, stale response handling
  without replay, and correct Work Area option selection.

Two unrelated attendance route expectations fail identically in a detached
untouched base checkout: `test_attendance_defaults_to_logged_in_management_scope`
and `test_attendance_route_preselects_scope_and_updates_existing_daily_record`.
They were not changed. No live production data or active Sort was used; this
verification does not claim production behavior was observed.
