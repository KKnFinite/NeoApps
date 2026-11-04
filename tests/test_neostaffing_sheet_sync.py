"""Classification and fixed TEST-sheet authority, preservation and retry proof."""
import copy
from datetime import timedelta
from types import SimpleNamespace as N
import unittest
from unittest.mock import patch

from app.extensions import db
from app.models import StaffingPerson
from app.models.staffing_sheet_sync import StaffingSheetSync as Receipt
from app.services import neostaffing as staffing, neostaffing_sheet_sync as sync
from app.services import neostaffing_bulk_change as bulk
from tests import test_neostaffing_shift_flow as fixtures


def metadata():
    values=[{} for _ in range(56)]
    for i,door in enumerate(sync.DOORS):values[1+4*i]={'formattedValue':f'D{door}'}
    values[49]={'formattedValue':'DIS'};values[55]={'formattedValue':'UNASSIGNED'}
    return dict(spreadsheetId=sync.TEST_WORKBOOK_ID,sheets=[dict(properties=dict(sheetId=0,title=sync.SHEET_TITLE),
        data=[dict(startRow=8,rowData=[dict(values=values)]),dict(startRow=10,startColumn=1,
            rowData=[dict(values=[{'userEnteredValue':{'formulaValue':'=Org!E26'}}])]),
            dict(startRow=11,startColumn=3,rowData=[dict(values=[{'userEnteredValue':{'formulaValue':'=E12'}}])])])])


class Adapter:
    def __init__(self):self.metadata=metadata();self.writes=[];self.closed=0
    def __enter__(self):return self
    def __exit__(self,*args):self.closed+=1
    def read(self):return copy.deepcopy(self.metadata)
    def write(self,requests):
        self.writes.append(requests)
        for request in requests:
            if 'updateCells' not in request:continue
            u=request['updateCells'];r=u.get('range',u.get('start'))
            assert r['sheetId']==0
            self.metadata['sheets'][0]['data'].append(dict(startRow=r.get('startRowIndex',r.get('rowIndex')),
                startColumn=r.get('startColumnIndex',r.get('columnIndex')),rowData=u['rows']))


class StaffingSheetSyncTest(unittest.TestCase):
    setUp=fixtures.ShiftFlowTest.setUp
    tearDown=fixtures.ShiftFlowTest.tearDown
    _person=fixtures.ShiftFlowTest._person

    def employee(self):
        person=self._person()
        staffing.assign_work_area(person,self.door)
        staffing.create_shift_flow_plan(person,dict(shift_flow_sort_start_work_area_id=self.door.id,
            shift_flow_final_door_work_area_id=self.door.id),self.door)
        db.session.commit();return person

    def test_pre_seniority_is_non_management_writable_filterable_and_bulk_normalized(self):
        person=staffing.create_person(dict(employee_id='PRE',first_name='Pre',last_name='Worker',
            seniority_date='2026-11-04',classification='Pre-Seniority'))
        staffing.assign_work_area(person,self.door);db.session.commit()
        self.assertEqual(person.classification,'pre_seniority')
        self.assertIn('pre_seniority',staffing.NON_MANAGEMENT_CLASSIFICATIONS)
        self.assertNotIn('pre_seniority',staffing.MANAGEMENT_CLASSIFICATIONS)
        self.assertIn(('pre_seniority','Pre-Seniority'),staffing.classification_choices())
        self.assertEqual(bulk._normalize_person_field('classification','Pre-Seniority',None),'pre_seniority')
        self.assertEqual(StaffingPerson.query.filter_by(classification='pre_seniority').count(),1)
        with self.assertRaisesRegex(ValueError,'Seniority date'):
            staffing.create_person(dict(employee_id='NO-DATE',first_name='Pre',last_name='Worker',classification='pre_seniority'))
        db.session.rollback()

    def test_projection_preserves_final_flow_codes_discharge_and_classification(self):
        person=self.employee();groups=sync.source_snapshot()
        self.assertEqual(groups['D1'][0][:3],('SMITH, ADA','- DOR','PT'))
        person.classification='pre_seniority';db.session.commit()
        groups=sync.source_snapshot();self.assertEqual(groups['D1'][0][2],'PRE')
        person.shift_flow_plan.final_door_work_area=None;db.session.commit()
        groups=sync.source_snapshot();self.assertEqual(len(groups['UNASSIGNED']),1);self.assertEqual(groups['D1'],[])
        person.shift_flow_plan.final_door_work_area=self.door;db.session.commit()
        self.assertEqual(sync.source_snapshot()['UNASSIGNED'],[])

    def test_delivery_is_idempotent_and_preserves_c_and_supervisor_formulas(self):
        self.employee();groups=sync.source_snapshot();adapter=Adapter()
        checksum=sync.deliver(groups,adapter)
        self.assertEqual(len(checksum),64);self.assertEqual(len(adapter.writes),1)
        _,cells=sync.cell_map(adapter.read())
        self.assertEqual(cells[(11,3)]['userEnteredValue'],{'formulaValue':'=E12'})
        self.assertEqual(cells[(10,1)]['userEnteredValue'],{'formulaValue':'=Org!E26'})
        sync.deliver(groups,adapter);self.assertEqual(len(adapter.writes),1)
        self.assertTrue(all(next(iter(r))=='updateCells' for r in adapter.writes[0]))

    def test_capacity_unknown_headers_and_formula_guard_write_nothing(self):
        self.employee();rows=staffing.shift_flow_context()['rows']
        many=[{**rows[0],'person':N(id=i,classification='part_time',first_name='A',last_name='B')} for i in range(21)]
        with self.assertRaisesRegex(sync.StaffingSheetError,'Capacity overflow'):sync.projection(many)
        adapter=Adapter();adapter.metadata['spreadsheetId']='1j_A1oeGCv92EiylwyOcj_1By_mc1Nakmb_rVdw6QrCY'
        with self.assertRaises(sync.StaffingSheetError):sync.deliver(sync.source_snapshot(),adapter)
        self.assertFalse(adapter.writes)
        with self.assertRaises(sync.StaffingSheetError):sync.GoogleAdapter('production')
        adapter=Adapter();adapter.metadata['sheets'][0]['data'].append(dict(startRow=11,startColumn=45,
            rowData=[dict(values=[{'userEnteredValue':{'formulaValue':'=DO_NOT_TOUCH()'}}])]))
        with self.assertRaisesRegex(sync.StaffingSheetError,'Formula found'):sync.deliver(sync.source_snapshot(),adapter)
        self.assertFalse(adapter.writes)

    def test_commit_intent_rollback_retry_and_no_google_on_employee_save(self):
        person=self.employee();self.app.config['NEOSTAFFING_TEST_SHEETS_SYNC_ENABLED']=True
        person.first_name='New';db.session.flush()
        self.assertIsNotNone(db.session.get(Receipt,1));db.session.rollback()
        self.assertIsNone(db.session.get(Receipt,1))
        person.first_name='Saved';db.session.commit()
        receipt=db.session.get(Receipt,1);revision=receipt.requested_revision
        with patch.object(sync.GoogleAdapter,'__init__',side_effect=AssertionError('No request I/O')):
            person.last_name='Durable';db.session.commit()
        self.assertGreater(db.session.get(Receipt,1,populate_existing=True).requested_revision,revision)
        bad=Adapter()
        with patch.object(bad,'write',side_effect=TimeoutError('lost response')):
            self.assertFalse(sync.drain(adapter_factory=lambda:bad))
        receipt=db.session.get(Receipt,1,populate_existing=True)
        self.assertIsNone(receipt.verified_at);self.assertEqual(bad.closed,1)
        self.assertGreater(sync.next_deadline(sync.now()),sync.now())
        receipt.retry_at=sync.now()-timedelta(seconds=1);db.session.commit()
        good=Adapter();self.assertTrue(sync.drain(adapter_factory=lambda:good))
        self.assertEqual(good.closed,1);self.assertIsNone(sync.next_deadline(sync.now()))

    def test_uncertain_accepted_write_is_verified_without_replay(self):
        self.employee();groups=sync.source_snapshot();adapter=Adapter();write=adapter.write
        def uncertain(requests):write(requests);raise TimeoutError('response lost')
        with patch.object(adapter,'write',side_effect=uncertain),self.assertRaises(TimeoutError):sync.deliver(groups,adapter)
        sync.deliver(groups,adapter);self.assertEqual(len(adapter.writes),1)

    def test_revision_arriving_during_delivery_remains_due(self):
        self.employee();self.app.config['NEOSTAFFING_TEST_SHEETS_SYNC_ENABLED']=True
        adapter=Adapter();write=adapter.write
        def changed(requests):
            write(requests);sync._dirty(db.session());db.session.commit()
        with patch.object(adapter,'write',side_effect=changed):self.assertTrue(sync.drain(adapter_factory=lambda:adapter))
        r=db.session.get(Receipt,1);self.assertGreater(r.requested_revision,r.delivered_revision)
        self.assertIsNotNone(sync.next_deadline(sync.now()))

    def test_expired_attempt_cannot_certify_or_strand_a_late_snapshot(self):
        self.employee();self.app.config['NEOSTAFFING_TEST_SHEETS_SYNC_ENABLED']=True
        adapter=Adapter();write=adapter.write
        def superseded(requests):
            write(requests)
            r=db.session.get(Receipt,1);r.lease_token=None;r.lease_until=None
            r.delivered_revision=r.requested_revision;db.session.commit()
        with patch.object(adapter,'write',side_effect=superseded):self.assertFalse(sync.drain(adapter_factory=lambda:adapter))
        r=db.session.get(Receipt,1,populate_existing=True)
        self.assertGreater(r.requested_revision,r.delivered_revision)
