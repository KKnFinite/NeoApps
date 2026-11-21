# RFD NeoSektor mirror retirement decision

**HOLD.** Native NeoRaiden can operate independently. Two external consumers still depend on the operational Google worksheet.

## Production evidence, November 21, 2026

| Component | Observed authority and behavior |
| --- | --- |
| Integrated NeoSektor | PostgreSQL setting and authenticated MotherBrain Integrations both report NEO PRIMARY + GOOGLE MIRROR. Mirror writes are enabled. The persisted full-sync warning is outstanding. The last recorded failure was November 20 at 06:58 UTC. |
| Native NeoRaiden | The authorized Sektor projection in `app/neonodes/neoraiden/services.py` calls `native_uld_summary` for the selected gateway, canonical Sort date and name. That service reads native Sort, wave, ballmat, per-wave side counts and open-bay rows. It calculates rollups on copies and evaluates wave timers without persisting them. Missing inputs stay unavailable. |
| Google Raiden Display | Ten formulas still import directly from the NeoSektor workbook. Display M6:P6 imports Live-Counts B2:E2, M8:P8 imports B3:E3, and M10:N10 imports B4:C4. These are the East/West counts and remaining-arrival/unload values for both waves, and East/West open bays. |
| Standalone NeoSektor | Render serves commit `4f7787e8d7c6f45c45eb7cdd67081abe4be4b839`. Its public Live Counts and Driver Routing use the Sheets-based application. On cache expiry its Sheets service reads Live-Counts A1:E15 and B15. The deployed code can also write counters and routing under its own Google-write switch. The standalone administrator screen requires a separate login, so the current process-local write switch was not independently verified. |
| Shared authority | The integrated administration page shows NOT ACTIVATED. The source workbook contains only Live-Counts. The optional generation-fenced shared authority contract uses its own worksheet when configured. Operator reconciliation and takeover are separate, explicit actions. |
| Other publishers | NeoStaffing publishes staffing to Google Raiden independently of those ten formulas. Its Google Production Hours Log and MotherBrain integrations are separate. NeoRain remains NEO ONLY. |

The November 19 RFD Night native state records open bays East 0, West 4. The November 18 state records East 3, West 5. The Friday test operation is archived. At 13:02 Central on November 21 the Matrix and operation windows showed no active RFD Sort. Sort availability alone does not clear the two external dependencies.

## Google I/O paths

* Google Primary uses the legacy operational cell reader and write-through functions in `app/services/neosektor_sheets_compat.py`.
* In Neo Primary + Google Mirror, an authorized Sektor change commits to PostgreSQL first. The post-commit publisher compares the old and new cell snapshots and writes changed cells. A failed write sets the sync warning. Ordinary later writes do not certify the entire worksheet.
* The retry and explicit mirror-enable controls perform a full synchronization. The retry has no autonomous scheduler in this repository. NEO ONLY skips the operational mirror and rejects retry. An explicitly configured shared authority worksheet has a separate contract.
* No production retry, takeover, reconciliation, spreadsheet write, or authority transition was executed for this audit. Read-only inspection cannot enumerate undisclosed third-party clients of a shared spreadsheet.

## Rehearsal and retirement prerequisites

1. Move the Google Raiden Display ULD audience to native NeoRaiden, or migrate the ten dependent cells through a separately approved change. The staffing publisher can continue independently.
2. Move or formally retire the standalone Live Counts and Driver Routing audience. Verify the standalone write switch and any failover agreement with its administrator.
3. During a confirmed gap in genuine operations, fingerprint the nine NeoSektor tables, capture the existing authority settings, and select NEO ONLY in MotherBrain Integrations. Recheck the persisted settings, native displays, fingerprints, and logs. Separate the expected settings-row change from operational changes.
4. For rollback, select NEO PRIMARY + GOOGLE MIRROR in the same control. Mirror writes start OFF. Review the canonical native values before explicitly enabling a full synchronization back to Google. A failed enable leaves writes disabled. Restore standalone authority only through the separate supported handoff if it has been activated.

The isolated PostgreSQL round trip checked all rows and columns of the five ULD input tables, the read-only Raiden state response, zero external HTTP/worksheet calls and persisted writes-OFF on rollback. Browser tests use synthetic Sorts and validate updates on desktop, mobile and TV. The release changes automatic refresh bounds only.
