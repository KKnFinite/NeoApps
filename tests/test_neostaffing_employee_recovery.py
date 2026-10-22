"""Employee recovery uses only isolated fixtures, with real FK/immutable guards."""
import base64
import hashlib
import json
import unittest
from datetime import date,datetime,timedelta
from unittest.mock import patch

from sqlalchemy import select,text
from sqlalchemy.exc import DatabaseError
from app.extensions import db
from app.models import (User,PortalAppAccess,StaffingPerson,StaffingPersonQualification,
    StaffingShiftFlowPlan,StaffingChangeRequest,StaffingChangeRequestItem,
    StaffingChangeRequestEvent,StaffingNotification,SortDateMission,NeoSubZeroUccAssignment)
from app.models.staffing_employee_record import StaffingEmployeeRecord as Record,StaffingEmployeeRecordEvent as RecordEvent
from app.models.staffing_employee_recovery import StaffingEmployeeRecovery as Backup,StaffingEmployeeRecoveryAudit as Audit
from app.models.staffing_timecard import StaffingTimecardSlice as Slice,StaffingTimecardSegment as Segment,StaffingTimecardWeek as Week
from app.services import neostaffing as staffing
from app.services import neostaffing_employee_recovery as recovery
from tests import test_neostaffing_timecards as fixture_module


class MemoryFiles:
    def __init__(self): self.objects={};self.fail=False
    def get(self,key,**_kwargs): return self.objects[key]
    def put(self,key,raw,**_kwargs): self.objects[key]=raw
    def delete(self,key):
        if self.fail: raise OSError('storage unavailable')
        self.objects.pop(key,None)


class EmployeeRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture_module.TimecardsTest();self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.app,self.client=self.fixture.app,self.fixture.client
        self.actor=self.fixture.user
        access=PortalAppAccess.query.filter_by(user_id=self.actor.id,app_code='neostaffing').first()
        if access: access.role='grandmaster';access.status='approved';access.is_active=True
        else: db.session.add(PortalAppAccess(user_id=self.actor.id,app_code='neostaffing',role='grandmaster',status='approved',is_active=True))
        db.session.commit()
        self.person=self.fixture.workers['ebm'];self.pid=self.person.id
        self.files=MemoryFiles();self.app.config['EMPLOYEE_RECORD_SIGNATURE_STORAGE']=self.files

    def confirmation(self):
        person=db.session.get(StaffingPerson,self.pid)
        home=staffing.assignment_service.shift_home(person)
        return dict(shared_editor='1',confirm_delete='yes',confirmed_employee_id=person.employee_id,
            expected_person_version=staffing.entity_version(person),
            expected_version=staffing.shift_flow_revision(person,person.shift_flow_plan,home) if home else '')

    def delete(self):
        result=recovery.delete_employee(self.actor,self.pid,self.confirmation())
        bid=result.id;db.session.commit();return db.session.get(Backup,bid)

    def rich_data(self):
        timecard=self.fixture.attendance(self.person)
        db.session.add(Segment(slice_id=timecard.id,start_utc=datetime(2026,9,13,3),end_utc=datetime(2026,9,13,7)))
        self.row_id=timecard.id
        self.actor.employee_id=self.person.employee_id
        db.session.add(StaffingPersonQualification(person_id=self.pid,qualification_key='deice_driver'))
        db.session.add(StaffingShiftFlowPlan(staffing_person_id=self.pid,sort_start_work_area_id=self.fixture.areas['ebm'].id))
        record=Record(person_id=self.pid,kind='verbal',body='Complete confidential employee record',
            context_json='[]',created_by=self.actor.id,finalized_at=datetime.utcnow(),finalized_by=self.actor.id,
            acknowledgment='delivered')
        db.session.add(record);db.session.flush();self.record_id=record.id
        db.session.add(RecordEvent(record_id=record.id,sequence=1,kind='created',body='Exact audit evidence',actor_id=self.actor.id))
        request=StaffingChangeRequest(person_id=self.pid,submitted_by_user_id=self.actor.id)
        other=StaffingChangeRequest(person_id=self.fixture.peer.id,submitted_by_user_id=self.actor.id,
            submitted_by_person_id=self.pid,routed_approver_person_ids_json=json.dumps([self.pid,self.fixture.manager.id]))
        db.session.add_all([request,other]);db.session.flush();self.other_request=other.id
        db.session.add(StaffingChangeRequestItem(request_id=request.id,person_id=self.pid,field_name='first_name'))
        db.session.add(StaffingChangeRequestEvent(request_id=request.id,event_type='submitted',actor_user_id=self.actor.id))
        db.session.add(StaffingNotification(recipient_user_id=self.actor.id,change_request_id=request.id,
            notification_type='new_request',message='Target record',dedupe_key='recovery-test'))
        op=self.fixture.operation
        mission=SortDateMission(sort_date_operation_id=op.id,sort_date=op.sort_date,gateway_code=op.gateway_code,
            sort_name=op.sort_name,mission_type='departure',mission_source='manual',flight_number='UPS1476',
            assigned_tail_number='N444UP',origin=op.gateway_code,destination='SDF',load_planner_person_id=self.pid)
        slot=NeoSubZeroUccAssignment(sort_date_operation_id=op.id,ramp='Alpha',position_number=1,
            team_role='driver',person_id=self.pid,assigned_by_user_id=self.actor.id)
        db.session.add_all([mission,slot]);db.session.commit();self.mission_id=mission.id;self.slot_id=slot.id

    def test_full_roundtrip_preserves_users_peers_shared_records_and_exact_evidence(self):
        self.rich_data()
        user_id=self.actor.id;peer_id=self.fixture.peer.id
        before=bytes(Record.query.get(self.record_id).body.encode())
        backup=self.delete();bid=backup.id
        self.assertEqual(backup.expires_at-backup.created_at,timedelta(hours=24))
        self.assertNotIn(before,backup.encrypted_payload)
        self.assertIsNone(db.session.get(StaffingPerson,self.pid))
        self.assertIsNotNone(db.session.get(User,user_id));self.assertIsNotNone(db.session.get(StaffingPerson,peer_id))
        self.assertIsNotNone(db.session.get(SortDateMission,self.mission_id))
        self.assertIsNone(db.session.get(SortDateMission,self.mission_id).load_planner_person_id)
        self.assertIsNone(db.session.get(NeoSubZeroUccAssignment,self.slot_id).person_id)
        other=db.session.get(StaffingChangeRequest,self.other_request)
        self.assertIsNone(other.submitted_by_person_id)
        self.assertEqual(json.loads(other.routed_approver_person_ids_json),[self.fixture.manager.id])
        self.assertIsNone(db.session.get(Slice,self.row_id));self.assertIsNone(db.session.get(Record,self.record_id))
        self.assertEqual(recovery.restore_employee(self.actor,bid),self.pid);db.session.commit()
        self.assertEqual(recovery.restore_employee(self.actor,bid),self.pid);db.session.commit()
        self.assertEqual(db.session.get(Record,self.record_id).body.encode(),before)
        self.assertEqual(db.session.get(SortDateMission,self.mission_id).load_planner_person_id,self.pid)
        self.assertEqual(Segment.query.filter_by(slice_id=self.row_id).count(),1)
        self.assertEqual(RecordEvent.query.filter_by(record_id=self.record_id).count(),1)
        self.assertEqual(Audit.query.filter_by(recovery_id=bid,action='restored').count(),1)
        self.assertIsNone(Week.query.first().purge_after)
        if db.engine.dialect.name=='sqlite':self.assertFalse(db.session.execute(text('PRAGMA foreign_key_check')).all())

    def test_confirmation_cancel_and_stale_version_never_create_backup(self):
        values=self.confirmation()
        for changes in ({'confirm_delete':''},{'confirmed_employee_id':'wrong'}, {'expected_person_version':'stale'}, {'expected_version':'stale'}):
            response=self.client.post(f'/neostaffing/app-management/people/{self.pid}/delete',data={**values,**changes})
            self.assertIn(response.status_code,(400,409))
            self.assertIsNotNone(db.session.get(StaffingPerson,self.pid))
            self.assertEqual(Backup.query.count(),0)

    def test_backup_failure_and_mid_delete_failure_roll_back_everything(self):
        self.rich_data();values=self.confirmation()
        with patch.object(recovery,'seal',side_effect=ValueError('Backup unavailable')):
            response=self.client.post(f'/neostaffing/app-management/people/{self.pid}/delete',data=values)
        self.assertEqual(response.status_code,400);self.assertEqual(Backup.query.count(),0)
        from sqlalchemy import event
        def fail(_connection,_cursor,statement,_params,_context,_many):
            if statement.startswith('DELETE FROM staffing_people'):raise RuntimeError('Simulated deletion failure')
        event.listen(db.engine,'before_cursor_execute',fail)
        try:
            with self.assertRaises(RuntimeError): recovery.delete_employee(self.actor,self.pid,values)
        finally:
            event.remove(db.engine,'before_cursor_execute',fail);db.session.rollback()
        self.assertIsNotNone(db.session.get(StaffingPerson,self.pid))
        self.assertIsNotNone(db.session.get(Record,self.record_id));self.assertEqual(Backup.query.count(),0)
        self.assertEqual(db.session.get(SortDateMission,self.mission_id).load_planner_person_id,self.pid)

    def test_exact_expiry_boundary_and_purge_payload_files(self):
        self.rich_data()
        # Create an independent legacy signature before immutable finalization.
        record=Record(person_id=self.pid,kind='talk_with',body='Signed record',context_json='[]',created_by=self.actor.id)
        db.session.add(record);db.session.flush()
        raw=b'isolated private signature bytes';key=f'employee-records/{record.id}/signature.png'
        self.files.objects[key]=raw
        record.signature_key=key;record.signature_sha256=hashlib.sha256(raw).hexdigest();record.signature_size=len(raw)
        record.finalized_at=datetime.utcnow();record.finalized_by=self.actor.id;record.acknowledgment='signature'
        db.session.commit();backup=self.delete();bid=backup.id;expires=backup.expires_at
        self.assertIn(key,self.files.objects)
        with patch.object(recovery,'now_utc',return_value=expires):
            with self.assertRaisesRegex(ValueError,'expired'):recovery.restore_employee(self.actor,bid)
        db.session.rollback()
        self.assertEqual(recovery.purge_expired(at=expires-timedelta(microseconds=1)),0);db.session.commit()
        self.files.fail=True
        with self.assertRaises(OSError):recovery.purge_expired(at=expires)
        db.session.rollback();self.assertIsNotNone(db.session.get(Backup,bid).encrypted_payload)
        self.files.fail=False
        self.assertEqual(recovery.purge_expired(at=expires),1);db.session.commit()
        self.assertNotIn(key,self.files.objects)
        self.assertIsNone(db.session.get(Backup,bid).encrypted_payload)
        self.assertEqual(recovery.purge_expired(at=expires+timedelta(seconds=1)),0)

    def test_shared_record_conflict_never_overwrites_newer_assignment(self):
        self.rich_data();backup=self.delete();bid=backup.id
        slot=db.session.get(NeoSubZeroUccAssignment,self.slot_id);slot.person_id=self.fixture.peer.id
        db.session.commit()
        with self.assertRaisesRegex(recovery.RecoveryConflict,'changed after deletion'):recovery.restore_employee(self.actor,bid)
        db.session.rollback()
        self.assertIsNone(db.session.get(StaffingPerson,self.pid))
        self.assertEqual(db.session.get(NeoSubZeroUccAssignment,self.slot_id).person_id,self.fixture.peer.id)

    def test_missing_signature_aborts_deletion_and_restored_signature_survives_purge(self):
        self.rich_data()
        adapter=self.app.config['EMPLOYEE_RECORD_SIGNATURE_STORAGE']
        self.assertFalse(adapter.objects)
        # Ordinary immutable history cannot be changed/deleted without recovery.
        with self.assertRaises(DatabaseError):db.session.execute(RecordEvent.__table__.delete())
        db.session.rollback()
        with self.assertRaises(DatabaseError):db.session.execute(Record.__table__.delete().where(Record.id==self.record_id))
        db.session.rollback()
        backup=self.delete();bid=backup.id;expires=backup.expires_at
        recovery.restore_employee(self.actor,bid);db.session.commit()
        recovery.purge_expired(at=expires);db.session.commit()
        self.assertIsNotNone(db.session.get(Record,self.record_id))
        self.assertIsNone(db.session.get(Backup,bid).encrypted_payload)

    def test_policy_covers_all_current_person_foreign_keys_without_following_shared_parents(self):
        for table in db.metadata.tables.values():
            for fk in table.foreign_keys:
                if fk.column.table.name=='staffing_people':
                    self.assertIn(table.name,recovery.OWNED|recovery.SHARED)
        self.assertNotIn('users',recovery.OWNED);self.assertNotIn('sort_date_missions',recovery.OWNED)

    def test_reinstalling_schema_guards_is_safe_and_history_stays_immutable(self):
        self.rich_data()
        from app.services.neostaffing_employee_recovery_schema import install_recovery_guards
        install_recovery_guards(db.session.connection())
        install_recovery_guards(db.session.connection())
        db.session.commit()
        with self.assertRaises(DatabaseError):
            db.session.execute(Record.__table__.delete().where(Record.id==self.record_id))
        db.session.rollback()
        backup=self.delete()
        recovery.restore_employee(self.actor,backup.id);db.session.commit()
        self.assertIsNotNone(db.session.get(Record,self.record_id))

    def test_nested_vacation_records_roundtrip_without_removing_shared_turn(self):
        from app.models import (StaffingVacationUnionSelection as Selection,StaffingVacationWeekConversion as Conversion,
            StaffingVacationDaySelection as Day,StaffingVacationManagementTurnState as Turn,
            StaffingVacationManagementTurnResolution as Resolution)
        selection=Selection(staffing_person_id=self.pid,vacation_year=2026,week_ending=date(2026,11,7),bank_type='regular',status='approved')
        turn=Turn(area_unit_id=self.fixture.areas['ebm'].id,vacation_year=2026,current_person_id=self.pid)
        db.session.add_all([selection,turn]);db.session.flush()
        conversion=Conversion(staffing_person_id=self.pid,vacation_year=2026,program='union',source_union_selection_id=selection.id)
        resolution=Resolution(turn_state_id=turn.id,staffing_person_id=self.pid,outcome='completed')
        db.session.add_all([conversion,resolution]);db.session.flush()
        day=Day(staffing_person_id=self.pid,vacation_year=2026,vacation_date=date(2026,11,2),conversion_id=conversion.id,status='scheduled')
        db.session.add(day);db.session.commit();sid,cid,did,tid,rid=selection.id,conversion.id,day.id,turn.id,resolution.id
        backup=self.delete();bid=backup.id
        for model,key in ((Selection,sid),(Conversion,cid),(Day,did),(Resolution,rid)):
            self.assertIsNone(db.session.get(model,key))
        self.assertIsNotNone(db.session.get(Turn,tid));self.assertIsNone(db.session.get(Turn,tid).current_person_id)
        recovery.restore_employee(self.actor,bid);db.session.commit()
        self.assertEqual(db.session.get(Day,did).conversion_id,cid)
        self.assertEqual(db.session.get(Turn,tid).current_person_id,self.pid)

    def signed_record(self):
        raw=b'private fixture signature'
        record=Record(person_id=self.pid,kind='talk_with',body='Signed fixture',context_json='[]',created_by=self.actor.id)
        db.session.add(record);db.session.flush()
        key=f'employee-records/{record.id}/signature.png';self.files.objects[key]=raw
        record.signature_key=key;record.signature_sha256=hashlib.sha256(raw).hexdigest();record.signature_size=len(raw)
        record.finalized_at=datetime.utcnow();record.finalized_by=self.actor.id;record.acknowledgment='signature'
        db.session.commit();return record.id,key,raw

    def test_signature_backup_required_missing_file_rolls_back_and_original_restored_file_survives_expiry(self):
        rid,key,raw=self.signed_record()
        self.files.objects.pop(key)
        with self.assertRaisesRegex(ValueError,'Signature is currently unavailable'):self.delete()
        db.session.rollback();self.assertIsNotNone(db.session.get(StaffingPerson,self.pid));self.assertEqual(Backup.query.count(),0)
        self.files.objects[key]=raw
        backup=self.delete();bid=backup.id;expires=backup.expires_at
        # A lost private object can be recovered from its verified encrypted copy.
        self.files.objects.pop(key)
        recovery.restore_employee(self.actor,bid);db.session.commit()
        self.assertEqual(self.files.objects[key],raw)
        recovery.purge_expired(at=expires);db.session.commit()
        self.assertEqual(self.files.objects[key],raw)
        self.assertIsNotNone(db.session.get(Record,rid));self.assertIsNone(db.session.get(Backup,bid).encrypted_payload)

    def test_duplicate_identity_and_corrupted_backup_fail_without_overwrites(self):
        code=self.person.employee_id;backup=self.delete();bid=backup.id
        peer=self.fixture.peer;peer.employee_id=code;db.session.commit()
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError):recovery.restore_employee(self.actor,bid)
        db.session.rollback();self.assertIsNone(db.session.get(StaffingPerson,self.pid))
        self.assertEqual(db.session.get(StaffingPerson,peer.id).employee_id,code)
        backup=db.session.get(Backup,bid);backup.encrypted_payload=b'tampered';db.session.commit()
        with self.assertRaisesRegex(ValueError,'cannot be verified'):recovery.restore_employee(self.actor,bid)
        db.session.rollback();self.assertIsNone(db.session.get(StaffingPerson,self.pid))

    def test_permissions_csrf_and_both_route_failure_paths(self):
        values=self.confirmation();self.app.config['CSRF_PROTECT_TESTING']=True
        response=self.client.post(f'/neostaffing/app-management/people/{self.pid}/delete',data=values)
        self.assertEqual(response.status_code,400);self.assertIsNotNone(db.session.get(StaffingPerson,self.pid))
        self.app.config['CSRF_PROTECT_TESTING']=False
        with patch.object(recovery,'seal',side_effect=RuntimeError('Do not expose internal storage details')):
            response=self.client.post(f'/neostaffing/app-management/people/{self.pid}/delete',data=values)
        self.assertEqual(response.status_code,400);self.assertNotIn('internal storage',response.json['error'])
        self.assertEqual(Backup.query.count(),0)
        backup=self.delete();bid=backup.id
        access=PortalAppAccess.query.filter_by(user_id=self.actor.id,app_code='neostaffing').one()
        access.role='watcher';db.session.commit()
        with self.assertRaises(ValueError):recovery.restore_employee(self.actor,bid)
        db.session.rollback()
        response=self.client.post(f'/neostaffing/app-management/employee-recovery/{bid}/restore')
        self.assertIn(response.status_code,(302,403));self.assertIsNone(db.session.get(StaffingPerson,self.pid))
