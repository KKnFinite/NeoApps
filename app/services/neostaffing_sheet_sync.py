"""Neo-authoritative, bounded Shift staffing mirror. TEST WORKBOOK ONLY.

No external work in employee transactions. A singleton receipt is dirtied in
the canonical transaction and drained by the existing deadline worker.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import uuid4

from flask import current_app, has_app_context
from sqlalchemy import case, event, inspect, select
from sqlalchemy.orm import Session

from app.extensions import db
from app.models.staffing_sheet_sync import StaffingSheetSync as Receipt

TEST_WORKBOOK_ID = '1WakfSwELioqBXbkd4LYd8CJ8BDHRLdsVfuzd0JjMgns'
SHEET_TITLE = 'Shift Staffing'
SHEET_ID = 0
DOORS = (34,32,29,26,24,21,17,13,9,6,4,1)
CLASSIFICATIONS = {'part_time':'PT', 'full_time_combo':'FT',
    'domiciled_full_time_combo':'FT','non_domiciled_full_time_combo':'FT',
    'seasonal':'SEA','pre_seniority':'PRE'}


class StaffingSheetError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enabled():
    override = current_app.config.get('NEOSTAFFING_TEST_SHEETS_SYNC_ENABLED')
    return override is True if override is not None else not current_app.testing and db.engine.dialect.name=='postgresql'


def projection(rows):
    """Only explicit Final Door/Discharge and existing canonical shorthand."""
    groups={f'D{door}':[] for door in DOORS};groups.update(DIS=[],UNASSIGNED=[])
    seen=set()
    for row in rows:
        person,plan,assignment=row['person'],row['plan'],row['assignment']
        if person.id in seen:
            raise StaffingSheetError('Duplicate active Shift placement; resolve in NeoStaffing.')
        seen.add(person.id)
        classification=CLASSIFICATIONS.get(person.classification)
        if classification is None:raise StaffingSheetError('Unsupported employee classification.')
        from app.services.neostaffing import _shift_flow_area_short_label, shift_work_area_type, SHIFT_FLOW_DISCHARGE
        final = _shift_flow_area_short_label(plan.final_door_work_area) if plan and plan.final_door_work_area else ''
        discharge=shift_work_area_type(assignment.work_area)==SHIFT_FLOW_DISCHARGE
        group='DIS' if discharge else final or 'UNASSIGNED'
        if group not in groups:raise StaffingSheetError('Final Door has no matching TEST sheet group.')
        code=(final if discharge else row['shorthand']) if group!='UNASSIGNED' else ''
        if not discharge and group!='UNASSIGNED' and code not in {
                f'{prefix} {phase}' for prefix in ('d','b','-') for phase in ('DOR','BM1','BM2','BM3')}:
            raise StaffingSheetError('Shift Flow must be resolved in NeoStaffing before sync.')
        groups[group].append((f'{person.last_name}, {person.first_name}'.upper(),code,classification,person.id))
    for group,people in groups.items():
        people.sort(key=lambda p:(p[0].casefold(),p[3]))
        capacity=192 if group=='UNASSIGNED' else 59 if group=='DIS' else 20
        if len(people)>capacity:
            raise StaffingSheetError(f'Capacity overflow: {group} has {len(people)} employees; capacity {capacity}. No employee cells were written.')
    return groups


def source_snapshot():
    from app.services import neostaffing as staffing
    context=staffing.shift_flow_context()
    if not context['shift_area_ids']:
        raise StaffingSheetError('Night / Ramp / Shift hierarchy unavailable; refusing to erase TEST roster.')
    return projection(context['rows'])


def cell_map(metadata):
    if metadata.get('spreadsheetId')!=TEST_WORKBOOK_ID:
        raise StaffingSheetError('Refusing a workbook other than the fixed TEST workbook.')
    sheet=next((s for s in metadata.get('sheets',[]) if s['properties']['sheetId']==SHEET_ID),None)
    if not sheet or sheet['properties']['title']!=SHEET_TITLE:
        raise StaffingSheetError('TEST Shift Staffing tab identity changed.')
    cells={}
    for data in sheet.get('data',[]):
        for i,row in enumerate(data.get('rowData',[])):
            for j,cell in enumerate(row.get('values',[])):
                cells[(data.get('startRow',0)+i,data.get('startColumn',0)+j)]=cell
    for index,door in enumerate(DOORS):
        header=cells.get((8,1+4*index),{})
        if header.get('formattedValue')!=f'D{door}':raise StaffingSheetError('TEST Door header layout changed.')
    if cells.get((8,49),{}).get('formattedValue')!='DIS' or cells.get((8,55),{}).get('formattedValue')!='UNASSIGNED':
        raise StaffingSheetError('TEST DIS / UNASSIGNED layout changed.')
    return sheet,cells


def requests_for(metadata, groups):
    """Write only name/code/S cells; C values/formulas and supervisors stay intact."""
    sheet,cells=cell_map(metadata);requests=[];expected={}
    def block(row,col,values):
        local={}
        for i,line in enumerate(values):
            for j,value in enumerate(line):
                old=cells.get((row+i,col+j),{}).get('userEnteredValue',{})
                if 'formulaValue' in old:raise StaffingSheetError('Formula found in an employee-owned cell; no sync performed.')
                expected[(row+i,col+j)]={'stringValue':value} if value else {}
                local[(row+i,col+j)]=expected[(row+i,col+j)]
        if all(cells.get(key,{}).get('userEnteredValue',{})==value for key,value in local.items()):return
        # A precise field mask leaves formats, validation, C columns and all
        # formula-driven projections below the employee block unchanged.
        requests.append({'updateCells':{'range':{'sheetId':0,'startRowIndex':row,'endRowIndex':row+len(values),
            'startColumnIndex':col,'endColumnIndex':col+len(values[0])},'rows':[{'values':[
                {'userEnteredValue':{'stringValue':v}} if v else {} for v in line]} for line in values], 'fields':'userEnteredValue'}})
    for index,group in enumerate([*(f'D{d}' for d in DOORS),'DIS']):
        col=1+index*4;capacity=59 if group=='DIS' else 20;people=groups[group]
        block(11,col,[[people[i][0],people[i][1]] if i<len(people) else ['',''] for i in range(capacity)])
        block(11,col+3,[[people[i][2]] if i<len(people) else [''] for i in range(capacity)])
    block(11,55,[[groups['UNASSIGNED'][i][0]] if i<len(groups['UNASSIGNED']) else [''] for i in range(192)])
    # Only classification dropdowns in the existing C/S employee columns.
    for index in range(13):
        for offset in (2,3):
            col=1+4*index+offset;height=59 if index==12 else 20
            rules={}
            for row in range(11,11+height):
                rule=cells.get((row,col),{}).get('dataValidation')
                if not rule or rule.get('condition',{}).get('type')!='ONE_OF_LIST':continue
                values=rule['condition'].get('values',[])
                if not any(v.get('userEnteredValue') in {'DFT','NFT'} for v in values):continue
                rule=json.loads(json.dumps(rule));seen=[]
                for value in values:
                    value={**value,'userEnteredValue':'FT' if value.get('userEnteredValue') in {'DFT','NFT'} else value.get('userEnteredValue')}
                    if value not in seen:seen.append(value)
                rule['condition']['values']=seen
                if 'inputMessage' in rule:rule['inputMessage']='Classification: PT, FT, SEA, PRE; existing absence overrides retained.'
                key=json.dumps(rule,sort_keys=True)
                rules.setdefault(key,[]).append(row)
            for key,rows in rules.items():
                # Contiguous runs only; never expand over a different rule.
                runs=[]
                for row in rows:
                    if runs and runs[-1][1]==row:runs[-1][1]=row+1
                    else:runs.append([row,row+1])
                for start,end in runs:
                    requests.append({'setDataValidation':{'range':{'sheetId':0,'startRowIndex':start,'endRowIndex':end,
                        'startColumnIndex':col,'endColumnIndex':col+1},'rule':json.loads(key)}})
    # Preserve the workbook's formulas, changing only the obsolete FT criteria.
    for (row,col),cell in cells.items():
        formula=cell.get('userEnteredValue',{}).get('formulaValue','')
        if (row,col)==(2,6) and '"DFT"' in formula:
            requests.append({'updateCells':{'start':{'sheetId':0,'rowIndex':row,'columnIndex':col},
                'rows':[{'values':[{'userEnteredValue':{'formulaValue':formula.replace('"DFT"','"FT"')}}]}],'fields':'userEnteredValue'}})
    # Keep the second legacy counter/formula; it naturally reads zero once all
    # permanent FT classifications are exported as FT. No unrelated tab edits.
    for index,rule in enumerate(sheet.get('conditionalFormats',[])):
        text=json.dumps(rule)
        if '\\"DFT\\"' in text:
            rule=json.loads(text.replace('\\"DFT\\"','\\"FT\\"'))
            requests.append({'updateConditionalFormatRule':{'sheetId':0,'index':index,'rule':rule}})
    if cells.get((2,5),{}).get('userEnteredValue')=={'stringValue':'DOMICILED FT'}:
        requests.append({'updateCells':{'start':{'sheetId':0,'rowIndex':2,'columnIndex':5},
            'rows':[{'values':[{'userEnteredValue':{'stringValue':'FULL TIME'}}]}],'fields':'userEnteredValue'}})
    return requests,expected


class GoogleAdapter:
    def __init__(self, workbook_id=TEST_WORKBOOK_ID):
        if workbook_id!=TEST_WORKBOOK_ID:raise StaffingSheetError('TEST workbook only.')
        from app.services.google_motherbrain_sheets import _credential_json, _parse_service_account_json, _create_gspread_writer
        from app.services.google_diagnostic_transport import diagnostic_http_client
        from functools import partial
        credentials,_=_credential_json(current_app.config)
        self.client=_create_gspread_writer(_parse_service_account_json(credentials),current_app.config,
            http_client=partial(diagnostic_http_client,maximum=4*1024*1024))

    def __enter__(self):return self
    def __exit__(self,*_args):
        from app.services.google_diagnostic_transport import close_google_clients
        close_google_clients([self.client])
    def read(self):
        return self.client.http_client.request('get',f'https://sheets.googleapis.com/v4/spreadsheets/{TEST_WORKBOOK_ID}',
            params={'ranges':"'Shift Staffing'!A1:BD203",'includeGridData':'true',
                'fields':'spreadsheetId,sheets(properties,conditionalFormats,data(startRow,startColumn,rowData(values(userEnteredValue,formattedValue,dataValidation))))'}).json()
    def write(self,requests):
        return self.client.http_client.request('post',f'https://sheets.googleapis.com/v4/spreadsheets/{TEST_WORKBOOK_ID}:batchUpdate',
            json={'requests':requests,'includeSpreadsheetInResponse':False}).json()


def deliver(groups, adapter):
    requests,expected=requests_for(adapter.read(),groups)
    if requests:adapter.write(requests)
    after=adapter.read()
    _sheet,cells=cell_map(after)
    if any(cells.get(key,{}).get('userEnteredValue',{})!=value for key,value in expected.items()):
        raise StaffingSheetError('TEST sheet read-back mismatch; delivery remains pending.')
    remaining,_=requests_for(after,groups)
    if remaining:raise StaffingSheetError('TEST classification rules could not be verified; delivery remains pending.')
    return sha256(json.dumps(groups,sort_keys=True).encode()).hexdigest()


def next_deadline(at):
    if not enabled():return None
    receipt=db.session.get(Receipt,1)
    if receipt is None:return at
    if receipt.requested_revision==receipt.delivered_revision:return None
    return max(receipt.retry_at,receipt.lease_until or at)


def status():
    if not enabled():return None
    row=db.session.get(Receipt,1)
    return dict(pending=not row or row.requested_revision!=row.delivered_revision,
        error=row.last_error if row else None,verified_at=row.verified_at if row else None)


def drain(_gateway_id=None, *, adapter_factory=GoogleAdapter):
    if not enabled():return False
    _dirty(db.session(),initial=True)
    db.session.commit()
    receipt=db.session.execute(select(Receipt).where(Receipt.id==1).with_for_update(skip_locked=True)).scalar_one_or_none()
    at=now()
    if (receipt is None or receipt.requested_revision==receipt.delivered_revision
            or receipt.retry_at>at or receipt.lease_until and receipt.lease_until>at):
        db.session.rollback();return False
    token=uuid4().hex;receipt.lease_token=token;receipt.lease_until=at+timedelta(minutes=5)
    receipt.attempts+=1;db.session.commit()
    revision=None;checksum=None;error=None
    try:
        if db.engine.dialect.name=='postgresql':
            db.session.connection(execution_options={'isolation_level':'REPEATABLE READ'})
        revision=db.session.get(Receipt,1,populate_existing=True).requested_revision
        snapshot=source_snapshot()
        db.session.remove()  # No database transaction/connection during Google I/O.
        with adapter_factory() as adapter:checksum=deliver(snapshot,adapter)
    except Exception as exc:
        # No names, credential details or provider bodies in logs/receipts.
        error=str(exc) if isinstance(exc,StaffingSheetError) else type(exc).__name__
        current_app.logger.warning('Staffing TEST sheet sync deferred: %s',error)
    finally:db.session.remove()
    receipt=db.session.execute(select(Receipt).where(Receipt.id==1).with_for_update()).scalar_one()
    if receipt.lease_token!=token:
        # A very late provider response must not strand an old snapshot behind
        # a newer verified receipt. Request canonical reconciliation again.
        _dirty(db.session());db.session.commit();return False
    receipt.lease_token=None;receipt.lease_until=None
    receipt.last_error=error[:100] if error else None
    if error:
        receipt.retry_at=now()+timedelta(seconds=min(600,30*2**min(receipt.attempts-1,5)))
    else:
        receipt.delivered_revision=revision;receipt.verified_at=now();receipt.content_checksum=checksum;receipt.attempts=0
        receipt.retry_at=now()+timedelta(seconds=1)
    db.session.commit();return not error


def _dirty(session, *, initial=False):
    dialect=session.bind.dialect.name if session.bind is not None else db.engine.dialect.name
    if dialect=='postgresql':from sqlalchemy.dialects.postgresql import insert
    elif dialect=='sqlite':from sqlalchemy.dialects.sqlite import insert
    else:raise RuntimeError('Unsupported Staffing mirror database')
    statement=insert(Receipt.__table__).values(id=1,requested_revision=1,delivered_revision=0,retry_at=now(),attempts=0)
    statement=statement.on_conflict_do_nothing(index_elements=['id']) if initial else statement.on_conflict_do_update(
        index_elements=['id'],set_={'requested_revision':Receipt.requested_revision+1,
            'retry_at':case((Receipt.last_error.is_not(None) & (Receipt.retry_at>now()),Receipt.retry_at),else_=now())})
    session.execute(statement)


def _changed(session,_context):
    if not has_app_context() or not enabled() or session.info.get('staffing_sheet_dirty'):return
    fields={'StaffingPerson':('first_name','last_name','classification','employee_status','active'),
        'StaffingWorkAssignment':('person_id','work_area_unit_id','active'),
        'StaffingShiftFlowPlan':('setup_work_area_id','sort_start_work_area_id','ballmat_transition','final_door_work_area_id'),
        'StaffingUnit':('name','parent_id','active')}
    for row in [*session.new,*session.dirty,*session.deleted]:
        tracked=fields.get(type(row).__name__)
        if tracked and (row in session.new or row in session.deleted or any(inspect(row).attrs[f].history.has_changes() for f in tracked)):
            _dirty(session);session.info['staffing_sheet_dirty']=True;session.info['archive_deadline_hint']=True;break


def _clear(session,*_args):session.info.pop('staffing_sheet_dirty',None)


_installed=False
def install(app):
    global _installed
    if not _installed:
        event.listen(Session,'after_flush',_changed)
        event.listen(Session,'after_commit',_clear)
        event.listen(Session,'after_rollback',_clear)
        _installed=True
