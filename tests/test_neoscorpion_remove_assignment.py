"""Explicit personnel/truck removal retains the mission and preparation evidence."""
import json
import unittest

from app.extensions import db
from app.models import (NeoScorpionFuelAssignment, NeoScorpionFuelWorkState,
    NeoScorpionFuelAuditEntry, NeoScorpionFuelingEvent, NeoScorpionTruckInventoryAudit,
    SortDateTailState)
from app.services import neoscorpion as service
from tests import test_neoscorpion_uplift_defuel as fixtures


class RemoveAssignmentTest(unittest.TestCase):
    setUp = fixtures.NeoScorpionUpliftDefuelTest.setUp
    tearDown = fixtures.NeoScorpionUpliftDefuelTest.tearDown
    _add_user = fixtures.NeoScorpionUpliftDefuelTest._add_user
    _operation = fixtures.NeoScorpionUpliftDefuelTest._operation
    _assignment = fixtures.NeoScorpionUpliftDefuelTest._assignment
    _truck = fixtures.NeoScorpionUpliftDefuelTest._truck
    _save_cycle = fixtures.NeoScorpionUpliftDefuelTest._save_cycle
    _complete_cycle = fixtures.NeoScorpionUpliftDefuelTest._complete_cycle
    _on = fixtures.NeoScorpionUpliftDefuelTest._on
    _submit = fixtures.NeoScorpionUpliftDefuelTest._submit
    _login = fixtures.NeoScorpionUpliftDefuelTest._login

    def prepare(self):
        self.operation = self._operation()
        self.mission, self.assignment = self._assignment(self.operation)
        self.truck, self.selection = self._truck(self.operation, self.assignment, 'REMOVE-1', 500)
        self._save_cycle(self.assignment, remaining=(10,10,10), actual=(10,10,10), transfer=0)
        db.session.commit()
        self.work = NeoScorpionFuelWorkState.query.one()

    def form(self):
        a = self.assignment
        return dict(assignment_id=str(a.id), expected_cycle=str(a.current_cycle_number),
            expected_tail=self.mission.assigned_tail_number,
            expected_work_ref=str(a.active_work_state_id or ''),
            expected_assigned_fueler_user_id=str(a.assigned_fueler_user_id or ''),
            expected_assigned_truck_id=str(a.assigned_truck_id or ''),
            expected_assignment_version=a.updated_at.isoformat(),
            confirm_remove='yes', reason='Original aircraft is OOS; dispatch will reassign replacement.')

    def remove(self, form=None):
        return service.remove_fuel_assignment(self.gateway, self.dispatcher,
            self.assignment.id, expected=form or self.form())

    def test_remove_retains_preparation_and_revokes_live_card_save_and_submit(self):
        self.prepare()
        before = service._fuel_rollback_values(self.work)
        tanks = [service._fuel_rollback_values(t) for t in self.work.tank_states]
        baseline = service.fueler_context(self.gateway,self.fueler)['rows'][0]['edit_baseline']
        form = self.form()
        self.assertTrue(self.remove(form).changed); db.session.commit()
        self.assertIsNone(self.assignment.assigned_fueler_user_id)
        self.assertIsNone(self.assignment.assigned_truck_id)
        self.assertEqual(service.fueler_context(self.gateway,self.fueler)['rows'], [])
        self.assertEqual(service._fuel_rollback_values(self.work), before)
        self.assertEqual([service._fuel_rollback_values(t) for t in self.work.tank_states], tanks)
        self.assertEqual(NeoScorpionFuelAssignment.query.count(), 1)
        self.assertEqual(self.selection.current_gallons, 500)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)
        self.assertEqual(NeoScorpionTruckInventoryAudit.query.count(), 0)
        audit = NeoScorpionFuelAuditEntry.query.filter_by(field_name='resource_assignments').one()
        self.assertEqual(audit.changed_by_user_id, self.dispatcher.id)
        self.assertEqual(audit.reason, form['reason']); self.assertIsNotNone(audit.created_at)
        self.assertIn(str(self.truck.id), audit.old_value)
        with self.assertRaises(ValueError): self.remove(form)
        db.session.rollback()
        for source in ({}, dict(physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number)):
            with self.assertRaises(ValueError):
                service.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
                    expected=json.dumps(baseline), notes='stale overwrite', **source))
            db.session.rollback()
        with self.assertRaises(ValueError):
            service.submit_fueler_assignment(self.gateway,self.fueler,self.assignment.id,expected=baseline)
        db.session.rollback()
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(field_name='resource_assignments').count(),1)

    def test_oos_replacement_keeps_tail_and_mission_and_removes_old_preparation(self):
        self.prepare()
        from app.services.parking_plan import set_tail_out_of_service
        set_tail_out_of_service(self.operation,'N412UP',True,user=self.dispatcher)
        self.mission.assigned_tail_number='N919UP'
        db.session.commit()
        mission_before=service._fuel_rollback_values(self.mission)
        old_work=service._fuel_rollback_values(self.work)
        self.assertTrue(service.fueler_context(self.gateway,self.fueler)['rows'])
        self.remove(); db.session.commit()
        self.assertEqual(service._fuel_rollback_values(self.mission),mission_before)
        self.assertEqual(service._fuel_rollback_values(self.work),old_work)
        self.assertTrue(SortDateTailState.query.filter_by(tail_number='N412UP').one().is_out_of_service)
        self.assertEqual(service.fueler_context(self.gateway,self.fueler)['rows'],[])
        with self.assertRaises(ValueError):
            service.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
                physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number,notes='old tail'))
        db.session.rollback()

    def test_on_off_submission_and_completed_work_block_removal(self):
        self.prepare(); self._on(self.assignment)
        for stage in ('on','off','submitted','completed'):
            if stage=='off': service.mark_fueler_off(self.gateway,self.fueler,self.assignment.id);db.session.commit()
            if stage=='submitted':
                self.mission.planned_fuel_load=30000;db.session.commit();self._submit(self.assignment)
            if stage=='completed':
                service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
            with self.subTest(stage=stage), self.assertRaises(ValueError): self.remove()
            db.session.rollback()
            self.assertEqual(self.assignment.assigned_fueler_user_id,self.fueler.id)
            self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(field_name='resource_assignments').count(),0)

    def test_confirmation_reason_and_exact_resource_identity_are_required(self):
        self.prepare()
        original=self.form()
        for key,value in (('confirm_remove',''),('reason',''),('expected_assignment_version','old'),
                          ('expected_work_ref','999'),('expected_assigned_fueler_user_id','999'),
                          ('expected_tail','N999UP'),('expected_cycle','9'),('expected_assigned_truck_id','999')):
            with self.subTest(key=key),self.assertRaises(ValueError): self.remove({**original,key:value})
            db.session.rollback()
        self.assertEqual(self.assignment.assigned_truck_id,self.truck.id)

    def test_explicit_reassignment_cannot_restore_old_fueler_source_access(self):
        self.prepare()
        self.mission.assigned_tail_number='N919UP';db.session.commit()
        self.remove();db.session.commit()
        service.save_dispatch_assignment(self.gateway,dict(mission_id=self.mission.id,
            assigned_fueler_user_id=self.other_fueler.id,assigned_truck_id=self.truck.id,
            expected_assigned_fueler_user_id='',expected_assigned_truck_id='',
            expected_work_ref=self.assignment.active_work_state_id))
        db.session.commit()
        self.assertEqual(service.fueler_context(self.gateway,self.fueler)['rows'],[])
        with self.assertRaises(ValueError):
            service.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
                physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number,notes='revoked'))
        db.session.rollback()

    def test_failure_rolls_back_resource_audit_and_revision(self):
        from unittest.mock import patch
        self.prepare();before=service._fuel_rollback_values(self.assignment)
        with patch('app.services.neoscorpion_spear_decisions.observe',side_effect=RuntimeError('injected')):
            with self.assertRaisesRegex(RuntimeError,'injected'):self.remove()
        db.session.rollback()
        self.assertEqual(service._fuel_rollback_values(self.assignment),before)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(field_name='resource_assignments').count(),0)

    def test_route_permissions_csrf_conflict_and_read_only_get(self):
        self.prepare(); self._login(self.fueler)
        url='/neoscorpion/fuel-dispatch/remove-assignment';headers={'Accept':'application/json'}
        self.assertEqual(self.client.post(url,data=self.form(),headers=headers).status_code,403)
        self._login(self.dispatcher)
        self.app.config.update(CSRF_ENABLED=True,CSRF_PROTECT_TESTING=True)
        self.assertEqual(self.client.post(url,data=self.form(),headers=headers).status_code,400)
        page=self.client.get('/neoscorpion/fuel-dispatch')
        self.assertEqual(page.status_code,200)
        from html.parser import HTMLParser
        class Inputs(HTMLParser):
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if attrs.get('name')=='csrf_token':self.token=attrs['value']
        inputs=Inputs();inputs.feed(page.get_data(as_text=True))
        form=self.form();headers['X-CSRFToken']=inputs.token
        self.assertEqual(self.client.post(url,data=form,headers=headers).status_code,200)
        self.assertEqual(self.client.post(url,data=form,headers=headers).status_code,409)
        self.assertEqual(self.client.get(url).status_code,405)
