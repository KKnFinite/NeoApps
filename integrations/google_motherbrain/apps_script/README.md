# Google MotherBrain Preview Sender

This package contains the container-bound Google Apps Script source for sending a
complete current-sort snapshot from the locked RFD MotherBrain workbook to NeoApps.

The integration remains **preview-only**:

- It does not write to Google Sheet cells.
- It does not create or change Neo operational records.
- It has no Apply action.
- It has no `onEdit`, timed, installable, or automatic trigger.
- It does not retry failed requests automatically.
- It does not publish Neo data back to Google.

## Locked Workbook

| Setting | Required value |
| --- | --- |
| Spreadsheet ID | `10Il5VRW-O3-T9RhrVPvvDphUh03vD-heMbqJwxxmyDg` |
| Spreadsheet title | `RFD-N-sim: Mother Brain` |
| Spreadsheet timezone | `America/Chicago` |
| Gateway | `RFD` |
| Sort | `night` |

The script validates all of these values and the required `Inbound`, `Outbound`, and
`Parking Plan` tabs before reading operational data or sending a request.

## Source Files

- `Code.gs`: read-only snapshot builder, request sender, and menu action.
- `appsscript.json`: V8 manifest with the minimum OAuth scopes and URL allowlist.

The destination is:

```text
POST https://neoapps.onrender.com/integrations/google-motherbrain/current-sort/preview
```

## Workbook Ranges

The sender reads only these ranges:

| Purpose | Range |
| --- | --- |
| Neo operation sort date | `Inbound!H2` |
| Inbound manual rows | `Inbound!A4:G13` |
| Inbound ALP rows | `Inbound!A15:G100` |
| Inbound official order | `Inbound!P4:P100` |
| Outbound manual rows | `Outbound!A4:G13` |
| Outbound ALP rows | `Outbound!A15:G100` |
| Outbound official order | `Outbound!P4:P100` |
| Outbound tail swaps | `Outbound!W4:Z100` |
| Parking normalized lookup | `Parking Plan!BG3:BH100` |

`Outbound!H2` is intentionally not used as the operation sort date. Inbound `H2` is
the Neo `SortDateOperation.sort_date`; outbound `H2` belongs to the following calendar
day for this overnight sort.

The parking helper table is read after `SpreadsheetApp.flush()`. Values such as `A01-b`
and `R03-b` are sent unchanged so Neo can normalize them to lane 2. The script does not
read or calculate visual parking-map coordinates.

## Installation

Installation must be completed manually by the locked workbook owner:

1. Open the locked `RFD-N-sim: Mother Brain` workbook.
2. Open **Extensions -> Apps Script**.
3. Replace the bound project's `Code.gs` contents with the committed `Code.gs` source.
4. In Apps Script **Project Settings**, enable viewing `appsscript.json` if needed.
5. Replace the manifest with the committed `appsscript.json` contents.
6. In **Project Settings -> Script Properties**, add:

   ```text
   NEO_GOOGLE_MOTHERBRAIN_IMPORT_TOKEN=<high-entropy shared token>
   ```

7. Save the project.
8. Run `onOpen` once from Apps Script, or reload the workbook.
9. Authorize only the requested current-spreadsheet and external-request scopes.
10. In the workbook, choose **NeoApps -> PREVIEW CURRENT SORT IN NEO**.

The script does not provide a menu command for ordinary users to set or replace the
token. Never place the token in a sheet cell, source file, manifest, log, or dialog.

## Render Configuration For A Controlled Test

Do not enable the integration until the bound script is installed and a controlled test
window is ready. At that time, configure Render with:

```text
GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=true
GOOGLE_MOTHERBRAIN_IMPORT_TOKEN=<the same high-entropy token>
```

The same token must be stored in Render and in Apps Script Script Properties. Never
store the token in the workbook itself.

The endpoint remains preview-only when enabled. A request returns normalized comparison
results and a fingerprint, but no Neo database records are created, updated, deleted,
cancelled, parked, or unparked.

## OAuth Scopes

The manifest requests exactly:

```text
https://www.googleapis.com/auth/spreadsheets.currentonly
https://www.googleapis.com/auth/script.external_request
```

URL fetch is restricted to:

```text
https://neoapps.onrender.com/
```

There is no Drive-wide scope, web-app deployment, execution API, add-on configuration,
or trigger declaration.

## User Flow

The sender:

1. Acquires a document lock to prevent simultaneous previews.
2. Validates workbook identity, title, timezone, and required tabs.
3. Reads the token from Script Properties.
4. Shows `Building Neo preview...` while collecting the current snapshot.
5. Shows `Sending Neo preview...` before one request.
6. Shows `Preview received.` after a successful response.
7. Displays a compact summary beginning with:

   ```text
   PREVIEW ONLY - NO NEO DATA WAS CHANGED
   ```

The final dialog includes operation identity, fingerprint, inbound/outbound/tail-swap/
parking summaries, and warning/error counts. It never displays the token, request headers,
or full operational payload.

## Disable And Roll Back

1. Set `GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=false` in Render.
2. Remove or disable the bound script menu/source if desired.
3. Remove `NEO_GOOGLE_MOTHERBRAIN_IMPORT_TOKEN` from Apps Script Script Properties.

No data rollback is required because the sender does not write Google data and the Neo
endpoint performs preview only. There is no Apply action. There is no automatic trigger.
