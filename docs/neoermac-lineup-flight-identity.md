# Building Lineup flight identity

Building Lineup keeps its existing gateway/runout geometry and destination columns. The additive nullable JSON column `neoermac_building_lineups.flight_assignments` maps each existing slot field to an integer MasterFlightSchedule primary key. Selection controls submit that ID, displaying Flight Number + Destination. Signed per-slot originals use a new token version, so pre-upgrade forms must refresh before writing.

The established SQLite/PostgreSQL schema synchronization adds the column and backfills legacy rows once. Only one active departure master within the owning gateway can resolve a legacy destination. Ambiguous or missing matches become explicit nulls; the original destination remains visible as SELECT FLIGHT in the editor. Later schedule changes and repeated bootstrap do not replace those choices. Selected retired IDs remain selected and may resolve their exact current mission; selecting them anew is disallowed.

Door View and Upcoming Pulls resolve only the current operation's unique departure whose master_flight_schedule_id equals the selected ID. Zero or multiple matches never fall back to destination, tail, chronology, status, or flight number. Finishing another flight cannot advance the door. Live card membership is keyed by master identity; pull writes remain signed against operation, mission, door, field and prior values. Linked-door writes require the same physical belt and selected master identity.

Aggregation requires doors for that exact master; it preserves canonical historical records for unassigned flights. Upcoming Pulls gap-fills only missing fields for the assigned mission, preserving existing actual/no-pull evidence. East/West/Both limits and the all-mission View Outbound scan board are unchanged. No shared mission-ingestion behavior or production records are manually edited.

Focused validation covers the two-LAX Door 32 case, independent same-destination assignments, stale forms, missing/duplicate mission matches, retired schedules, idempotent legacy upgrade, midnight ordering, gap filling and real Chromium desktop/WebKit mobile live editing.
