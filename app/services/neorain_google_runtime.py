"""Bounded runtime certification using NeoApps' established Google credentials.

Diagnostics perform GETs only. Ordinary Sheets writes have no conditional
flight/formula assertion: none is certified merely because repeated reads agree.
"""
from app.services.google_motherbrain_sheets import (
    _credential_json, _parse_service_account_json, _create_gspread_writer,
    _create_gspread, GOOGLE_MOTHERBRAIN_WRITE_SCOPES,
)
from app.services.neorain_google_overrides import APPROVED, MB, GoogleOverrideBlocked, cell_number

DRIVE_METADATA_SCOPE = 'https://www.googleapis.com/auth/drive.metadata.readonly'
RACE_REASON = ('No verified Google-side flight/row/formula write fence. Sheets '
               'read-before-write cannot prevent a collaborator changing the row '
               'or inserting a formula before the update. Writer remains disabled.')


def column_index(column):
    value=0
    for char in column:value=value*26+ord(char)-64
    return value-1


def contains(area, row, col, sheet_id):
    return (area.get('sheetId') == sheet_id and
            area.get('startRowIndex',0) <= row < area.get('endRowIndex',float('inf')) and
            area.get('startColumnIndex',0) <= col < area.get('endColumnIndex',float('inf')))


def protection_at(metadata, sheet, row, col):
    """Resolve named and sheet protections, including unprotected holes."""
    named={n['namedRangeId']:n['range'] for n in metadata.get('namedRanges',[])}
    sid=sheet['properties']['sheetId']; applicable=[]
    for rule in sheet.get('protectedRanges',[]):
        area=rule.get('range') or named.get(rule.get('namedRangeId'))
        if area is None:
            return 'UNKNOWN'  # e.g. an unresolved table-backed protection.
        if contains(area,row,col,sid) and not any(contains(h,row,col,sid) for h in rule.get('unprotectedRanges',[])):
            applicable.append(rule)
    if any(not p.get('warningOnly',False) and p.get('requestingUserCanEdit') is not True for p in applicable):
        return 'DENIED'
    return 'EDITABLE' if applicable else 'UNPROTECTED'


def grid_cells(resource):
    result={}
    for sheet in resource.get('sheets',[]):
        for data in sheet.get('data',[]):
            for i,row in enumerate(data.get('rowData',[])):
                for j,cell in enumerate(row.get('values',[])):
                    result[(data.get('startRow',0)+i,data.get('startColumn',0)+j)]=cell
    return result


class RuntimeGoogleAdapter:
    # No configuration boolean may convert observations into a write fence.
    certified_fields=()

    def __init__(self, config):
        self.config=config
        raw,self.credential_source=_credential_json(config)
        try:credentials=_parse_service_account_json(raw)
        except Exception:raise GoogleOverrideBlocked('Runtime Google credentials are unavailable or invalid.') from None
        self.account=credentials['client_email']
        self.writer=_create_gspread_writer(credentials,config)
        # Same service-account source, extra READ-ONLY Drive metadata scope solely
        # to ask Google about this principal's effective canEdit capability.
        self.metadata_client=_create_gspread(credentials,(*GOOGLE_MOTHERBRAIN_WRITE_SCOPES,DRIVE_METADATA_SCOPE),config)
        self.write_scope_requested=set(GOOGLE_MOTHERBRAIN_WRITE_SCOPES).issubset(
            set(self.writer.http_client.auth.scopes or ()))
        self.fallback_matches=None
        import os
        raw_fallback=config.get('GOOGLE_SERVICE_ACCOUNT_JSON') or os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
        if raw_fallback:
            try:self.fallback_matches=_parse_service_account_json(raw_fallback)['client_email']==self.account
            except Exception:self.fallback_matches=False

    def workbook(self, book):
        if book not in {v[0] for v in APPROVED.values()}:
            raise GoogleOverrideBlocked('Workbook is outside the approved allowlist.')
        return self.writer.open_by_key(book)

    def permission_verified(self, book):
        if book not in {v[0] for v in APPROVED.values()}:return False
        response=self.metadata_client.http_client.request('get',
            'https://www.googleapis.com/drive/v3/files/'+book,
            params={'fields':'id,capabilities(canEdit)'})
        payload=response.json()
        return payload.get('id')==book and payload.get('capabilities',{}).get('canEdit') is True and self.write_scope_requested

    def field_snapshot(self, field):
        if field not in APPROVED:raise GoogleOverrideBlocked('No approved destination.')
        book,tab,col,start,end,flight=APPROVED[field]
        wb=self.workbook(book)
        metadata=wb.fetch_sheet_metadata(params={'includeGridData':False,
            'fields':'spreadsheetId,properties(timeZone),namedRanges,sheets(properties,protectedRanges,merges)'})
        if metadata.get('spreadsheetId')!=book:raise GoogleOverrideBlocked('Workbook identity mismatch.')
        sheets=[s for s in metadata.get('sheets',[]) if s['properties']['title']==tab]
        if len(sheets)!=1:raise GoogleOverrideBlocked('Worksheet identity mismatch.')
        sheet=sheets[0]
        raw=wb.fetch_sheet_metadata(params={'includeGridData':True,
            'ranges':[f"'{tab}'!{col}{start}:{col}{end}"],
            'fields':'sheets(data(startRow,startColumn,rowData(values(userEnteredValue,effectiveValue,dataValidation,pivotTable,dataSourceFormula,dataSourceTable))))'})
        return metadata,sheet,grid_cells(raw)

    def inspect_input(self, book, tab, cell):
        import re
        match=re.fullmatch(r'([A-Z]+)([0-9]+)',cell)
        if not match:raise GoogleOverrideBlocked('Invalid input cell.')
        fields=[f for f,spec in APPROVED.items() if spec[0]==book and spec[1]==tab and spec[2]==match[1]]
        if len(fields)!=1:raise GoogleOverrideBlocked('Input is outside the approved allowlist.')
        field=fields[0]; row=int(match[2]); cell_number(field,row)
        metadata,sheet,cells=self.field_snapshot(field); col=column_index(match[1]); data=cells.get((row-1,col),{})
        denied=protection_at(metadata,sheet,row-1,col) in ('DENIED','UNKNOWN')
        # Empty cells cannot be certified non-spill from a missing CellData entry.
        direct=bool(data.get('userEnteredValue')) and 'formulaValue' not in data['userEnteredValue']
        merged=any(contains(area,row-1,col,sheet['properties']['sheetId']) for area in sheet.get('merges',[]))
        generated=any(data.get(k) for k in ('pivotTable','dataSourceFormula','dataSourceTable'))
        return {'cell':data,'spill_owned':not direct or generated,
                'manual_contract_verified':direct and not denied and not merged,
                'atomic_identity_contract_verified':False}

    def read_identities(self, book, tab):
        """Bounded rows, with the locked MotherBrain current operational date."""
        from datetime import datetime,timedelta
        valid=[v for v in APPROVED.values() if v[0]==book and v[1]==tab]
        if not valid:raise GoogleOverrideBlocked('Worksheet is outside the allowlist.')
        bounds=min(v[3] for v in valid),max(v[4] for v in valid)
        context=self.workbook(MB).values_get('Inbound!H2',params={'valueRenderOption':'UNFORMATTED_VALUE'}).get('values',[])
        day=context[0][0] if context and context[0] else None
        if type(day) not in (int,float):raise GoogleOverrideBlocked('Operational date is unavailable.')
        date=(datetime(1899,12,30)+timedelta(days=day)).date().isoformat()
        from app.services.mission_identity import flight_identity
        from app.services.google_motherbrain_import import GOOGLE_MOTHERBRAIN_GATEWAY_CODE, GOOGLE_MOTHERBRAIN_SORT_NAME
        direction='arrival' if tab in ('Inbound','Sim Unload') else 'departure'
        manifest_tab='Inbound' if direction=='arrival' else 'Outbound'
        manifest=self.workbook(MB).values_get(f'{manifest_tab}!P4:T100',params={'valueRenderOption':'UNFORMATTED_VALUE'}).get('values',[])
        first,last=(4,100) if book==MB else bounds
        area=f"'{tab}'!{'P' if book==MB else 'A'}{first}:{'T' if book==MB else 'E'}{last}"
        rows=self.workbook(book).values_get(area,params={'valueRenderOption':'UNFORMATTED_VALUE'}).get('values',[])
        result=[]
        for offset,raw in enumerate(rows):
            row=list(raw)+['']*5
            if not row[0]:continue
            tail,place=(row[2],row[1]) if tab=='Sim Load' else ((row[1],row[2]) if tab=='Outbound' or book==MB or tab=='Sim Unload' else ('',''))
            matches=[m for m in manifest if m and flight_identity(m[0])==flight_identity(row[0])]
            if len(matches)!=1 or len(matches[0])<5:
                raise GoogleOverrideBlocked('Missing or duplicate canonical Google mission.')
            canonical=matches[0]
            if (str(tail).upper()!=str(canonical[1]).upper() or str(place).upper()!=str(canonical[2]).upper()
                or type(row[4]) not in (int,float) or row[4]!=canonical[4]
                or not int(day)<=row[4]<int(day)+2):
                raise GoogleOverrideBlocked('Google tail, route, planned timestamp or operational date mismatch.')
            result.append({'flight':row[0],'tail':str(tail),'place':str(place),'row':first+offset,
                'sort_date':date,'gateway':GOOGLE_MOTHERBRAIN_GATEWAY_CODE,'sort_name':GOOGLE_MOTHERBRAIN_SORT_NAME,
                'direction':direction})
        return result

    def write_input_once(self, *_args):
        # Deliberately no values.update/acell fallback: it cannot enforce a
        # condition on the flight and formula inspected by an earlier GET.
        raise GoogleOverrideBlocked(RACE_REASON)

    def read_input(self, book, tab, cell):
        import re
        match=re.fullmatch(r'([A-Z]+)([0-9]+)',cell)
        if not match or not any(v[0]==book and v[1]==tab and v[2]==match[1] and v[3]<=int(match[2])<=v[4] for v in APPROVED.values()):
            raise GoogleOverrideBlocked('Readback is outside the allowlist.')
        values=self.workbook(book).values_get(f"'{tab}'!{cell}",params={'valueRenderOption':'UNFORMATTED_VALUE'}).get('values',[])
        return values[0][0] if values and values[0] else None

    def diagnose(self, field):
        book,tab,col,start,end,_=APPROVED[field]
        identity={'field':field,'workbook':book,'range':f"'{tab}'!{col}{start}:{col}{end}",
            'status':'BLOCKED','credential_source':self.credential_source,'account':self.account,
            'same_as_neosektor_account':self.fallback_matches,'sheets_write_scope_requested':self.write_scope_requested}
        try:metadata,sheet,cells=self.field_snapshot(field)
        except Exception:
            return {**identity,'authenticated_read_ok':False,'reason':'Authenticated workbook/protection read failed. No write attempted.'}
        try:effective_permission=self.permission_verified(book)
        except Exception:effective_permission=None
        counts=dict(manual_constants=0,formula_cells=0,generated_cells=0,blank_unproven=0,denied_or_unknown=0)
        for row in range(start-1,end):
            cell=cells.get((row,column_index(col)),{}); entered=cell.get('userEnteredValue',{})
            if 'formulaValue' in entered:counts['formula_cells']+=1
            elif entered:counts['manual_constants']+=1
            elif cell.get('effectiveValue'):counts['generated_cells']+=1
            else:counts['blank_unproven']+=1
            if protection_at(metadata,sheet,row,column_index(col)) in ('DENIED','UNKNOWN'):counts['denied_or_unknown']+=1
        return {**identity,'authenticated_read_ok':True,'effective_workbook_write_permission':effective_permission,
            'protected_ranges':len(sheet.get('protectedRanges',[])),'cells':counts,
            'race_contract_verified':False,'reason':RACE_REASON,'isolated_write_test':'NOT_RUN',
            'checked_date':__import__('datetime').datetime.utcnow().isoformat()+'Z'}
