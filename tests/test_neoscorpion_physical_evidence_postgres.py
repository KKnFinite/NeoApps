"""R03B physical facts and cancellation serialized by real PostgreSQL locks."""
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from sqlalchemy import text
from app.extensions import db
from app.models import Gateway, User, NeoScorpionFuelAssignment, NeoScorpionFuelWorkState, NeoScorpionTailFuelState
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_physical_evidence as cases
from tests import test_neoscorpion_dispatch_planning as fixtures
from tests import test_motherbrain_block_out_authority_postgres as pg
from tests import test_neoscorpion_fueling_status as status


@unittest.skipUnless(pg.URI, 'Requires disposable loopback PostgreSQL')
class PhysicalEvidencePostgresTest(cases.PhysicalEvidenceTest):
    _off_physical_transfer = status.FuelingStatusIntegrationTest._off_physical_transfer
    test_insufficient_inventory = status.FuelingStatusIntegrationTest.test_off_completion_accepts_insufficient_truck_reading_without_negative_inventory
    test_unknown_inventory = status.FuelingStatusIntegrationTest.test_unknown_truck_inventory_does_not_block_valid_aircraft_completion
    test_inventory_rollback = status.FuelingStatusIntegrationTest.test_inventory_discrepancy_completion_rolls_back_with_event_failure
    test_repeated_inventory = status.FuelingStatusIntegrationTest.test_repeated_physical_jobs_on_unverified_truck_keep_all_events_without_balance_fiction

    def setUp(self):
        pg.postgres_setup(self, fixtures, super().setUp)

    def test_simultaneous_cancel_and_fuel_save_preserve_winning_facts(self):
        self.prepare(); self.finish(); work = self.start_followup(); self.on_current()
        identity = self.gateway.id,self.fueler.id,self.dispatcher.id,self.assignment.id,work.id
        expected = self.baseline()
        app = self.fixture.app
        barrier = Barrier(2)
        db.session.remove()
        def action(index):
            with app.app_context():
                db.session.execute(text("SET LOCAL lock_timeout='8s'"))
                gateway=db.session.get(Gateway,identity[0])
                actor=db.session.get(User,identity[1 if index else 2])
                barrier.wait(timeout=5)
                try:
                    if index:
                        result=fuel.save_fueler_entry(gateway,actor,dict(assignment_id=identity[3],
                            apu_running='no',actual_left='16',actual_ctr='0',actual_right='15',
                            transfer_fuel_gallons='150',expected=json.dumps(expected)),require_expected=True)
                    else:
                        result=fuel.cancel_uplift(gateway,actor,identity[3],expected_cycle=2,expected_tail='N491UP')
                    db.session.commit()
                    return 'saved' if index else 'cancelled'
                except ValueError:
                    db.session.rollback()
                    return 'rejected'
                finally:
                    db.session.remove()
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(action,(0,1)))
        self.assertEqual(results.count('rejected'),1,results)
        assignment=db.session.get(NeoScorpionFuelAssignment,identity[3])
        work=db.session.get(NeoScorpionFuelWorkState,identity[4])
        if 'saved' in results:
            self.assertEqual((assignment.current_cycle_number,work.physical_transfer_gallons),(2,150))
            self.assertIsNone(work.physical_finalized_at_utc)
        else:
            self.assertEqual(assignment.current_cycle_number,1)
            self.assertIsNone(work.physical_transfer_gallons)
            self.assertIsNotNone(work.physical_finalized_at_utc)

    def test_cancellation_waits_for_other_mission_save_and_preserves_it(self):
        self.prepare(); self.finish(); self.start_followup()
        _,other,work=self.replacement()
        other.assigned_fueler_user_id=self.fueler.id
        db.session.commit()
        gid,did,aid=self.gateway.id,self.dispatcher.id,self.assignment.id
        app=self.fixture.app
        pid=[];started=Event()
        def cancel():
            with app.app_context():
                try:
                    pid.append(db.session.execute(text('SELECT pg_backend_pid()')).scalar_one())
                    gateway=db.session.get(Gateway,gid);actor=db.session.get(User,did)
                    started.set()
                    try:
                        fuel.cancel_uplift(gateway,actor,aid,expected_cycle=2,expected_tail='N491UP')
                    except ValueError as exc:
                        db.session.rollback()
                        return str(exc)
                    db.session.commit()
                    return 'unexpected cancellation'
                finally:
                    db.session.remove()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=other.id,
            remaining_left='15',remaining_ctr='0',remaining_right='15',
            actual_left='14',actual_ctr='0',actual_right='14',apu_running='no'))
        db.session.flush()  # Canonical save holds the operation lock, not committed yet.
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(cancel)
            try:
                self.assertTrue(started.wait(timeout=5))
                deadline=time.monotonic()+5
                waiting=False
                while time.monotonic()<deadline:
                    with self.admin.connect() as connection:
                        waiting=connection.execute(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"),{'pid':pid[0]}).scalar()
                    if waiting: break
                    time.sleep(.02)
                self.assertTrue(waiting,'Cancellation did not wait on the canonical save')
            finally:
                db.session.commit()
            self.assertIn('another mission updated',future.result(timeout=8))
        tail=NeoScorpionTailFuelState.query.filter_by(sort_date_operation_id=self.operation.id,tail_number='N491UP').one()
        self.assertEqual(tail.actual_fuel_lbs,28000)
        self.assertEqual(self.assignment.current_cycle_number,2)
        self.assertEqual(sum(t.actual_lbs for t in work.tank_states),28000)
