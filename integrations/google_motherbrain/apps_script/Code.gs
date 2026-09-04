/**
 * Preview-only sender for the locked RFD MotherBrain workbook.
 *
 * This bound script reads the current workbook snapshot and sends it to NeoApps.
 * It never writes spreadsheet cells and exposes no operational Apply action.
 */

const NEO_MOTHERBRAIN_CONFIG = Object.freeze({
  spreadsheetId: '10Il5VRW-O3-T9RhrVPvvDphUh03vD-heMbqJwxxmyDg',
  spreadsheetTitle: 'RFD-N-sim: Mother Brain',
  timezone: 'America/Chicago',
  gatewayCode: 'RFD',
  sortName: 'night',
  endpoint: 'https://neoapps.onrender.com/integrations/google-motherbrain/current-sort/preview',
  tokenProperty: 'NEO_GOOGLE_MOTHERBRAIN_IMPORT_TOKEN',
  lockTimeoutMs: 10000,
});

const NEO_MOTHERBRAIN_RANGES = Object.freeze({
  sortDate: 'Inbound!H2',
  inboundManual: 'Inbound!A4:G13',
  inboundAlp: 'Inbound!A15:G100',
  inboundOfficialOrder: 'Inbound!P4:P100',
  outboundManual: 'Outbound!A4:G13',
  outboundAlp: 'Outbound!A15:G100',
  outboundOfficialOrder: 'Outbound!P4:P100',
  outboundTailSwaps: 'Outbound!W4:Z100',
  parkingAssignments: 'Parking Plan!BG3:BH100',
});

const NEO_MOTHERBRAIN_REQUIRED_TABS = Object.freeze([
  'Inbound',
  'Outbound',
  'Parking Plan',
]);


function onOpen(event) {
  SpreadsheetApp.getUi()
    .createMenu('NeoApps')
    .addItem('PREVIEW CURRENT SORT IN NEO', 'previewCurrentSortInNeo')
    .addToUi();
}


function previewCurrentSortInNeo() {
  const ui = SpreadsheetApp.getUi();
  const lock = LockService.getDocumentLock();
  let lockAcquired = false;

  try {
    lockAcquired = lock.tryLock(NEO_MOTHERBRAIN_CONFIG.lockTimeoutMs);
    if (!lockAcquired) {
      ui.alert(
        'NeoApps',
        'Another Neo preview is already running. Please try again after it finishes.',
        ui.ButtonSet.OK
      );
      return;
    }

    const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
    const sheets = validateLockedWorkbook_(spreadsheet);
    const token = PropertiesService.getScriptProperties().getProperty(
      NEO_MOTHERBRAIN_CONFIG.tokenProperty
    );
    if (typeof token !== 'string' || token.trim() === '') {
      throw userFacingError_(
        'The Neo integration token is not configured in Apps Script Project Settings.'
      );
    }

    spreadsheet.toast('Building Neo preview...', 'NeoApps', 5);
    const envelope = buildCurrentSortEnvelope_(spreadsheet, sheets);

    spreadsheet.toast('Sending Neo preview...', 'NeoApps', 5);
    const response = sendPreviewRequest_(envelope, token);
    const result = parsePreviewResponse_(response);

    if (result.statusCode === 200) {
      spreadsheet.toast('Preview received.', 'NeoApps', 5);
      ui.alert('NeoApps', previewSuccessMessage_(result.body), ui.ButtonSet.OK);
      return;
    }

    ui.alert(
      'NeoApps',
      previewFailureMessage_(result.statusCode, result.body),
      ui.ButtonSet.OK
    );
  } catch (error) {
    ui.alert('NeoApps', safeLocalErrorMessage_(error), ui.ButtonSet.OK);
  } finally {
    if (lockAcquired) {
      lock.releaseLock();
    }
  }
}


function validateLockedWorkbook_(spreadsheet) {
  if (!spreadsheet || spreadsheet.getId() !== NEO_MOTHERBRAIN_CONFIG.spreadsheetId) {
    throw userFacingError_('This script is locked to the authorized RFD MotherBrain workbook.');
  }
  if (spreadsheet.getName() !== NEO_MOTHERBRAIN_CONFIG.spreadsheetTitle) {
    throw userFacingError_('The active workbook title does not match the authorized workbook.');
  }
  if (spreadsheet.getSpreadsheetTimeZone() !== NEO_MOTHERBRAIN_CONFIG.timezone) {
    throw userFacingError_('The workbook timezone must be America/Chicago.');
  }

  const sheets = {};
  NEO_MOTHERBRAIN_REQUIRED_TABS.forEach(function (tabName) {
    const sheet = spreadsheet.getSheetByName(tabName);
    if (!sheet) {
      throw userFacingError_('Required workbook tab is missing: ' + tabName + '.');
    }
    sheets[tabName] = sheet;
  });
  return sheets;
}


function buildCurrentSortEnvelope_(spreadsheet, sheets) {
  const timezone = spreadsheet.getSpreadsheetTimeZone();
  const inbound = sheets.Inbound;
  const outbound = sheets.Outbound;
  const parkingPlan = sheets['Parking Plan'];

  const sortDateCell = inbound.getRange(
    localA1Range_(inbound, NEO_MOTHERBRAIN_RANGES.sortDate)
  );
  const sortDateRaw = sortDateCell.getValues()[0][0];
  const sortDateDisplay = sortDateCell.getDisplayValues()[0][0];
  const sortDate = formatRequiredSheetDate_(
    sortDateRaw,
    sortDateDisplay,
    timezone,
    'Inbound H2 sort date'
  );

  SpreadsheetApp.flush();

  return {
    schema_version: 1,
    spreadsheet_id: spreadsheet.getId(),
    spreadsheet_title: spreadsheet.getName(),
    gateway_code: NEO_MOTHERBRAIN_CONFIG.gatewayCode,
    sort_name: NEO_MOTHERBRAIN_CONFIG.sortName,
    sort_date: sortDate,
    timezone: timezone,
    submitted_at: new Date().toISOString(),
    snapshot: {
      inbound: {
        manual_rows: readFlightRows_(
          inbound,
          NEO_MOTHERBRAIN_RANGES.inboundManual,
          'origin',
          'manual',
          timezone
        ),
        alp_rows: readFlightRows_(
          inbound,
          NEO_MOTHERBRAIN_RANGES.inboundAlp,
          'origin',
          'alp',
          timezone
        ),
        official_order: readOfficialOrder_(
          inbound,
          NEO_MOTHERBRAIN_RANGES.inboundOfficialOrder
        ),
      },
      outbound: {
        manual_rows: readFlightRows_(
          outbound,
          NEO_MOTHERBRAIN_RANGES.outboundManual,
          'destination',
          'manual',
          timezone
        ),
        alp_rows: readFlightRows_(
          outbound,
          NEO_MOTHERBRAIN_RANGES.outboundAlp,
          'destination',
          'alp',
          timezone
        ),
        official_order: readOfficialOrder_(
          outbound,
          NEO_MOTHERBRAIN_RANGES.outboundOfficialOrder
        ),
        tail_swaps: readTailSwaps_(
          outbound,
          NEO_MOTHERBRAIN_RANGES.outboundTailSwaps
        ),
      },
      parking: {
        assignments: readParkingAssignments_(
          parkingPlan,
          NEO_MOTHERBRAIN_RANGES.parkingAssignments
        ),
      },
    },
  };
}


function readFlightRows_(sheet, a1Range, airportKey, rowType, timezone) {
  const range = sheet.getRange(localA1Range_(sheet, a1Range));
  const rawRows = range.getValues();
  const displayedRows = range.getDisplayValues();
  const firstSheetRow = range.getRow();
  const rows = [];

  displayedRows.forEach(function (displayed, index) {
    const values = displayed.map(trimDisplayedValue_);
    if (rowType === 'alp' && isAlpHeaderRow_(values)) {
      return;
    }
    const flightNumber = values[1];
    const tailNumber = values[3];
    const status = values[5];
    const cancelled = isCancelledStatus_(status);
    const include = rowType === 'manual'
      ? Boolean(tailNumber || (cancelled && flightNumber))
      : values.slice(1, 7).some(Boolean);

    if (!include) {
      return;
    }

    const sheetRow = firstSheetRow + index;
    const row = {
      sheet_row: sheetRow,
      date: formatOptionalSheetDate_(
        rawRows[index][0],
        displayed[0],
        timezone,
        sheet.getName() + ' row ' + sheetRow
      ),
      flight_number: flightNumber,
      tail_number: tailNumber,
      parking: values[4],
      status: status,
      time: values[6],
    };
    row[airportKey] = values[2];
    rows.push(row);
  });

  return rows;
}


function isAlpHeaderRow_(values) {
  return values.length >= 2
    && String(values[0] || '').trim().toLowerCase() === 'date'
    && String(values[1] || '').trim().toLowerCase() === 'flight';
}


function readOfficialOrder_(sheet, a1Range) {
  return sheet.getRange(localA1Range_(sheet, a1Range)).getDisplayValues()
    .map(function (row) { return trimDisplayedValue_(row[0]); })
    .filter(Boolean);
}


function readTailSwaps_(sheet, a1Range) {
  const range = sheet.getRange(localA1Range_(sheet, a1Range));
  const displayedRows = range.getDisplayValues();
  const firstSheetRow = range.getRow();
  const rows = [];

  displayedRows.forEach(function (displayed, index) {
    const values = displayed.map(trimDisplayedValue_);
    if (!values[2] && !values[3]) {
      return;
    }
    rows.push({
      sheet_row: firstSheetRow + index,
      flight_number: values[0],
      destination: values[1],
      new_tail: values[2],
      scorpion_unlock: values[3],
    });
  });
  return rows;
}


function readParkingAssignments_(sheet, a1Range) {
  const rows = [];
  sheet.getRange(localA1Range_(sheet, a1Range)).getDisplayValues().forEach(function (displayed) {
    const tailNumber = trimDisplayedValue_(displayed[0]);
    if (!tailNumber) {
      return;
    }
    rows.push({
      tail_number: tailNumber,
      position: trimDisplayedValue_(displayed[1]),
    });
  });
  return rows;
}


function localA1Range_(sheet, qualifiedRange) {
  const prefix = sheet.getName() + '!';
  if (qualifiedRange.indexOf(prefix) !== 0) {
    throw userFacingError_('A configured workbook range does not match its required tab.');
  }
  return qualifiedRange.slice(prefix.length);
}


function formatRequiredSheetDate_(rawValue, displayedValue, timezone, fieldName) {
  const formatted = formatOptionalSheetDate_(rawValue, displayedValue, timezone, fieldName);
  if (!formatted) {
    throw userFacingError_(fieldName + ' is required.');
  }
  return formatted;
}


function formatOptionalSheetDate_(rawValue, displayedValue, timezone, fieldName) {
  if (rawValue instanceof Date && !isNaN(rawValue.getTime())) {
    return Utilities.formatDate(rawValue, timezone, 'yyyy-MM-dd');
  }

  const displayed = trimDisplayedValue_(displayedValue);
  if (!displayed && (rawValue === '' || rawValue === null)) {
    return '';
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(displayed)) {
    return displayed;
  }
  throw userFacingError_(fieldName + ' must contain a valid spreadsheet date.');
}


function sendPreviewRequest_(envelope, token) {
  return UrlFetchApp.fetch(NEO_MOTHERBRAIN_CONFIG.endpoint, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(envelope),
    muteHttpExceptions: true,
    followRedirects: false,
    headers: {
      'X-Neo-Integration-Token': token,
    },
  });
}


function parsePreviewResponse_(response) {
  const statusCode = response.getResponseCode();
  let body = null;
  try {
    body = JSON.parse(response.getContentText());
  } catch (error) {
    body = null;
  }
  return {statusCode: statusCode, body: body};
}


function previewSuccessMessage_(body) {
  if (!body || body.ok !== true || body.preview_only !== true || !body.operation) {
    return 'Neo returned an invalid preview response. No Neo data was changed.';
  }

  const summary = body.summary || {};
  return [
    'PREVIEW ONLY \u2014 NO NEO DATA WAS CHANGED',
    '',
    'Operation ID: ' + safeText_(body.operation.id),
    'Sort date: ' + safeText_(body.operation.sort_date),
    'Gateway: ' + safeText_(body.operation.gateway_code),
    'Sort: ' + safeText_(body.operation.sort_name),
    'Fingerprint: ' + safeText_(body.fingerprint),
    '',
    compactSummaryLine_('Inbound', summary.inbound),
    compactSummaryLine_('Outbound', summary.outbound),
    compactSummaryLine_('Tail swaps', summary.tail_swaps),
    compactSummaryLine_('Parking', summary.parking),
    'Warnings: ' + arrayLength_(body.warnings),
    'Errors: ' + arrayLength_(body.errors),
  ].join('\n');
}


function compactSummaryLine_(label, summary) {
  return label + ': ' + JSON.stringify(summary || {});
}


function previewFailureMessage_(statusCode, body) {
  const safeProviderMessage = returnedSafeMessage_(body);
  if (statusCode === 400) {
    return safeProviderMessage || 'Neo rejected the snapshot validation.';
  }
  if (statusCode === 401) {
    return 'Neo rejected the integration credentials.';
  }
  if (statusCode === 404) {
    return safeProviderMessage ||
      'The Neo preview bridge is disabled, unavailable, or has no matching operation.';
  }
  if (statusCode === 409) {
    return safeProviderMessage || 'Neo found more than one matching operation.';
  }
  if (statusCode === 413) {
    return 'The current-sort snapshot is too large for the Neo preview bridge.';
  }
  if (statusCode === 415) {
    return 'Neo rejected the preview content type or integration configuration.';
  }
  return 'Neo could not generate the preview. No Neo data was changed.';
}


function returnedSafeMessage_(body) {
  if (!body || !body.error || typeof body.error.message !== 'string') {
    return '';
  }
  return body.error.message.replace(/[\r\n]+/g, ' ').slice(0, 300);
}


function safeLocalErrorMessage_(error) {
  if (error && error.neoUserFacing === true && typeof error.message === 'string') {
    return error.message;
  }
  return 'The Neo preview could not be sent. No Neo data was changed.';
}


function userFacingError_(message) {
  const error = new Error(message);
  error.neoUserFacing = true;
  return error;
}


function isCancelledStatus_(value) {
  const normalized = trimDisplayedValue_(value).toUpperCase();
  return normalized === 'CNL' || normalized === 'CANCELLED';
}


function trimDisplayedValue_(value) {
  return String(value === null || value === undefined ? '' : value).trim();
}


function safeText_(value) {
  return value === null || value === undefined ? '' : String(value);
}


function arrayLength_(value) {
  return Array.isArray(value) ? value.length : 0;
}
