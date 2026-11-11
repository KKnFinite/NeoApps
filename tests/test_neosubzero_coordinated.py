"""Registry, shared qualification, separate pad and audited CLEAR correction."""
import copy
import unittest
from datetime import datetime
from unittest.mock import patch

from app.extensions import db
from app.models import (Gateway, NeoSubZeroTruck, NeoSubZeroAuditEntry, NeoSubZeroSprayRecord,
                        NeoSubZeroUccTruckAssignment, StaffingPersonQualification, SortDateParkingAssignment)
from app.services.live_collaboration import entity_version
from app.services.neosubzero_trucks import manage_truck
from app.services.neosubzero_spray import set_neosubzero_ucc_truck
from app.services.neosubzero_staffing import (employee_deice_context, save_employee_deice_training,
    neosubzero_callout_context, set_neosubzero_callout_membership)
from app.services.neosubzero_ucc import (set_neosubzero_ucc_assignment, neosubzero_ucc_context,
    move_ucc_unit, ucc_staffing_version, reorder_ucc_queue)
from tests import test_neosubzero_ucc as ucc
from tests import test_neosubzero_radio as radio
from tests import test_neostaffing_bulk_change as bulk_fixture


class RegistryQualificationTest(unittest.TestCase):
    def setUp(self):
        self.f = ucc.NeoSubZeroUccTest(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.actor = self.f._user('coordinated-manager', 'grandmaster')

    def register(self, number='DZ-101'):
        row = manage_truck(self.f.gateway, 'add_truck', {'truck_number':number}, actor_id=self.actor.id)
        db.session.commit(); return row

    def test_registry_normalization_duplicate_and_gateway_isolation(self):
        row = self.register(' dz-101 ')
        self.assertEqual(row.number, 'DZ-101')
        with self.assertRaisesRegex(ValueError, 'already registered'): self.register('Dz-101')
        db.session.rollback()
        other = Gateway(code='OTH', name='Other'); db.session.add(other); db.session.flush()
        manage_truck(other, 'add_truck', {'truck_number':'DZ-101'}, actor_id=self.actor.id)
        db.session.commit(); self.assertEqual(NeoSubZeroTruck.query.count(), 2)
        self.assertEqual(row.history[0]['actor_id'], self.actor.id)

    def test_deactivation_confirmation_stale_rejection_and_retained_unit_move(self):
        f=self.f; row=self.register()
        set_neosubzero_ucc_truck(f.operation,'Alpha',1,row.number)
        set_neosubzero_ucc_assignment(f.operation,'Alpha',1,'driver',f.permanent)
        db.session.commit()
        version=entity_version(row)
        with self.assertRaisesRegex(ValueError,'Confirm'):
            manage_truck(f.gateway,'deactivate_truck',dict(truck_id=row.id,expected_version=version),actor_id=self.actor.id)
        db.session.rollback()
        manage_truck(f.gateway,'deactivate_truck',dict(truck_id=row.id,expected_version=version,confirm='yes'),actor_id=self.actor.id)
        db.session.commit()
        with self.assertRaisesRegex(ValueError,'active registered'):
            set_neosubzero_ucc_truck(f.operation,'Bravo',2,row.number)
        db.session.rollback()
        move_ucc_unit(f.operation,'Alpha',1,'Bravo',2,expected_version=ucc_staffing_version(f.operation),user_id=self.actor.id)
        db.session.commit()
        self.assertEqual(NeoSubZeroUccTruckAssignment.query.filter_by(truck_number=row.number).one().ramp,'Bravo')
        with self.assertRaisesRegex(ValueError,'changed'):
            manage_truck(f.gateway,'activate_truck',dict(truck_id=row.id,expected_version=version,confirm='yes'),actor_id=self.actor.id)
        db.session.rollback()
        manage_truck(f.gateway,'activate_truck',dict(truck_id=row.id,expected_version=entity_version(row),confirm='yes'),actor_id=self.actor.id)
        db.session.commit(); self.assertTrue(row.active); self.assertEqual(len(row.history),3)

    def test_dropdown_truck_change_preserves_people_and_prevents_duplicate(self):
        f=self.f; self.register(); self.register('DZ-102')
        set_neosubzero_ucc_assignment(f.operation,'Alpha',1,'driver',f.permanent)
        truck=set_neosubzero_ucc_truck(f.operation,'Alpha',1,'DZ-101'); db.session.commit()
        set_neosubzero_ucc_truck(f.operation,'Alpha',1,'dz-102',assignment=truck); db.session.commit()
        self.assertEqual(truck.truck_number,'DZ-102')
        self.assertEqual(f._ramp_context('Alpha')['slots'][0]['roles']['driver']['person'].id,f.permanent.id)
        with self.assertRaisesRegex(ValueError,'already at'):
            set_neosubzero_ucc_truck(f.operation,'Bravo',1,'DZ-102')
        db.session.rollback()
        self.assertEqual(NeoSubZeroUccTruckAssignment.query.count(),1)

    def test_existing_qualification_is_preserved_and_unqualified_is_not_automatic(self):
        f=self.f; self.assertTrue(employee_deice_context(f.permanent)['qualified'])
        person=f._person('NEW-DEICE','New','Employee',f.permanent.work_assignments[0].work_area)
        db.session.commit(); self.assertFalse(employee_deice_context(person)['qualified'])
        save_employee_deice_training(person,'yes',actor_id=self.actor.id,expected_version='')
        db.session.commit(); self.assertTrue(employee_deice_context(person)['qualified'])
        row=StaffingPersonQualification.query.filter_by(person_id=person.id,qualification_key='deice').one()
        self.assertEqual(row.granted_by_user_id,self.actor.id)
        self.assertEqual(StaffingPersonQualification.query.filter_by(person_id=person.id).count(),1)

    def test_shared_qualification_stale_conflict_and_callout_remains_explicit(self):
        f=self.f; area=f._unit('work_area','Ramp',f.night if hasattr(f,'night') else None)
        person=f._person('RAMP-QUAL','Ramp','Employee',area); db.session.commit()
        save_employee_deice_training(person,'yes',actor_id=self.actor.id,expected_version=''); db.session.commit()
        context=neosubzero_callout_context(f.operation)
        self.assertIn(person.id,{r['person'].id for r in context['candidates']})
        self.assertNotIn(person.id,{r['person'].id for r in context['staffing_pool']})
        set_neosubzero_callout_membership(f.operation,person,True,user_id=self.actor.id); db.session.commit()
        self.assertIn(person.id,{r['person'].id for r in neosubzero_callout_context(f.operation)['staffing_pool']})
        with self.assertRaisesRegex(ValueError,'changed'):
            save_employee_deice_training(person,'no',actor_id=self.actor.id,expected_version='')
        db.session.rollback()
        version=employee_deice_context(person)['version']
        save_employee_deice_training(person,'no',actor_id=self.actor.id,expected_version=version); db.session.commit()
        self.assertNotIn(person.id,{r['person'].id for r in neosubzero_callout_context(f.operation)['staffing_pool']})

    def test_settings_and_ducc_get_are_read_only_and_authorized(self):
        f=self.f; self.register(); client=f.app.test_client(); f._login(client,self.actor)
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=f.operation), patch('app.neonodes.neosubzero.routes.neosubzero_weather_context',return_value=f._weather_context_fixture()):
            for path in ('/neosubzero/settings','/neosubzero/ucc','/neosubzero/callouts'):
                response=client.get(path); self.assertEqual(response.status_code,200,path)
            page=client.get('/neosubzero/ucc').data
            self.assertIn(b'<select name="truck_number"',page)
            self.assertNotIn(b'<input name="truck_number"',page)
            self.assertNotIn(b'>EDIT<',page)
            calls=client.get('/neosubzero/callouts').data
            self.assertIn(b'DEICER MANAGEMENT', calls)
            self.assertIn(b'QUALIFIED CALLOUT CANDIDATES', calls)
        self.assertEqual(NeoSubZeroTruck.query.count(),1)
        self.assertEqual(NeoSubZeroAuditEntry.query.count(),0)
        watcher=f._user('registry-watcher','watcher');f._login(client,watcher)
        self.assertEqual(client.post('/neosubzero/settings',data={'action':'add_truck','truck_number':'NO'}).status_code,403)


class PadAndUndoTest(unittest.TestCase):
    def setUp(self):
        self.r=radio.RadioCompletionTest(); self.r.setUp(); self.addCleanup(self.r.doCleanups)

    def test_four_waiters_separate_active_pad_and_no_fifth_waiting_space(self):
        r=self.r; f=r.f
        missions=[f.alpha,r.second]
        for i in range(4):
            mission=f._departure('QUEUE'+str(i),'QUEUE-TAIL'+str(i),'LAX',3)
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=f.operation.id,tail_number=mission.assigned_tail_number,ramp_code='A',position_code='A0'+str(i+5)))
            r.mutate(mission,'initial_contact');mission.actual_block_out_datetime_utc=datetime(2026,9,1,2,i+2)
            missions.append(mission)
        db.session.commit()
        self.assertIsNone(r.ramp()['pad']); self.assertEqual(len(r.ramp()['waiting_queue']),4)
        self.assertEqual(len(r.ramp()['exceptions']),2)
        event=r.finish();self.assertEqual(r.ramp()['pad']['mission_id'],f.alpha.id)
        self.assertEqual([w['queue_label'] for w in r.ramp()['waiting_queue']],['POSITION 10','MIDDLE 1','MIDDLE 2','POSITION 9'])
        self.assertEqual(len(r.ramp()['exceptions']),1)
        self.assertNotIn(f.alpha.id,{w['mission_id'] for w in r.ramp()['waiting_queue']})
        r.mutate(f.alpha,'clear'); self.assertEqual(r.ramp()['pad']['mission_id'],f.alpha.id)
        with self.assertRaisesRegex(ValueError,'occupies'):
            r.mutate(r.second,'save_treatment',treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='22:21')
        db.session.rollback()
        self.assertEqual(r.post(event,'radio_complete').status_code,200)
        self.assertIsNone(r.ramp()['pad']); self.assertEqual(r.ramp()['throat']['mission_id'],r.second.id)

    def test_undo_clear_preserves_passes_participation_pad_and_audited_prior_clear(self):
        r=self.r; event=r.finish(two=True); r.mutate(r.f.alpha,'clear')
        original=copy.deepcopy(event.pass_evidence); stamp=event.cleared_at_utc
        sprays=[(s.id,s.started_at_utc,s.ended_at_utc,s.gallons,s.truck_number_snapshot) for s in NeoSubZeroSprayRecord.query.all()]
        self.assertEqual(r.post(event,'undo_clear',correction_reason='Cleared the wrong aircraft').status_code,200)
        self.assertEqual(event.status,'finished'); self.assertIsNone(event.radio_state)
        self.assertIsNone(event.cleared_at_utc); self.assertEqual(event.pass_evidence,original)
        self.assertEqual(r.ramp()['pad']['mission_id'],r.f.alpha.id)
        self.assertEqual(sprays,[(s.id,s.started_at_utc,s.ended_at_utc,s.gallons,s.truck_number_snapshot) for s in NeoSubZeroSprayRecord.query.all()])
        audit=NeoSubZeroAuditEntry.query.filter_by(action='departure_undo_clear').one()
        self.assertEqual(audit.before_values['cleared_at_utc'],stamp.isoformat())
        self.assertEqual(audit.actor_user_id,r.actor.id);self.assertEqual(audit.reason,'Cleared the wrong aircraft')
        self.assertEqual(r.post(event,'undo_clear',correction_reason='Repeated').status_code,409)
        self.assertEqual(NeoSubZeroAuditEntry.query.filter_by(action='departure_undo_clear').count(),1)

    def test_fourth_waiter_cannot_hide_a_parked_position_nine_conflict(self):
        r=self.r;f=r.f
        for i in range(2):
            mission=f._departure('WAIT'+str(i),'WAIT-NINE'+str(i),'SDF',3)
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=f.operation.id,tail_number=mission.assigned_tail_number,ramp_code='A',position_code='A0'+str(i+5)))
            r.mutate(mission,'initial_contact');mission.actual_block_out_datetime_utc=datetime(2026,9,1,2,i+2)
        parked=f._departure('PARK-NINE','PARK-NINE','SDF',3)
        db.session.add(SortDateParkingAssignment(sort_date_operation_id=f.operation.id,tail_number=parked.assigned_tail_number,ramp_code='A',position_code='A09'));db.session.commit()
        with self.assertRaisesRegex(ValueError,'Position 9 also has a parked aircraft'):
            r.finish()
        db.session.rollback()
        self.assertIsNone(r.ramp()['pad'])
        self.assertEqual(len(r.ramp()['waiting_queue']),4)

    def test_undo_clear_requires_reason_current_identity_and_pending_readback(self):
        r=self.r;event=r.finish();r.mutate(r.f.alpha,'clear')
        for values in ({},{'correction_reason':'reason','expected_version':'stale'}, {'correction_reason':'reason','physical_tail':'FOREIGN'}, {'correction_reason':'reason','operation_id':-1}):
            with self.subTest(values=values):self.assertEqual(r.post(event,'undo_clear',**values).status_code,409)
        self.assertEqual(r.post(event,'radio_complete').status_code,200)
        stamp=event.radio_completed_at_utc
        self.assertEqual(r.post(event,'undo_clear',correction_reason='Too late').status_code,409)
        self.assertEqual(event.radio_completed_at_utc,stamp)
        self.assertEqual(NeoSubZeroAuditEntry.query.filter_by(action='departure_undo_clear').count(),0)


class BulkDeiceQualificationTest(unittest.TestCase):
    def setUp(self):
        self.f=bulk_fixture.NeoStaffingBulkChangeTest();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.area_one.name='Ramp';db.session.commit()

    def stage(self, workspace, person, value):
        from app.services import neostaffing_bulk_change as bulk
        f=self.f
        return f._service_call(bulk.stage_workspace_change,workspace,'stage_person',dict(person_id=str(person.id),change_deice_trained='1',deice_trained=value),f.grandmaster_user)

    def test_bulk_rejects_forged_qualification_changes(self):
        from app.services import neostaffing_bulk_change as bulk
        f=self.f;workspace=bulk.new_workspace(f.grandmaster_user)
        with self.assertRaisesRegex(ValueError,'DEICE TRAINED'):
            self.stage(workspace,f.employee_one,'yes')
        self.assertEqual(StaffingPersonQualification.query.filter_by(person_id=f.employee_one.id).count(),0)

    def test_legacy_bulk_qualification_draft_cannot_apply(self):
        from app.services import neostaffing_bulk_change as bulk
        f=self.f;workspace=bulk.new_workspace(f.grandmaster_user)
        original=f.employee_two.first_name
        f._stage_person_name(workspace,f.employee_two,'Must roll back',f.grandmaster_user)
        workspace["people"][f"p:{f.employee_two.id}"]["changes"]["deice_trained"] = True
        with self.assertRaisesRegex(ValueError,'DEICE TRAINED'):
            f._service_call(bulk.apply_workspace,workspace,f.grandmaster_user)
        db.session.rollback();self.assertEqual(f.employee_two.first_name,original)
        self.assertEqual(StaffingPersonQualification.query.filter_by(person_id=f.employee_two.id).count(),0)
