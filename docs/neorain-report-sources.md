# NeoRain Rainrock and Daily Briefing sources

Both pages resolve each metric for a selected `SortDateOperation`, in this order:
valid canonical Neo fact, sort-scoped NeoRain operator entry, dated Google RFD
Recap snapshot, unavailable. Numeric zero is valid. The same resolved fields feed
Rainrock and Daily Briefing.

| Field | Neo authority when available | Google RFD Recap fallback |
| --- | --- | --- |
| Hub / Shift actual working | NeoStaffing Hub Operation attendance: finalized retained summary, or fully marked current-sort attendance | `Inputs!B6` |
| Hub planned/actual payroll, planned working | No verified Neo payroll/plan fact yet; active roster counts are not substituted | `Inputs!B3:B5` |
| Ramp planned payroll / actual payroll / planned working / actual working | No approved Ramp-only Neo source yet. NeoRain's combined Ramp display is never used. | `Inputs!B9`, `C10`, `B11`, `C12` respectively |
| Planned HPS volume / actual volume / smalls processed % / planned FPH / actual FPH | No verified Neo owner yet | `Inputs!B18:B22` |
| Five additional sort notes | Sort-scoped NeoRain entry | `Inputs!B24:B28` |
| Aircraft timing, lateness and Include/Exclude | Selected operation's NeoRain/SortDateMission records | None |
| Delay codes and notes | NeoRainDelayInfo on the selected departure mission | None |

Google access reuses the existing read-only service-account configuration. The
service account must also have Viewer access to the RFD Recap workbook. One
bounded batch of the listed cells is cached for two minutes per process. The
`Completed Form!B2` reporting date must match the selected Neo operational
date before current-sort values are snapshotted. A mismatch, formula error,
blank, or outage never becomes zero. A date mismatch does **not** imply the
worksheet is stale; it simply means this integration has not verified that
workbook as belonging to the selected Neo sort. The source must be validated
before an alternative reporting-date convention is adopted. Historical sorts
use only their own persisted Google snapshots and canonical Neo data; they
never read the latest worksheet as their fallback.

The report-entry table stores manual values and Google snapshots separately
under the gateway, operation, date, sort, field and source. Operators can edit
unowned inputs; manual entries are never replaced by Google. A cleared manual
entry retains its audit/version row while revealing any valid Google fallback.
Future NeoReptile Ramp integration should add a canonical per-field resolver,
not replace this report model or reinterpret NeoRain combined staffing.
