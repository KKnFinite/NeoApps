"""Resource snapshots and additive bootstrap on disposable PostgreSQL only."""
import copy
import unittest
from datetime import timedelta
from unittest.mock import patch
from sqlalchemy import text,inspect
from app.extensions import db
from app.models import NeoScorpionFuelingEvent,NeoScorpionSortTruck,SortDateParkingAssignment
from app.services import neoscorpion as service
from app.services.neoscorpion_prof import event_math,load_observation_jobs
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_neoscorpion_segments_postgres as segments
from tests import test_neoscorpion_resource_prof_integration as integration


@unittest.skipUnless(segments.URI,'Requires disposable local PostgreSQL')
class ResourceProfPostgresTest(unittest.TestCase):
    setUp=segments.PhysicalSegmentPostgresTest.setUp
    drop_schema=segments.PhysicalSegmentPostgresTest.drop_schema
    def prepare(self,lead=15):
        if not hasattr(self,'work'):
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=self.operation.id,
                tail_number=self.mission.assigned_tail_number,ramp_code='B',position_code='B06',lane_number=1))
            db.session.commit()
        segments.PhysicalSegmentPostgresTest.prepare(self,lead)
    concurrent=segments.PhysicalSegmentPostgresTest.concurrent
    save=segments.PhysicalSegmentPostgresTest.save
    off=segments.PhysicalSegmentPostgresTest.off
    final_data=segments.PhysicalSegmentPostgresTest.final_data
    upcoming=integration.ResourceProfIntegrationTest.upcoming
    finish_first=integration.ResourceProfIntegrationTest.finish_first
    test_postgres_early_prep_snapshot_report_and_exact_once_posting=integration.ResourceProfIntegrationTest.test_early_preparation_while_truck_busy_freezes_v4_and_keeps_physical_workflow
    test_postgres_v3_frozen_history=integration.ResourceProfIntegrationTest.test_v3_active_and_completed_snapshots_do_not_migrate
    test_postgres_repeated_tail_history=integration.ResourceProfIntegrationTest.test_repeated_tail_visits_keep_resource_evidence_and_inventory_independent

    def test_concurrent_complete_freezes_one_v4_snapshot_and_one_inventory_post(self):
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        assignment_id=self.assignment.id
        result=self.concurrent(lambda index,gateway,fueler,dispatcher:
            service.complete_fueled_assignment(gateway,dispatcher,assignment_id).changed)
        self.assertEqual(sorted(result),[False,True])
        event=NeoScorpionFuelingEvent.query.one()
        self.assertEqual(event.performance_snapshot['version'],4)
        self.assertEqual(event.performance_snapshot['resource_timing']['work_id'],event.fuel_work_state_id)
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,7000)
        original=copy.deepcopy(event.performance_snapshot)
        db.session.expire_all()
        self.assertEqual(NeoScorpionFuelingEvent.query.one().performance_snapshot,original)
        self.assertEqual(event_math(event)['fueler_prof'],load_observation_jobs(self.ids[0],operation_id=event.sort_date_operation_id)[0]['fueler_prof'])

    def test_availability_columns_additive_repeat_bootstrap_preserves_v3_and_inventory(self):
        old={**self.work.performance_context,'version':3};old.pop('resource_timing',None)
        self.work.performance_context=old
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        event=NeoScorpionFuelingEvent.query.one();original=copy.deepcopy(event.performance_snapshot)
        identity=(event.id,event.fuel_work_state_id,event.transfer_fuel_gallons,event.started_at_utc,event.ended_at_utc)
        db.session.execute(text('ALTER TABLE neoscorpion_sort_trucks DROP COLUMN availability_history'))
        db.session.execute(text('ALTER TABLE neoscorpion_fuel_trucks DROP COLUMN availability_history'));db.session.commit()
        for _ in range(2):
            with patch.dict(self.fixture.app.config,{'TESTING':False}):ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all()
        event=NeoScorpionFuelingEvent.query.one()
        self.assertEqual((event.id,event.fuel_work_state_id,event.transfer_fuel_gallons,event.started_at_utc,event.ended_at_utc),identity)
        self.assertEqual(event.performance_snapshot,original)
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,7000)
        self.assertIsNone(NeoScorpionSortTruck.query.one().availability_history)
