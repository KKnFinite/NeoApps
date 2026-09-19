from io import BytesIO
import json
import unittest
from PIL import Image, ImageDraw
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.extensions import db
from app.models import PortalAppAccess, StaffingWorkAssignment
from app.services import neostaffing_employee_records as records
from app.models.staffing_accountability import StaffingAccountabilityResolution
from tests import test_neosektor_employees as fixture


class MemorySignatures:
    def __init__(self):
        self.objects = {}

    def put(self, key, value, **metadata):
        if key in self.objects:
            raise ValueError('immutable')
        self.objects[key] = value

    def get(self, key, **options):
        return self.objects[key]


class EmployeeRecordTest(unittest.TestCase):
    setUpBase = fixture.SektorEmployeesTest.setUpBase
    person = fixture.SektorEmployeesTest.person
    _login_approved_user = fixture.SektorEmployeesTest._login_approved_user
    tearDown = fixture.SektorEmployeesTest.tearDown

    def setUp(self):
        fixture.SektorEmployeesTest.setUp(self)
        self.worker = self.workers['ebm']
        db.session.add(PortalAppAccess(user_id=self.user.id, app_code='neostaffing', status='approved', role='master', is_active=True))
        db.session.commit()

    def enable(self):
        records.configure(self.user, self.shift.id, True, 0)
        db.session.commit()

    def draft(self):
        record = records.create(self.user, self.worker.id, 'talk_with', 'Review of the safety procedure.')
        db.session.commit()
        return record

    def test_default_off_inheritance_and_disable_preserves_history_and_draft(self):
        with self.assertRaisesRegex(ValueError, 'OFF'):
            self.draft()
        db.session.rollback()
        self.enable()
        record = self.draft()
        records.configure(self.user, self.shift.id, False, 1)
        db.session.commit()
        self.assertEqual(self.client.get(f'/neostaffing/employee-records/{record.id}').status_code, 200)
        records.edit(self.user, record.id, 1, 'verbal', 'Updated saved draft')
        records.finalize(self.user, record.id, 2, 'yes')
        db.session.commit()
        self.assertEqual(record.acknowledgment_text, records.DELIVERY_CONFIRMATION)
        with self.assertRaisesRegex(ValueError, 'OFF'):
            self.draft()

    def test_operation_on_overrides_department(self):
        self.leadership.unit_id = self.ramp.id
        self.leadership.leadership_level = 'operation'
        self.manager.classification = 'manager'
        db.session.commit()
        records.configure(self.user, self.ramp.id, True, 0)
        db.session.commit()
        self.assertTrue(records.enabled(records.authority(self.user)[0], {self.areas['ebm'].id}))
        with self.assertRaisesRegex(ValueError, 'ON BY OPERATION'):
            records.configure(self.user, self.shift.id, False, 0)

    def test_final_original_and_audit_immutable_addenda_append(self):
        self.enable()
        record = self.draft()
        records.finalize(self.user, record.id, 1, 'yes')
        db.session.commit()
        original = record.body
        with self.assertRaisesRegex(ValueError, 'Finalized'):
            records.edit(self.user, record.id, 2, 'verbal', 'rewrite')
        db.session.rollback()
        records.addendum(self.user, record.id, 'Dated clarification.', 2)
        db.session.commit()
        self.assertEqual(record.body, original)
        self.assertEqual(records.Event.query.filter_by(kind='addendum').count(), 1)
        with self.assertRaisesRegex(ValueError, 'history changed'):
            records.addendum(self.user, record.id, 'replay', 2)
        db.session.rollback()
        for sql in ('UPDATE staffing_employee_records SET body=\'changed\' WHERE id=:id',
                    'DELETE FROM staffing_employee_records WHERE id=:id',
                    'UPDATE staffing_employee_record_events SET body=\'changed\' WHERE record_id=:id',
                    'DELETE FROM staffing_employee_record_events WHERE record_id=:id'):
            with self.assertRaises(DatabaseError):
                db.session.execute(text(sql), {'id': record.id})
            db.session.rollback()

    def test_stale_draft_and_finalization_rejected(self):
        self.enable()
        record = self.draft()
        records.edit(self.user, record.id, 1, 'written_warning', 'Changed content.')
        db.session.commit()
        with self.assertRaisesRegex(ValueError, 'changed'):
            records.finalize(self.user, record.id, 1, 'yes')
        db.session.rollback()
        self.assertIsNone(record.finalized_at)

    def test_missing_version_cannot_bypass_stale_review_protection(self):
        self.enable()
        record=self.draft()
        with self.assertRaisesRegex(ValueError,'version is required'):
            records.edit(self.user,record.id,None,'verbal','No expected version')
        with self.assertRaisesRegex(ValueError,'version is required'):
            records.finalize(self.user,record.id,None,'yes')
        self.assertIsNone(record.finalized_at)

    def test_transfer_author_not_entitled_return_regains_and_grandmaster_history(self):
        self.enable()
        record = self.draft()
        snapshot = record.context_json
        assignment = StaffingWorkAssignment.query.filter_by(person_id=self.worker.id).one()
        assignment.work_area_unit_id = self.outside.id
        db.session.commit()
        with self.assertRaises(ValueError):
            records.access(self.user, self.worker.id)
        self.assertEqual(self.client.get(f'/neostaffing/employee-records/{record.id}').status_code, 403)
        assignment.work_area_unit_id = self.areas['wbm'].id
        db.session.commit()
        records.access(self.user, self.worker.id)
        self.assertEqual(record.context_json, snapshot)
        self.worker.active = False
        db.session.commit()
        with self.assertRaises(ValueError):
            records.access(self.user, self.worker.id)
        self.user.role = 'grandmaster'
        db.session.commit()
        records.access(self.user, self.worker.id)
        with self.assertRaises(ValueError):
            records.edit(self.user, record.id, 1, 'verbal', 'must not write inactive')

    def test_nonmanagement_and_out_of_scope_denied(self):
        self.enable()
        with self.assertRaises(ValueError):
            records.create(self.user, self.outsider.id, 'talk_with', 'Forbidden')
        db.session.rollback()
        self.manager.active = False
        db.session.commit()
        with self.assertRaises(ValueError):
            self.draft()
        self.assertEqual(records.Record.query.count(), 0)

    def test_delivery_without_storage_saves_actor_time_and_no_signature(self):
        self.enable()
        record = self.draft()
        self.assertIsNone(self.app.config.get('EMPLOYEE_RECORD_SIGNATURE_STORAGE'))
        records.finalize(self.user, record.id, 1, 'yes')
        db.session.commit()
        db.session.expire_all()
        self.assertEqual(record.acknowledgment, 'delivered')
        self.assertEqual(record.finalized_by, self.user.id)
        self.assertIsNotNone(record.finalized_at)
        self.assertIsNone(record.signature_key)
        self.assertIsNone(record.signature_sha256)
        self.assertEqual(records.Event.query.filter_by(kind='finalized').one().body, 'Delivered / Discipline Given')

    def test_legacy_signature_history_remains_readable_but_not_an_active_workflow(self):
        from datetime import datetime
        self.enable()
        record = self.draft()
        image = Image.new('RGB', (400, 100), 'white')
        ImageDraw.Draw(image).line([(10, 20), (60, 70), (120, 20)], fill='black', width=4)
        buffer = BytesIO(); image.save(buffer, format='PNG'); raw = buffer.getvalue()
        store = MemorySignatures()
        self.app.config['EMPLOYEE_RECORD_SIGNATURE_STORAGE'] = store
        # Historical row produced by the prior release, not the new write path.
        actor_id = self.user.id
        record.signature_key, record.signature_sha256, record.signature_size = records.storage.store(record.id, raw)
        record.acknowledgment = 'signature'
        record.finalized_by = actor_id
        record.finalized_at = datetime.utcnow()
        db.session.commit()
        self.assertEqual(len(store.objects), 1)
        self.assertTrue(records.storage.read(record).startswith(b'\x89PNG'))
        for row in db.session.execute(text('SELECT * FROM staffing_employee_records')).all():
            self.assertFalse(any(isinstance(value, bytes) or 'base64' in str(value) for value in row))
        self.assertNotIn('PNG', ''.join(event.body for event in records.Event.query.all()))
        response = self.client.get(f'/neostaffing/employee-records/{record.id}/signature')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_discipline_link_preserved_without_progression_mutation(self):
        from datetime import date
        self.enable()
        resolution = StaffingAccountabilityResolution(person_id=self.worker.id, kind='informal', action='Verbal', recommendation='Verbal', actor_id=self.user.id, resolved_on=date.today())
        db.session.add(resolution); db.session.commit()
        record = records.create(self.user, self.worker.id, 'verbal', 'Reviewed.', resolution.id)
        db.session.commit()
        self.assertEqual(json.loads(record.discipline_snapshot_json)['id'], resolution.id)
        records.finalize(self.user, record.id, 1, 'yes')
        db.session.commit()
        self.assertEqual(record.discipline_resolution_id, resolution.id)
        self.assertEqual(StaffingAccountabilityResolution.query.count(), 1)
        resolution.person_id = self.outsider.id
        db.session.commit()
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            records.create(self.user, self.worker.id, 'verbal', 'Forged.', resolution.id)

    def test_routes_draft_edit_delivered_reload_addendum_and_bootstrap_idempotent(self):
        from app.services.schema_sync import _create_missing_application_tables
        from sqlalchemy import inspect
        self.enable()
        response = self.client.post(f'/neostaffing/employee-records/person/{self.worker.id}', data={'kind':'talk_with','body':'Original.'})
        self.assertEqual(response.status_code, 302)
        url = response.location
        response = self.client.post(url, data={'command':'edit','version':'1','kind':'verbal','body':'Revised.'})
        self.assertEqual(response.status_code, 302)
        response = self.client.post(url, data={'command':'finalize','version':'2','delivered':'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertIn(b'ORIGINAL IMMUTABLE', self.client.get(url).data)
        _create_missing_application_tables(set(inspect(db.engine).get_table_names()))
        _create_missing_application_tables(set(inspect(db.engine).get_table_names()))
        self.assertEqual(records.Record.query.count(), 1)

    def test_missing_confirmation_and_legacy_forms_leave_draft(self):
        self.enable()
        record = self.draft()
        for delivered in (None, 'no', 'rts', 'signature'):
            with self.assertRaises(ValueError):
                records.finalize(self.user,record.id,1,delivered)
            db.session.rollback()
            self.assertIsNone(record.finalized_at)
            self.assertEqual(records.Event.query.count(),1)
        url = f'/neostaffing/employee-records/{record.id}'
        for method in ('rts', 'signature'):
            response = self.client.post(url, data={'command':'finalize','version':'1','method':method,'reviewed':'yes','delivered':'yes'})
            self.assertEqual(response.status_code, 409)
            self.assertIsNone(record.finalized_at)
        response = self.client.get(url)
        self.assertIn(b'MARK DELIVERED', response.data)
        for obsolete in (b'<canvas', b'data-signature-pad', b'REFUSE TO SIGN', b'ACKNOWLEDGE &amp;', b'neostaffing_employee_records.js'):
            self.assertNotIn(obsolete, response.data)

    def test_grandmaster_fallback_is_not_active_outside_scope_write_grant(self):
        self.user.role = 'grandmaster'
        db.session.commit()
        with self.assertRaises(ValueError):
            records.access(self.user,self.outsider.id)
        self.outsider.active = False
        db.session.commit()
        records.access(self.user,self.outsider.id)
        with self.assertRaises(ValueError):
            records.access(self.user,self.outsider.id,write=True)

    def test_bootstrap_creates_missing_record_tables(self):
        from sqlalchemy import inspect
        from app.services.schema_sync import _create_missing_application_tables
        for model in (records.Event,records.Record,records.Setting):
            model.__table__.drop(db.engine)
        _create_missing_application_tables(set(inspect(db.engine).get_table_names()))
        self.enable()
        record=self.draft()
        records.finalize(self.user,record.id,1,'yes'); db.session.commit()
        with self.assertRaises(DatabaseError):
            db.session.execute(text('DELETE FROM staffing_employee_records WHERE id=:id'),{'id':record.id})
        db.session.rollback()

    def test_csrf_required_and_record_responses_not_cached(self):
        self.enable()
        self.app.config['CSRF_ENABLED'] = True
        self.app.config['CSRF_PROTECT_TESTING'] = True
        response=self.client.post(f'/neostaffing/employee-records/person/{self.worker.id}',data={'kind':'verbal','body':'No token'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(records.Record.query.count(),0)
        response=self.client.get('/neostaffing/employee-records')
        self.assertEqual(response.status_code,200)
        self.assertIn('no-store',response.headers['Cache-Control'])

    def test_old_constraint_upgrade_preserves_history_and_is_idempotent(self):
        from datetime import datetime
        from sqlalchemy import inspect
        from app.services.schema_sync import _create_missing_application_tables
        constraint = next(c for c in records.Record.__table__.constraints if c.name == 'ck_employee_record_finalization')
        new_sql = constraint.sqltext
        records.Event.__table__.drop(db.engine)
        records.Record.__table__.drop(db.engine)
        try:
            constraint.sqltext = text(str(new_sql).replace("acknowledgment IN ('rts','delivered')", "acknowledgment = 'rts'"))
            records.Record.__table__.create(db.engine)
            records.Event.__table__.create(db.engine)
        finally:
            constraint.sqltext = new_sql
        self.enable()
        legacy = self.draft()
        actor_id = self.user.id
        legacy.acknowledgment = 'rts'
        legacy.acknowledgment_text = 'This information was reviewed with me.'
        legacy.finalized_by = actor_id
        legacy.finalized_at = datetime.utcnow()
        db.session.commit()
        pending = self.draft()
        legacy_id, pending_id = legacy.id, pending.id
        before = (legacy.body, legacy.context_json, legacy.finalized_at, legacy.acknowledgment_text)
        db.session.commit()
        _create_missing_application_tables(set(inspect(db.engine).get_table_names()))
        _create_missing_application_tables(set(inspect(db.engine).get_table_names()))
        db.session.expire_all()
        self.assertEqual(records.Record.query.count(), 2)
        self.assertEqual(records.Event.query.count(), 2)
        self.assertEqual((legacy.body, legacy.context_json, legacy.finalized_at, legacy.acknowledgment_text), before)
        self.assertEqual(legacy.acknowledgment, 'rts')
        records.finalize(self.user, pending_id, 1, 'yes')
        db.session.commit()
        with self.assertRaises(DatabaseError):
            db.session.execute(text('DELETE FROM staffing_employee_records WHERE id=:id'), {'id':legacy_id})
        db.session.rollback()
        records.addendum(self.user, legacy_id, 'Historical context retained.', 1)
        db.session.commit()

    def test_delivery_rechecks_current_scope(self):
        self.enable()
        record = self.draft()
        assignment = StaffingWorkAssignment.query.filter_by(person_id=self.worker.id).one()
        assignment.work_area_unit_id = self.outside.id
        db.session.commit()
        with self.assertRaises(ValueError):
            records.finalize(self.user, record.id, 1, 'yes')
        db.session.rollback()
        self.assertIsNone(record.finalized_at)

    def test_directory_is_paginated_with_constant_query_count(self):
        from sqlalchemy import event
        def reads():
            statements=[]
            def capture(conn,cursor,statement,parameters,context,executemany):
                if statement.lstrip().upper().startswith('SELECT'):
                    statements.append(statement)
            event.listen(db.engine,'before_cursor_execute',capture)
            try:
                page,_,_=records.directory(self.user,'',1)
                count=len(statements)
            finally:
                event.remove(db.engine,'before_cursor_execute',capture)
            return count,page
        small,_=reads()
        for number in range(30):
            self.person(f'RECORD-{number}',area=self.areas['ebm'])
        db.session.commit()
        large,page=reads()
        self.assertEqual(small,large)
        self.assertEqual(len(page.items),25)
        self.assertTrue(page.has_next)

    def test_unassigned_history_and_new_current_scope(self):
        self.enable()
        record=self.draft()
        assignment=StaffingWorkAssignment.query.filter_by(person_id=self.worker.id).one()
        assignment.work_area_unit_id=self.outside.id
        db.session.commit()
        with self.assertRaises(ValueError):
            records.access(self.user,self.worker.id)
        self.leadership.unit_id=self.outside.id
        db.session.commit()
        records.access(self.user,self.worker.id)
        self.assertIn('East Ballmat',record.context_json)
        assignment.active=False
        db.session.commit()
        with self.assertRaises(ValueError):
            records.access(self.user,self.worker.id)
        self.user.role='grandmaster'; db.session.commit()
        records.access(self.user,self.worker.id)
