# NeoStaffing production Hours Log cutover — 2026-11-09

## Authority
NeoStaffing's active Shift Flow and person records are authoritative. The real Hours Log is a one-way projection. Legacy Google-only roster names are deliberately replaceable at user direction; the user's backup is the source for subsequent manual NeoStaffing imports. The proof-of-concept TEST workbook is no longer a sync destination.

## Destination
Production spreadsheet: `1j_A1oeGCv92EiylwyOcj_1By_mc1Nakmb_rVdw6QrCY`, tab `Shift Staffing`, sheetId `0`. The service-account writer permission was confirmed. The destination is hard-coded and validated again on every API read; the TEST workbook is rejected.

## Pre-deployment sheet changes
The original 55-column production tab was extended with BD (UNASSIGNED) and its total and supporting header. Summary C2/C3 and G2/G3/G5/G6 use explicit primary classification columns without dividing by two; G3 combines legacy DFT/NFT with FT. DIS total AY10 now counts AX12:AX70. Existing other tabs, supervisors, legacy formulas and employee roster values were preserved until the deployed sync overwrites only its bounded authoritative cells. A separate backup was confirmed by the user, and an additional Drive copy was made earlier in the conversation.

## Delivery
The existing durable receipt has legacy 64-character hashes. A new production-delivery receipt uses `P1` plus 62 hash digits. A previous VERIFIED TEST receipt is considered pending at scheduling; the first production drain dirties it once and commits an initial production snapshot. Subsequent deliveries retain the same lease, retry and readback verification logic. The template status and link reference the production Hours Log. Existing `NEOSTAFFING_TEST_SHEETS_SYNC_ENABLED` is a legacy fallback to avoid unexpectedly changing the deployed enable state; the new primary flag is `NEOSTAFFING_HOURS_LOG_SYNC_ENABLED`.

## Safety
The sync still rejects unexpected formulas in authoritative employee-owned cells, invalid headers, duplicate placements and capacity overflow. It writes only the permitted name, code, classification and unassigned ranges, preserving other tabs and nonowned cells. The Google-only employee discrepancy is consciously permitted by the user.

## Validation
The reconciliation of current production layout, service-account write permission, and absence of formulas in the managed input cells was read-only verified before writing the new summary/BD layout. Targeted code/test updates include an explicit initial-cutover test and rejection of the obsolete TEST workbook. Full production deployment and worker-delivery status must be verified separately; do not claim LIVE based on commit alone.
