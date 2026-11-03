"""The eight R03D cases on PostgreSQL, plus atomic posting rollback."""
import unittest
from datetime import timedelta
from threading import Barrier
from app.extensions import db
from app.models import NeoScorpionFuelingEvent, NeoScorpionSortTruck
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_dispatcher_complete as cases
from tests import test_motherbrain_block_out_authority_postgres as pg
from tests import test_neoscorpion_lifecycle_postgres as lifecycle


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class DispatcherCompletionPostgresTest(unittest.TestCase):
    def run_case(self,name):
        case=cases.NeoScorpionDispatcherCompleteTest(name)
        def exercise():
            self.addCleanup(case.doCleanups)
            case.setUp();self.addCleanup(case.tearDown)
            getattr(case,name)()
            self.assertEqual(db.engine.dialect.name,'postgresql')
        pg.postgres_setup(self,cases,exercise)

    def test_active_fueler_filter_and_read_only_dispatch_render(self):
        self.run_case('test_active_fueler_filter_and_read_only_dispatch_render')

    def test_existing_event_and_fuel_on_board_block_normal_complete(self):
        self.run_case('test_existing_event_and_fuel_on_board_block_normal_complete')

    def test_moved_completion_validates_truck_tf_and_gallons(self):
        self.run_case('test_moved_completion_validates_truck_tf_and_gallons')

    def test_moved_success_creates_event_deducts_once_and_preserves_master(self):
        self.run_case('test_moved_success_creates_event_deducts_once_and_preserves_master')

    def test_not_moved_completes_without_event_or_truck_deduction(self):
        self.run_case('test_not_moved_completes_without_event_or_truck_deduction')

    def test_stale_cross_sort_attempt_and_route_permission_do_not_complete(self):
        self.run_case('test_stale_cross_sort_attempt_and_route_permission_do_not_complete')

    def test_topping_off_and_oos_nightly_trucks_may_complete_moved_work(self):
        self.run_case('test_topping_off_and_oos_nightly_trucks_may_complete_moved_work')

    def test_unknown_movement_blocks_completion(self):
        self.run_case('test_unknown_movement_blocks_completion')

    def test_atomic_rollback_and_retry(self):
        self.run_case('test_r03d_completion_failure_rolls_back_and_preserves_handoff')

    def test_complete_route_commits_once(self):
        self.run_case('test_complete_route_commits_once')

    def test_concurrent_dispatcher_completions_deduct_inventory_once(self):
        case = lifecycle.LifecyclePostgresTest(
            'test_concurrent_dispatcher_verification_posts_once_after_submission')
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.save(case.start + timedelta(minutes=10), **case.final_data())
        case.off()
        case.submit()
        selection_id, before = db.session.query(
            NeoScorpionSortTruck.id, NeoScorpionSortTruck.current_gallons).filter_by(
                sort_date_operation_id=case.operation.id,
                fuel_truck_id=case.truck.id).one()
        assignment_id = case.ids[3]
        simultaneous = Barrier(2)

        def complete(index, gateway, fueler, dispatcher):
            simultaneous.wait(timeout=5)
            return fuel.complete_fueled_assignment(
                gateway, dispatcher, assignment_id).changed

        self.assertEqual(sorted(case.concurrent(complete)), [False, True])
        db.session.expire_all()
        event = NeoScorpionFuelingEvent.query.one()
        self.assertEqual(event.fuel_assignment_id, assignment_id)
        self.assertGreater(event.transfer_fuel_gallons, 0)
        self.assertEqual(db.session.get(NeoScorpionSortTruck, selection_id).current_gallons,
                         before - event.transfer_fuel_gallons)
        self.assertEqual(db.engine.dialect.name, 'postgresql')
