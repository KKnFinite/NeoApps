import json
import copy
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import inspect

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelAuditEntry,
    NeoScorpionFuelingEvent,
    NeoScorpionFuelTankState,
    NeoScorpionFuelWorkState,
    NeoScorpionFuelTruck,
    NeoScorpionSortTruck,
    NeoScorpionSortFueler,
    NeoScorpionSettings,
    NeoScorpionSortAssetState,
    NeoScorpionTailFuelState,
    PortalAppAccess,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services import neoscorpion as fuel
from app.services.neoscorpion import (
    classify_fuel_movement,
    complete_fueled_assignment,
    correct_fuel_actuals,
    mark_fueler_off,
    reopen_fueler_off,
    save_fueler_entry,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from app.services.schema_sync import sync_local_sqlite_schema


class NeoScorpionFuelCorrectionTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-correction-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE": datetime(2026, 8, 17, 22, 0),
                "AUTO_BOOTSTRAP_DATABASE": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.add(NeoScorpionSettings(gateway_id=self.gateway.id))
        self.fueler = self._add_user("correction_fueler", "operator")
        self.dispatcher = self._add_user("correction_dispatcher", "simulator")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_audit_table_bootstrap_and_dispatch_get_are_read_only(self):
        NeoScorpionFuelAuditEntry.__table__.drop(bind=db.engine)
        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)

        columns = {
            column["name"]
            for column in inspect(db.engine).get_columns(
                "neoscorpion_fuel_audit_entries"
            )
        }
        self.assertTrue(
            {
                "sort_date_operation_id",
                "fuel_assignment_id",
                "fuel_work_state_id",
                "action",
                "field_name",
                "old_value",
                "new_value",
                "reason",
                "changed_by_user_id",
                "created_at",
            }.issubset(columns)
        )

        _operation, _mission, assignment = self._assignment()
        self._save_tanks(assignment)
        self._login(self.dispatcher)
        audit_count = NeoScorpionFuelAuditEntry.query.count()
        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.get("/neoscorpion/fuel-dispatch")
            self.assertEqual(commit.call_count, 0)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CORRECT ACTUAL", response.data)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(), audit_count)

    def test_reopen_requires_reason_and_audits_prior_off(self):
        operation, _mission, assignment = self._assignment()
        work = self._ready_off(assignment)
        original_off = work.off_at_utc
        original_off_by = work.off_by_user_id
        starting_revision = self._revision(operation)
        baseline_audits = self._audit_facts()
        visit_id = (work.id, work.visit_number, work.physical_sequence_number)

        with self.assertRaisesRegex(ValueError, "reason is required"):
            reopen_fueler_off(
                self.gateway,
                self.dispatcher,
                assignment.id,
                "",
            )
        db.session.rollback()
        self.assertEqual(self._audit_facts(), baseline_audits)
        self.assertEqual(self._revision(operation), starting_revision)

        result = reopen_fueler_off(
            self.gateway,
            self.dispatcher,
            assignment.id,
            "Fueler declared OFF too early.",
            now_utc=datetime(2026, 8, 18, 5, 15),
        )
        self.assertTrue(result.changed)
        self.assertEqual(result.revision, starting_revision + 1)
        db.session.commit()

        db.session.refresh(work)
        self.assertIsNone(work.off_at_utc)
        self.assertIsNone(work.off_by_user_id)
        audit = NeoScorpionFuelAuditEntry.query.filter_by(action='reopen_off').one()
        self.assertEqual(audit.action, "reopen_off")
        self.assertEqual(audit.field_name, "off")
        self.assertEqual(audit.reason, "Fueler declared OFF too early.")
        self.assertEqual(audit.changed_by_user_id, self.dispatcher.id)
        self.assertIn(original_off.isoformat(), audit.old_value)
        self.assertIn(str(original_off_by), audit.old_value)
        self.assertEqual((audit.sort_date_operation_id,audit.fuel_assignment_id,audit.fuel_work_state_id),
                         (operation.id,assignment.id,work.id))
        self.assertEqual((work.id,work.visit_number,work.physical_sequence_number),visit_id)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertEqual(self._stock(assignment).current_gallons,1000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

        repeated = reopen_fueler_off(
            self.gateway,
            self.dispatcher,
            assignment.id,
            "Repeated click.",
        )
        self.assertFalse(repeated.changed)
        self.assertEqual(repeated.revision, starting_revision + 1)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action='reopen_off').count(), 1)

    def test_reopened_work_can_be_edited_and_marked_off_again(self):
        _operation, _mission, assignment = self._assignment()
        work = self._ready_off(assignment)
        old_off = work.off_at_utc
        identity=(work.id,work.visit_number,work.physical_sequence_number,work.manual_on_at_utc)
        reopen_fueler_off(
            self.gateway,
            self.dispatcher,
            assignment.id,
            "Continue physical fueling.",
        )
        db.session.commit()

        changed = save_fueler_entry(
            self.gateway,
            self.fueler,
            self._fuel_form(assignment, actual_left="12.0"),
        )
        self.assertTrue(changed.changed)
        db.session.commit()
        new_off = datetime(2026, 8, 18, 5, 45)
        mark_fueler_off(
            self.gateway,
            self.fueler,
            assignment.id,
            now_utc=new_off,
        )
        db.session.commit()

        db.session.refresh(work)
        self.assertEqual(work.off_at_utc, new_off)
        self.assertNotEqual(work.off_at_utc, old_off)
        self.assertIn(
            old_off.isoformat(), NeoScorpionFuelAuditEntry.query.filter_by(action='reopen_off').one().old_value
        )
        self.assertEqual((work.id,work.visit_number,work.physical_sequence_number,work.manual_on_at_utc),identity)
        self.assertEqual(work.off_by_user_id,self.fueler.id)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.assertEqual(self._stock(assignment).current_gallons,1000)

    def test_completed_assignments_cannot_reopen_or_be_corrected(self):
        _operation, mission, assignment = self._assignment()
        work=self._ready_off(assignment)
        self._submit(assignment)
        result=complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id,
            now_utc=datetime(2026,8,18,6))
        db.session.commit()
        event=result.fueling_event
        frozen=self._event_facts(event)
        baseline=(self._revision(assignment.sort_date_operation),self._audit_facts(),work.off_at_utc)
        self.assertEqual(self._stock(assignment).current_gallons,850)

        with self.assertRaisesRegex(ValueError, "Completed fuel assignments"):
            reopen_fueler_off(
                self.gateway,
                self.dispatcher,
                assignment.id,
                "Invalid reopen.",
            )
        db.session.rollback()
        with self.assertRaisesRegex(ValueError, "Completed fuel assignments"):
            correct_fuel_actuals(
                self.gateway,
                self.dispatcher,
                self._correction_form(assignment, left="10.0"),
            )
        db.session.rollback()
        self.assertEqual(self._event_facts(event),frozen)
        self.assertEqual((self._revision(assignment.sort_date_operation),self._audit_facts(),work.off_at_utc),baseline)
        self.assertEqual(self._stock(assignment).current_gallons,850)

        # A separate, genuine truckless FOB decision; never reopen a fabricated
        # completed assignment by clearing its completion columns.
        operation,other_mission,other=self._assignment(operation=assignment.sort_date_operation,
            flight_number='UPS1202',tail_number='N413UP')
        fuel.autosave_dispatch_field(self.gateway,other_mission.id,'required_fuel','30',
            expected_value='50',user=self.dispatcher)
        save_fueler_entry(self.gateway,self.fueler,{**self._fuel_form(other,actual_left='10'),
            'transfer_fuel_gallons':''})
        fuel.complete_fuel_on_board(self.gateway,self.dispatcher,other.id)
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "Fuel On Board"):
            reopen_fueler_off(
                self.gateway,
                self.dispatcher,
                other.id,
                "Invalid FOB reopen.",
            )
        db.session.rollback()
        with self.assertRaisesRegex(ValueError,'Fuel On Board'):
            correct_fuel_actuals(self.gateway,self.dispatcher,self._correction_form(other,left='12'))
        db.session.rollback()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(self._event_facts(event),frozen)

    def test_fueler_save_is_frozen_after_off_and_ui_is_read_only(self):
        operation, _mission, assignment = self._assignment()
        work=self._ready_off(assignment)
        revision = self._revision(operation)
        off=(work.off_at_utc,work.off_by_user_id,work.manual_on_at_utc,work.id)
        # The October 16 contract permits final data entry after physical OFF.
        # It does not permit ordinary saves to restart physical work or retain
        # an obsolete Fueler submission after a material change.
        self._login(self.fueler)
        self.assertIn(b'name="actual_left"',self.client.get('/neoscorpion/fueler').data)
        self._submit(assignment)
        changed=save_fueler_entry(self.gateway,self.fueler,self._fuel_form(assignment,actual_left='12'))
        db.session.commit()
        self.assertTrue(changed.changed)
        self.assertFalse(self._row(assignment)['fueler_submitted'])
        self.assertEqual((work.off_at_utc,work.off_by_user_id,work.manual_on_at_utc,work.id),off)
        self.assertGreater(self._revision(operation),revision)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action='reopen_off').count(),0)
        self._submit(assignment)
        complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id);db.session.commit()
        frozen=self._event_facts(NeoScorpionFuelingEvent.query.one())
        with self.assertRaisesRegex(ValueError,'Completed fuel assignments'):
            save_fueler_entry(self.gateway,self.fueler,self._fuel_form(assignment,actual_left='13'))
        db.session.rollback()
        self.assertEqual(self.client.get(f'/neoscorpion/fueler/assignments/{assignment.id}').status_code,404)
        self._login(self.dispatcher)
        response=self.client.get(f'/neoscorpion/fuel-dispatch/fueler-data/{assignment.id}')
        self.assertEqual(response.status_code,200)
        self.assertNotIn('name="actual_left"',response.get_json()['html'])
        self.assertEqual(self._event_facts(NeoScorpionFuelingEvent.query.one()),frozen)
        self.assertEqual(self._stock(assignment).current_gallons,850)

    def test_actual_correction_audits_each_change_once_and_preserves_off(self):
        operation, _mission, assignment = self._assignment()
        work = self._ready_off(
            assignment,
            actual_left="11.0",
            actual_ctr="11.0",
        )
        old_off = work.off_at_utc
        self.assertEqual(
            classify_fuel_movement(
                assignment,
                work,
                tank_states=list(work.tank_states),
            ),
            "moved",
        )
        starting_revision = self._revision(operation)

        result = correct_fuel_actuals(
            self.gateway,
            self.dispatcher,
            self._correction_form(
                assignment,
                left="10.0",
                ctr="10.0",
                right="10.0",
            ),
            now_utc=datetime(2026, 8, 18, 5, 20),
        )
        self.assertTrue(result.changed)
        self.assertEqual(result.revision, starting_revision + 1)
        self.assertEqual(len(result.audit_entries), 2)
        db.session.commit()

        db.session.refresh(work)
        self.assertEqual(work.off_at_utc, old_off)
        self.assertEqual(assignment.transfer_fuel_gallons, 150)
        self.assertEqual(
            classify_fuel_movement(
                assignment,
                work,
                tank_states=list(work.tank_states),
            ),
            "moved",  # Positive T/F still proves movement after gauge correction.
        )
        audits = NeoScorpionFuelAuditEntry.query.filter_by(action='correct_actual').order_by(
            NeoScorpionFuelAuditEntry.field_name
        ).all()
        self.assertEqual(
            [audit.field_name for audit in audits],
            ["actual_ctr", "actual_left"],
        )
        self.assertTrue(all(audit.reason == "Correct gauge entry." for audit in audits))
        self.assertTrue(
            all(audit.changed_by_user_id == self.dispatcher.id for audit in audits)
        )
        tail_state = NeoScorpionTailFuelState.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number="N412UP",
        ).one()
        self.assertEqual(tail_state.actual_fuel_lbs, 30000)
        self.assertEqual([(a.old_value,a.new_value) for a in audits],[('11000','10000'),('11000','10000')])
        self.assertTrue(all(a.fuel_work_state_id==work.id and a.fuel_assignment_id==assignment.id for a in audits))
        facts=self._audit_facts();revision=self._revision(operation)
        retry=correct_fuel_actuals(self.gateway,self.dispatcher,
            self._correction_form(assignment,left='10',ctr='10',right='10'))
        db.session.commit()
        self.assertFalse(retry.changed);self.assertEqual(self._revision(operation),revision)
        self.assertEqual(self._audit_facts(),facts)
        self.assertEqual(self._stock(assignment).current_gallons,1000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_clearing_actual_makes_completion_incomplete_without_clearing_off(self):
        operation, _mission, assignment = self._assignment()
        work = self._ready_off(assignment)
        self._submit(assignment)
        off_at = work.off_at_utc
        result = correct_fuel_actuals(
            self.gateway,
            self.dispatcher,
            self._correction_form(assignment, left=""),
        )
        self.assertTrue(result.changed)
        db.session.commit()

        db.session.refresh(work)
        self.assertEqual(work.off_at_utc, off_at)
        self.assertFalse(self._row(assignment)['fueler_submitted'])
        tail_state = NeoScorpionTailFuelState.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number="N412UP",
        ).one()
        self.assertIsNone(tail_state.actual_fuel_lbs)
        with self.assertRaisesRegex(ValueError, "Complete Actual fuel"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.assertEqual(self._stock(assignment).current_gallons,1000)

    def test_noop_correction_creates_no_audit_or_revision(self):
        operation, _mission, assignment = self._assignment()
        entered_at = datetime(2026, 8, 18, 4, 0)
        saved = save_fueler_entry(self.gateway, self.fueler, self._fuel_form(assignment), now_utc=entered_at)
        db.session.commit()
        starting_revision = self._revision(operation)
        entry = NeoScorpionFuelAuditEntry.query.one()
        expected_values = {"actual_left": 11000, "actual_ctr": 10000, "actual_right": 10000,
                           "remaining_left": 10000, "remaining_ctr": 10000, "remaining_right": 10000,
                           "apu_running": False, "transfer_fuel_gallons": 150}
        self.assertEqual((entry.action, entry.field_name), ("segment_transfer", "fuel_entry"))
        self.assertEqual(entry.sort_date_operation_id, operation.id)
        self.assertEqual(entry.fuel_assignment_id, assignment.id)
        self.assertEqual(entry.fuel_work_state_id, saved.fuel_work_state.id)
        self.assertEqual(assignment.active_work_state_id, saved.fuel_work_state.id)
        self.assertEqual(saved.fuel_work_state.tail_number, "N412UP")
        self.assertEqual(entry.changed_by_user_id, self.fueler.id)
        self.assertEqual(entry.created_at, entered_at)
        self.assertEqual(json.loads(entry.old_value), {key: None for key in expected_values})
        self.assertEqual(json.loads(entry.new_value), expected_values)
        original_entry = (entry.id, entry.old_value, entry.new_value, entry.created_at)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

        result = correct_fuel_actuals(
            self.gateway,
            self.dispatcher,
            self._correction_form(
                assignment,
                left="11.0",
                ctr="10.0",
                right="10.0",
            ),
        )
        self.assertFalse(result.changed)
        self.assertEqual(result.revision, starting_revision)
        self.assertEqual(result.audit_entries, ())
        db.session.commit()
        retry = save_fueler_entry(self.gateway, self.fueler, self._fuel_form(assignment),
                                 now_utc=entered_at + timedelta(minutes=1))
        db.session.commit()
        self.assertFalse(retry.changed)
        self.assertEqual(self._revision(operation), starting_revision)
        retained_entry = NeoScorpionFuelAuditEntry.query.one()
        self.assertEqual((retained_entry.id, retained_entry.old_value, retained_entry.new_value, retained_entry.created_at),
                         original_entry)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action="correct_actual").count(), 0)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

    def test_changed_fuel_entry_audit_records_only_delta_and_duplicate_is_noop(self):
        operation, _mission, assignment = self._assignment()
        entered_at = datetime(2026, 8, 18, 4, 0)
        first = save_fueler_entry(self.gateway, self.fueler, self._fuel_form(assignment), now_utc=entered_at)
        db.session.commit()
        changed_form = self._fuel_form(assignment, actual_left="12.0")
        changed = save_fueler_entry(self.gateway, self.fueler, changed_form,
                                   now_utc=entered_at + timedelta(minutes=1))
        db.session.commit()
        self.assertTrue(changed.changed)
        entries = NeoScorpionFuelAuditEntry.query.order_by(NeoScorpionFuelAuditEntry.id).all()
        self.assertEqual(len(entries), 2)
        delta = entries[1]
        self.assertEqual((delta.action, delta.field_name), ("segment_transfer", "fuel_entry"))
        self.assertEqual(delta.fuel_assignment_id, assignment.id)
        self.assertEqual(delta.fuel_work_state_id, first.fuel_work_state.id)
        self.assertEqual(delta.sort_date_operation_id, operation.id)
        self.assertEqual(delta.changed_by_user_id, self.fueler.id)
        self.assertEqual(delta.created_at, entered_at + timedelta(minutes=1))
        self.assertEqual(json.loads(delta.old_value), {"actual_left": 11000})
        self.assertEqual(json.loads(delta.new_value), {"actual_left": 12000})
        expected_ids = [entry.id for entry in entries]
        revision = self._revision(operation)
        retry = save_fueler_entry(self.gateway, self.fueler, changed_form,
                                 now_utc=entered_at + timedelta(minutes=2))
        db.session.commit()
        self.assertFalse(retry.changed)
        self.assertEqual(self._revision(operation), revision)
        self.assertEqual([entry.id for entry in NeoScorpionFuelAuditEntry.query.order_by(NeoScorpionFuelAuditEntry.id)],
                         expected_ids)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

    def test_dispatcher_routes_commit_each_meaningful_action_once(self):
        _operation, _mission, assignment = self._assignment()
        self._ready_off(assignment)
        self._login(self.dispatcher)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            correction = self.client.post(
                "/neoscorpion/fuel-dispatch/correct-actual",
                data=self._correction_form(assignment, left="10.0"),
                follow_redirects=False,
            )
            self.assertEqual(commit.call_count, 1)
        self.assertEqual(correction.status_code, 302)
        audit_count=NeoScorpionFuelAuditEntry.query.filter_by(action='correct_actual').count()
        with patch.object(db.session,'commit',wraps=db.session.commit) as commit:
            repeat=self.client.post('/neoscorpion/fuel-dispatch/correct-actual',
                data=self._correction_form(assignment,left='10'))
            self.assertEqual(commit.call_count,0)
        self.assertEqual(repeat.status_code,302)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action='correct_actual').count(),audit_count)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            reopen = self.client.post(
                "/neoscorpion/fuel-dispatch/reopen-off",
                data={
                    "assignment_id": str(assignment.id),
                    "reopen_reason": "Return fueler to work.",
                },
                follow_redirects=False,
            )
            self.assertEqual(commit.call_count, 1)
        self.assertEqual(reopen.status_code, 302)
        with patch.object(db.session,'commit',wraps=db.session.commit) as commit:
            repeat=self.client.post('/neoscorpion/fuel-dispatch/reopen-off',
                data=dict(assignment_id=assignment.id,reopen_reason='Duplicate request.'))
            self.assertEqual(commit.call_count,0)
        self.assertEqual(repeat.status_code,302)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action='reopen_off').count(),1)
        self.assertEqual(self._stock(assignment).current_gallons,1000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_stale_correction_and_reopen_have_conflict_responses(self):
        operation,mission,assignment=self._assignment()
        work=self._ready_off(assignment)
        self._login(self.dispatcher)
        before=(self._audit_facts(),self._revision(operation),work.off_at_utc)
        for action in ('correct-actual','reopen-off'):
            with self.subTest(action=action):
                form={**self._correction_form(assignment,left='12'),
                    'expected_work_ref':work.id+999,'reopen_reason':'Stale correction.'}
                response=self.client.post('/neoscorpion/fuel-dispatch/'+action,data=form,
                    headers={'X-Requested-With':'XMLHttpRequest'})
                self.assertEqual(response.status_code,409)
                self.assertEqual((self._audit_facts(),self._revision(operation),work.off_at_utc),before)

    def test_physical_source_parameter_cannot_bypass_current_work_guard(self):
        operation,mission,assignment=self._assignment()
        work=self._ready_off(assignment)
        self._login(self.dispatcher)
        for action in ('correct-actual','reopen-off'):
            with self.subTest(action=action):
                before=(self._audit_facts(),self._revision(operation),work.off_at_utc,
                    fuel._fuel_rollback_values(work.tank_states[0]))
                response=self.client.post('/neoscorpion/fuel-dispatch/'+action,
                    data={**self._correction_form(assignment,left='12'),'reopen_reason':'Stale source.',
                        'physical_work_id':work.id,'physical_sequence':work.physical_sequence_number,
                        'expected_work_ref':work.id+999},headers={'X-Requested-With':'XMLHttpRequest'})
                self.assertEqual(response.status_code,409)
                self.assertEqual((self._audit_facts(),self._revision(operation),work.off_at_utc,
                    fuel._fuel_rollback_values(work.tank_states[0])),before)

    def test_correction_permissions_csrf_and_stale_fueler_baseline(self):
        operation,mission,assignment=self._assignment()
        work=self._ready_off(assignment)
        baseline=fuel.fueler_context(self.gateway,self.fueler,assignment_id=assignment.id)['rows'][0]['edit_baseline']
        facts=self._audit_facts()
        self._login(self.fueler)
        for action in ('correct-actual','reopen-off'):
            response=self.client.post('/neoscorpion/fuel-dispatch/'+action,
                data={**self._correction_form(assignment,left='12'),'reopen_reason':'Not authorized.'})
            self.assertEqual(response.status_code,403)
        self.assertEqual(self._audit_facts(),facts)
        self._login(self.dispatcher)
        self.app.config['CSRF_PROTECT_TESTING']=True
        response=self.client.post('/neoscorpion/fuel-dispatch/correct-actual',
            data=self._correction_form(assignment,left='12'))
        self.assertEqual(response.status_code,400)
        self.assertEqual(self._audit_facts(),facts)
        self.app.config['CSRF_PROTECT_TESTING']=False
        correct_fuel_actuals(self.gateway,self.dispatcher,self._correction_form(assignment,left='12'))
        db.session.commit()
        self._login(self.fueler)
        facts=self._audit_facts();revision=self._revision(operation)
        response=self.client.post(f'/neoscorpion/fueler/assignments/{assignment.id}',
            data={**self._fuel_form(assignment,actual_left='13'),'expected':json.dumps(baseline)},
            headers={'X-Requested-With':'XMLHttpRequest'})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self._audit_facts(),facts);self.assertEqual(self._revision(operation),revision)
        self.assertEqual(work.off_at_utc,datetime(2026,8,18,5))
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_failed_correction_and_reopen_roll_back_all_changes(self):
        operation,mission,assignment=self._assignment()
        work=self._ready_off(assignment);self._submit(assignment)
        for action in ('correct','reopen'):
            with self.subTest(action=action):
                before=copy.deepcopy((self._audit_facts(),self._revision(operation),
                    fuel._fuel_rollback_values(work),[fuel._fuel_rollback_values(t) for t in work.tank_states]))
                def mutate():
                    if action=='correct':
                        return correct_fuel_actuals(self.gateway,self.dispatcher,self._correction_form(assignment,left='12'))
                    return reopen_fueler_off(self.gateway,self.dispatcher,assignment.id,'Resume physical work.')
                with patch.object(fuel,'record_nightly_operational_change',side_effect=RuntimeError('Injected transaction failure')):
                    with self.assertRaisesRegex(RuntimeError,'Injected transaction failure'):mutate()
                db.session.rollback();db.session.expire_all()
                self.assertEqual((self._audit_facts(),self._revision(operation),fuel._fuel_rollback_values(work),
                    [fuel._fuel_rollback_values(t) for t in work.tank_states]),before)
                self.assertTrue(mutate().changed);db.session.commit()
                self.assertEqual(self._stock(assignment).current_gallons,1000)
                self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def _assignment(self, *, operation=None, flight_number='UPS1201', tail_number='N412UP'):
        day = date(2026, 8, 17)
        if operation is None:
            operation = SortDateOperation(
                generated_by_user_id=self.dispatcher.id,
                gateway_id=self.gateway.id,
                sort_date=day,
                gateway_code=self.gateway.code,
                sort_name="night",
                window_minutes=60,
            )
            db.session.add(operation)
            db.session.flush()
        mission = SortDateMission(
            sort_date=day,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date_operation_id=operation.id,
            mission_type="departure",
            mission_source="manual",
            flight_number=flight_number,
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 17, 23, 30),
            planned_datetime_utc=datetime(2026, 8, 18, 4, 30),
            planned_source="manual",
            planned_fuel_load=50000,
            assigned_tail_number=tail_number,
            tail_source="manual",
            fuel_status="waiting",
            departure_status="loading",
        )
        db.session.add(mission)
        db.session.flush()
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            assigned_fueler_user_id=self.fueler.id,
        )
        db.session.add_all(
            [
                assignment,
                SortDateTailState(
                    sort_date=day,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    tail_number=tail_number,
                    aircraft_type="757",
                    aircraft_type_source="derived",
                ),
            ]
        )
        db.session.commit()
        return operation, mission, assignment

    def _save_tanks(
        self,
        assignment,
        *,
        actual_left="11.0",
        actual_ctr="10.0",
        actual_right="10.0",
    ):
        return save_fueler_entry(
            self.gateway,
            self.fueler,
            self._fuel_form(
                assignment,
                actual_left=actual_left,
                actual_ctr=actual_ctr,
                actual_right=actual_right,
            ),
        )

    def _ready_off(self, assignment, **actuals):
        operation = assignment.sort_date_operation
        truck = NeoScorpionFuelTruck(gateway_id=self.gateway.id,
            truck_number=f'CORRECT-{assignment.id}', capacity_gallons=2000)
        db.session.add(truck)
        db.session.flush()
        db.session.add_all([
            NeoScorpionSortTruck(sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
                status='available', starting_gallons=1000, current_gallons=1000),
            NeoScorpionSortFueler(sort_date_operation_id=operation.id,user_id=self.fueler.id),
        ])
        db.session.commit()
        fuel.save_dispatch_assignment(self.gateway,dict(mission_id=assignment.sort_date_mission_id,
            expected_assigned_fueler_user_id=self.fueler.id,expected_assigned_truck_id='',
            assigned_fueler_user_id=self.fueler.id,assigned_truck_id=truck.id))
        db.session.commit()
        fuel.confirm_fueler_truck_on(self.gateway,self.fueler,assignment.id,expected_cycle=1,
            expected_tail=assignment.sort_date_mission.assigned_tail_number,expected_truck_id=truck.id,
            now_utc=datetime(2026,8,18,4))
        db.session.commit()
        save_fueler_entry(self.gateway,self.fueler,self._fuel_form(assignment,**actuals),
            now_utc=datetime(2026,8,18,4,30))
        db.session.commit()
        result = mark_fueler_off(
            self.gateway,
            self.fueler,
            assignment.id,
            now_utc=datetime(2026, 8, 18, 5, 0),
        )
        db.session.commit()
        return result.fuel_work_state

    def _submit(self, assignment):
        row=fuel.fueler_context(self.gateway,self.fueler,assignment_id=assignment.id)['rows'][0]
        result=fuel.submit_fueler_assignment(self.gateway,self.fueler,assignment.id,
            expected=row['edit_baseline'],now_utc=datetime(2026,8,18,5,1))
        db.session.commit()
        return result

    def _row(self, assignment):
        return next(row for row in fuel.fuel_dispatch_context(self.gateway)['rows']
                    if row['assignment'].id==assignment.id)

    def _stock(self, assignment):
        return NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=assignment.sort_date_operation_id,
            fuel_truck_id=assignment.assigned_truck_id).one()

    def _audit_facts(self):
        return [fuel._fuel_rollback_values(a) for a in
                NeoScorpionFuelAuditEntry.query.order_by(NeoScorpionFuelAuditEntry.id).all()]

    def _event_facts(self,event):
        return copy.deepcopy((fuel._fuel_rollback_values(event),
            [fuel._fuel_rollback_values(t) for t in event.tank_snapshots]))

    @staticmethod
    def _fuel_form(
        assignment,
        *,
        actual_left="11.0",
        actual_ctr="10.0",
        actual_right="10.0",
    ):
        return {
            "assignment_id": str(assignment.id),
            "apu_running": "no",
            "remaining_left": "10.0",
            "actual_left": actual_left,
            "remaining_ctr": "10.0",
            "actual_ctr": actual_ctr,
            "remaining_right": "10.0",
            "actual_right": actual_right,
            "transfer_fuel_gallons": "150",
            "notes": "",
        }

    @staticmethod
    def _correction_form(assignment, **values):
        form = {
            "assignment_id": str(assignment.id),
            "correction_reason": "Correct gauge entry.",
        }
        form.update(
            {f"correct_actual_{tank_code}": value for tank_code, value in values.items()}
        )
        return form

    @staticmethod
    def _revision(operation):
        state = NeoScorpionSortAssetState.query.filter_by(
            sort_date_operation_id=operation.id
        ).first()
        return int(state.revision if state else 0)

    def _add_user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name=username.replace("_", " ").title(),
            role="watcher",
            is_active=True,
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=self.gateway.id,
            status="approved",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()
        scorpion = NeoNode.query.filter_by(code="scorpion").one()
        db.session.add_all(
            [
                PortalAppAccess(
                    user_id=user.id,
                    app_code="neogateway",
                    status="approved",
                    role=role,
                    is_active=True,
                ),
                GatewayNodeRole(
                    gateway_membership_id=membership.id,
                    node_id=scorpion.id,
                    role=role,
                    is_active=True,
                ),
            ]
        )
        return user

    def _login(self, user):
        self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
