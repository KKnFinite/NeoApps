"""Mission-scoped Scorpion progress is a display, not a new fuel authority."""
import unittest
from datetime import timedelta
from app.extensions import db
from app.models import NeoScorpionFuelingEvent, NeoScorpionFuelWorkState
from app.services import neoscorpion as fuel
from app.neonodes.neorain.services import neorain_outbound_context, neorain_outbound_revision
from app.services.neorain_fuel_authority import record_google_rain_fuel_value
from tests import test_neoscorpion_resume as fixtures


class RainBoardFuelTest(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.ResumeTest(); self.case.setUp(); self.addCleanup(self.case.doCleanups)

    def row(self, mission=None):
        return next(r for r in neorain_outbound_context(self.case.gateway,operation=self.case.operation)['rows']
                    if r['mission_id']==(mission or self.case.mission).id)

    def test_current_status_then_verified_quantity_without_changing_center_or_source(self):
        c=self.case
        record_google_rain_fuel_value(c.operation,c.mission,'12000','1400');db.session.commit()
        self.assertEqual(self.row()['scorpion_fuel_display'],'READY')
        c.assignment.assigned_fueler_user_id=c.fueler.id
        c.assignment.assigned_truck_id=c.fixture._nightly_truck('BEFORE').id;db.session.commit()
        self.assertEqual(self.row()['scorpion_fuel_display'],'ASSIGNED')
        c.prepare();self.assertEqual(self.row()['scorpion_fuel_display'],'FUELING')
        c.save(c.start+timedelta(minutes=1),**c.final_data())
        self.assertEqual(self.row()['scorpion_fuel_display'],'FUELING')
        c.off();self.assertEqual(self.row()['scorpion_fuel_display'],'OFF')
        c.submit();self.assertFalse(self.row()['scorpion_fuel_final'])
        fuel.complete_fueled_assignment(c.gateway,c.dispatcher,c.assignment.id);db.session.commit()
        row=self.row();self.assertTrue(row['scorpion_fuel_final']);self.assertEqual(row['scorpion_fuel_display'],'30.0')
        self.assertEqual(row['center_fuel_display'],'1.4');self.assertEqual(row['neo_fuel_display'],'12.0')

    def test_same_tail_other_mission_and_replacement_never_get_completed_quantity(self):
        c=self.case;c.prepare();c.save(c.start+timedelta(minutes=1),**c.final_data());c.off();c.submit()
        fuel.complete_fueled_assignment(c.gateway,c.dispatcher,c.assignment.id);db.session.commit()
        other=c.fixture._mission('UPS990','N412UP',25000,5);other.assigned_tail_number=c.mission.assigned_tail_number
        db.session.commit()
        self.assertFalse(self.row(other)['scorpion_fuel_final']);self.assertNotEqual(self.row(other)['scorpion_fuel_display'],'30.0')
        c.mission.assigned_tail_number='N492UP';db.session.commit()
        self.assertFalse(self.row()['scorpion_fuel_final'])
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_work_and_reading_changes_advance_revision_without_poll_writes(self):
        c=self.case;c.prepare()
        original=neorain_outbound_revision(c.gateway,operation=c.operation)
        c.save(c.start+timedelta(minutes=1),remaining_left='6')
        changed=neorain_outbound_revision(c.gateway,operation=c.operation)
        self.assertNotEqual(original,changed)
        self.assertEqual(self.row()['scorpion_fuel_display'],'FUELING')
        db.session.flush();self.assertFalse(db.session.new);self.assertFalse(db.session.dirty)
        self.assertEqual(changed,neorain_outbound_revision(c.gateway,operation=c.operation))
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)

    def test_fob_ready_is_status_until_dispatcher_verifies_measured_no_truck_fuel(self):
        c=self.case;c.assignment.assigned_fueler_user_id=c.fueler.id;db.session.commit()
        fuel.save_fueler_entry(c.gateway,c.fueler,dict(assignment_id=c.assignment.id,apu_running='no',
            remaining_left='15',remaining_ctr='0',remaining_right='15',
            actual_left='15',actual_ctr='0',actual_right='15',transfer_fuel_gallons='0'));db.session.commit()
        self.assertEqual(self.row()['scorpion_fuel_display'],'FOB READY');self.assertFalse(self.row()['scorpion_fuel_final'])
        fuel.complete_fuel_on_board(c.gateway,c.dispatcher,c.assignment.id);db.session.commit()
        self.assertEqual(self.row()['scorpion_fuel_display'],'30.0');self.assertTrue(self.row()['scorpion_fuel_final'])
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_pending_continuation_does_not_publish_previous_visit_as_final(self):
        from app.services.neoscorpion_pending_uplift import plan_continuation
        c=self.case;c.prepare()
        fuel.autosave_dispatch_field(c.gateway,c.mission.id,'required_fuel','40',expected_value='25.0',user=c.dispatcher);db.session.commit()
        truck=c.fixture._nightly_truck('NEXT');db.session.commit()
        plan_continuation(c.gateway,c.dispatcher,dict(assignment_id=c.assignment.id,expected_cycle=1,
            expected_tail=c.mission.assigned_tail_number,expected_work_ref=c.work.id,proposed_truck_id=truck.id));db.session.commit()
        c.save(c.start+timedelta(minutes=1),**c.final_data());c.off();c.submit()
        fuel.complete_fueled_assignment(c.gateway,c.dispatcher,c.assignment.id,
            physical_work_id=c.work.id,physical_sequence=c.work.physical_sequence_number);db.session.commit()
        self.assertEqual(self.row()['scorpion_fuel_status'],'assigned');self.assertFalse(self.row()['scorpion_fuel_final'])


class RainBoardPostgresTest(unittest.TestCase):
    def test_real_postgres_progress_snapshot_and_readonly_identity(self):
        from tests import test_neoscorpion_segments_postgres as pg
        if not pg.URI:self.skipTest('Requires disposable local PostgreSQL')
        c=pg.PhysicalSegmentPostgresTest();c.setUp();self.addCleanup(c.doCleanups)
        rows=lambda:neorain_outbound_context(c.gateway,operation=c.operation)['rows']
        self.assertEqual(rows()[0]['scorpion_fuel_display'],'FUELING')
        c.save(c.start+timedelta(minutes=1),**c.final_data());c.off();c.submit_current()
        self.assertEqual(rows()[0]['scorpion_fuel_display'],'OFF')
        fuel.complete_fueled_assignment(c.gateway,c.dispatcher,c.assignment.id);db.session.commit()
        self.assertEqual(rows()[0]['scorpion_fuel_display'],'30.0')
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertFalse(db.session.new);self.assertFalse(db.session.dirty)
