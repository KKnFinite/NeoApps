# Truck Driver and read-only tank data

Truck Driver uses the current SortDateOperation's nightly truck roster. The
selection is stored by operation/truck/user, not in a browser or a second fleet.
It survives sign-in and device changes; a new sort starts with no selections.

Default permissions: Watcher can view; Operator can select their own truck.
Dispatcher management uses existing Fuel Dispatch view/edit authority. Drivers
cannot take another driver's truck. Dispatcher overrides require confirmation
and a reason. Stale target/source selections are rejected atomically. Clear a
driver before removing their truck from the nightly roster.

Two additive tables (`neoscorpion_truck_drivers` and
`neoscorpion_truck_driver_audits`) retain relationship history and actor/UTC
timestamps. Unique constraints enforce one driver per truck and one truck per
driver in each sort. Existing bootstrap creates them idempotently; no existing
fueling rows or identities are migrated or rewritten.

Current work requires trusted physical ON without OFF. Upcoming assignments
sort by full ETD; previous work is confined to the selected sort and keyed by
permanent physical visit IDs. OFF is not administrative COMPLETE. No driver
action changes fueling, inventory, timing, or resource assignments.

Fueling Board and Driver cards use canonical Fueler tank rows and the shared
read-only renderer. Labels/order and Remaining/Planned/Actual values come from
the existing aircraft configuration/calculation services. Missing values show
`-`, measured zero remains zero. Refresh reconciliation keys visits separately,
including A→B→A, and preserves unsaved driver controls and scroll.
