"""Launch invariants using isolated canonical fixtures, never production."""
from datetime import datetime
import os
import unittest
from app.extensions import db
from app.models import SortDateParkingAssignment
from tests.test_neosubzero_ucc import NeoSubZeroUccTest
from app.services.neosubzero_departure_deice import mutate_departure_deice, NeoSubZeroDepartureDeiceError


class LaunchTest(NeoSubZeroUccTest):
    def queue(self, count):
        missions=[self.alpha]
        for i in range(count-1):
            m=self._departure('UPS-LAUNCH-'+str(i),'N-LAUNCH-'+str(i),'SDF',3)
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=self.operation.id,
                tail_number=m.assigned_tail_number,ramp_code='A',position_code='A0'+str(i+4)))
            missions.append(m)
        for i,m in enumerate(missions):
            m.actual_block_out_datetime_utc=datetime(2026,9,1,1,i)
            self._deice_event(m,'deice_planned')
        db.session.commit()
        return missions

    def test_launch_throat_has_three_places_and_preserves_overflow(self):
        missions=self.queue(5)
        ramp=self._ramp_context('Alpha')
        self.assertEqual(len(ramp['waiting_queue']),2)
        self.assertEqual([r['mission_id'] for r in ramp['exceptions']],[m.id for m in missions[3:]])

    def test_launch_waiting_aircraft_cannot_start_before_position_10(self):
        first,second=self.queue(2)
        with self.assertRaises(NeoSubZeroDepartureDeiceError):
            mutate_departure_deice(self.operation,second,'save_treatment',dict(
                treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='2200'))
        db.session.rollback()


    def test_launch_active_unconfigured_pass_retains_position_10(self):
        first,second=self.queue(2)
        values=dict(treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='2200')
        event=mutate_departure_deice(self.operation,first,'save_treatment',values)
        db.session.commit()
        self.assertIsNotNone(event.pass1_started_at_utc)
        self.assertEqual(self._ramp_context('Alpha')['pad']['mission_id'], first.id)
        self.assertEqual(self._ramp_context('Alpha')['pad']['visual_label'], 'DEICING')
        with self.assertRaises(NeoSubZeroDepartureDeiceError):
            mutate_departure_deice(self.operation,second,'toggle_configured',{})
        db.session.rollback()
        with self.assertRaises(NeoSubZeroDepartureDeiceError):
            mutate_departure_deice(self.operation,second,'save_treatment',values)
        db.session.rollback()

    def test_launch_occupied_parking_ten_is_reviewed_without_duplicate_placement(self):
        self.queue(1)
        parked=self._departure('UPS-PARKED','N-PARKED','DEN',3)
        db.session.add(SortDateParkingAssignment(sort_date_operation_id=self.operation.id,
            tail_number=parked.assigned_tail_number,ramp_code='A',position_code='A10'))
        db.session.commit()
        ramp=self._ramp_context('Alpha')
        self.assertIn(parked.id,[r['mission_id'] for r in ramp['parking_exceptions']])
        self.assertFalse(any(r['mission_id']==parked.id for p in ramp['parking_positions'] for r in p['aircraft']))
        with self.assertRaises(NeoSubZeroDepartureDeiceError):
            mutate_departure_deice(self.operation,self.alpha,'save_treatment',dict(
                treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='2200'))
        db.session.rollback()


@unittest.skipUnless(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL'), 'Requires isolated local PostgreSQL')
class LaunchPostgresTest(unittest.TestCase):
    def setUp(self):
        from tests.test_neosubzero_ucc_postgres import UccPostgresTest
        self.pg=UccPostgresTest()
        self.pg.setUp()
        self.addCleanup(self.pg.doCleanups)

    def prepare(self):
        f=self.pg.fixture
        f.queue=lambda count: LaunchTest.queue(f,count)
        return f

    def test_throat_capacity(self):
        LaunchTest.test_launch_throat_has_three_places_and_preserves_overflow(self.prepare())

    def test_waiting_start_rejected(self):
        LaunchTest.test_launch_waiting_aircraft_cannot_start_before_position_10(self.prepare())

    def test_active_pass_retained(self):
        LaunchTest.test_launch_active_unconfigured_pass_retains_position_10(self.prepare())

    def test_parking_collision_review(self):
        LaunchTest.test_launch_occupied_parking_ten_is_reviewed_without_duplicate_placement(self.prepare())

    def test_simultaneous_starts_admit_only_position_ten(self):
        from app.models import SortDateMission
        from app.services.neosubzero_ucc import NeoSubZeroUccError
        f=self.prepare()
        ids=[mission.id for mission in f.queue(2)]
        def start(index, operation):
            try:
                mutate_departure_deice(operation,db.session.get(SortDateMission,ids[index]),
                    'save_treatment',dict(treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='2200'))
            except NeoSubZeroDepartureDeiceError as error:
                raise NeoSubZeroUccError(str(error)) from error
        self.assertCountEqual(self.pg.race(start),['saved','conflict'])
