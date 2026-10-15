"""Physical segment concurrency proof in an isolated disposable PostgreSQL schema."""
import os
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import local
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import make_url
from app import create_app
from app.extensions import db
from app.models import Gateway, User, NeoScorpionFuelingEvent, NeoScorpionFuelWorkState, NeoScorpionSortTruck
from app.services import neoscorpion as service
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_neoscorpion_dispatch_planning as fixtures
from tests import test_neoscorpion_operational_timeline as timeline

URI = os.environ.get("NEOSTAFFING_TEST_POSTGRES_URL")


@unittest.skipUnless(URI, "Requires disposable local PostgreSQL")
class PhysicalSegmentPostgresTest(unittest.TestCase):
    login = timeline.OperationalTimelineTest.login
    def prepare(self,lead=15):
        if not hasattr(self,"work"):
            timeline.OperationalTimelineTest.prepare(self,lead)
    save = timeline.OperationalTimelineTest.save
    off = timeline.OperationalTimelineTest.off
    final_data = timeline.OperationalTimelineTest.final_data
    _exercise_returning_tail = timeline.OperationalTimelineTest._exercise_returning_tail

    def setUp(self):
        url = make_url(URI)
        if url.host not in {"localhost", "127.0.0.1"} or not url.database.startswith("neostaffing_test_"):
            raise RuntimeError("Refusing non-disposable PostgreSQL")
        self.schema = "scorpion_segment_" + uuid4().hex
        self.engine = create_engine(URI)
        with self.engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.addCleanup(self.drop_schema)

        def app_for_schema(config):
            config = type("PostgresSegmentConfig", (config,), {
                "SQLALCHEMY_DATABASE_URI": URI,
                "SQLALCHEMY_ENGINE_OPTIONS": {"connect_args": {"options": f"-csearch_path={self.schema}"}},
            })
            return create_app(config)

        self.fixture = fixtures.NeoScorpionDispatchPlanningTest()
        with patch.object(fixtures, "create_app", app_for_schema):
            self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.gateway, self.operation = self.fixture.gateway, self.fixture.operation
        self.client = self.fixture.client
        self.dispatcher = self.fixture._login_user("segment-dispatcher", "simulator")
        self.fueler = self.fixture._login_user("segment-fueler", "operator")
        self.mission = self.fixture._mission("UPS1038", "N491UP", 25000, 1)
        self.assignment = self.fixture._assignment(self.mission)
        from app.models import NeoScorpionSettings
        self.settings = NeoScorpionSettings(gateway_id=self.gateway.id)
        db.session.add(self.settings); db.session.commit()
        self.prepare()
        self.ids = self.gateway.id, self.fueler.id, self.dispatcher.id, self.assignment.id, self.work.id
        db.session.commit()
        if self._testMethodName not in {"test_additive_bootstrap_is_idempotent_without_historical_rewrite",
                                       "test_legacy_live_visit_migration_does_not_reuse_a_closed_event_owner",
                                       "test_concurrent_legacy_bootstrap_allocates_one_live_identity",
                                       "test_ambiguous_legacy_on_is_retained_without_becoming_new_visit_evidence",
                                       "test_same_cycle_legacy_revisit_gets_independent_identity"}:
            with patch.dict(self.fixture.app.config,{"TESTING":False}):
                ensure_neoscorpion_production_schema(self.fixture.app)

    def drop_schema(self):
        if not re.fullmatch(r"scorpion_segment_[0-9a-f]{32}", self.schema):
            raise RuntimeError("Unexpected test schema")
        with self.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.engine.dispose()

    def concurrent(self, callback):
        db.session.remove()
        def execute(index):
            with self.fixture.app.app_context():
                db.session.execute(text("SET LOCAL lock_timeout='5s'"))
                gateway = db.session.get(Gateway, self.ids[0])
                fueler = db.session.get(User, self.ids[1])
                dispatcher = db.session.get(User, self.ids[2])
                try:
                    result = callback(index, gateway, fueler, dispatcher)
                    db.session.commit()
                    return result
                except service.FuelerDataConflict:
                    db.session.rollback()
                    return "conflict"
                finally:
                    db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(execute, (0, 1)))

    def swap_tail(self):
        self.mission.assigned_tail_number="N492UP"; db.session.commit()
        service.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,
            expected_cycle=1,expected_tail="N492UP")
        db.session.commit()
        return dict(physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number)

    def test_duplicate_physical_off_preserves_first_timestamp(self):
        times = [self.start+timedelta(minutes=12), self.start+timedelta(minutes=13)]
        result = self.concurrent(lambda i,g,f,d: service.mark_fueler_off(g,f,self.ids[3],now_utc=times[i]).changed)
        self.assertEqual(sorted(result), [False,True])
        self.assertIn(db.session.get(NeoScorpionFuelWorkState,self.ids[4]).off_at_utc,times)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_duplicate_source_completion_posts_event_and_inventory_once(self):
        self.save(self.start+timedelta(minutes=1),**self.final_data())
        identity=self.swap_tail(); self.off(defer_completion=True,**identity)
        result=self.concurrent(lambda i,g,f,d: service.complete_fueled_assignment(g,d,self.ids[3],**identity).changed)
        self.assertEqual(sorted(result),[False,True])
        event=NeoScorpionFuelingEvent.query.one()
        self.assertEqual((event.tail_number,event.transfer_fuel_gallons),("N491UP",3000))
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,7000)
        self.assertIsNone(service.NeoScorpionFuelAssignment.query.one().completed_at_utc)

    def test_stale_segment_gallon_save_cannot_overwrite_winning_finalization(self):
        self.save(self.start+timedelta(minutes=1),remaining_left="6",remaining_ctr="0",remaining_right="6")
        identity=self.swap_tail(); self.off(**identity)
        source=service.fueler_context(self.gateway,self.fueler,**identity)["rows"][0]
        import json
        expected=json.dumps(source["edit_baseline"])
        result=self.concurrent(lambda i,g,f,d: service.save_fueler_entry(g,f,dict(
            assignment_id=self.ids[3],**identity,**{**self.final_data(),"transfer_fuel_gallons":str(2000+i*500)},expected=expected)).changed)
        self.assertEqual(result.count("conflict"),1)
        event=NeoScorpionFuelingEvent.query.one()
        self.assertIn(event.transfer_fuel_gallons,(2000,2500))
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,10000-event.transfer_fuel_gallons)

    def test_failure_rolls_back_segment_event_inventory_and_completion(self):
        self.save(self.start+timedelta(minutes=1),**self.final_data())
        identity=self.swap_tail(); self.off(defer_completion=True,**identity)
        close=service._close_current_truck_segment
        def fail(*args,**kwargs):
            close(*args,**kwargs)
            raise RuntimeError("Injected failure after posting")
        with patch.object(service,"_close_current_truck_segment",fail), self.assertRaisesRegex(RuntimeError,"Injected failure"):
            service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,**identity)
        db.session.rollback()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,10000)
        self.assertIsNone(self.work.physical_finalized_at_utc)

    def test_additive_bootstrap_is_idempotent_without_historical_rewrite(self):
        self.save(self.start+timedelta(minutes=1),**self.final_data()); self.off()
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        original=self.work.manual_on_at_utc
        original_event=NeoScorpionFuelingEvent.query.one()
        event_id, work_id=original_event.id,self.work.id
        # Recreate the actual production identity constraint, not merely a
        # latest-schema fixture. Keep existing tank and event foreign keys.
        db.session.execute(text("ALTER TABLE neoscorpion_fuel_work_states DROP CONSTRAINT uq_scorpion_work_assignment_visit"))
        db.session.execute(text("ALTER TABLE neoscorpion_fuel_work_states ADD CONSTRAINT uq_neoscorpion_fuel_work_state_assignment_tail UNIQUE (fuel_assignment_id,tail_number)"))
        db.session.execute(text("UPDATE neoscorpion_fuel_assignments SET active_work_state_id=NULL"))
        db.session.execute(text("UPDATE neoscorpion_fuel_work_states SET visit_number=NULL"))
        db.session.commit()
        with patch.dict(self.fixture.app.config,{"TESTING":False}):
            self.assertTrue(ensure_neoscorpion_production_schema(self.fixture.app))
            self.assertTrue(ensure_neoscorpion_production_schema(self.fixture.app))
        db.session.expire_all()
        self.assertEqual(self.work.manual_on_at_utc,original)
        self.assertEqual(self.assignment.active_work_state_id,work_id)
        self.assertEqual(db.session.get(NeoScorpionFuelingEvent,event_id).fuel_work_state_id,work_id)
        self.assertEqual(db.session.get(NeoScorpionFuelingEvent,event_id).transfer_fuel_gallons,3000)
        self.assertEqual(next(t for t in self.work.tank_states if t.tank_code=="left").actual_lbs,15000)
        constraints={c["name"] for c in inspect(db.engine).get_unique_constraints("neoscorpion_fuel_work_states")}
        self.assertNotIn("uq_neoscorpion_fuel_work_state_assignment_tail",constraints)
        self.assertIn("uq_scorpion_work_assignment_visit",constraints)
        for statement in (
            f"UPDATE neoscorpion_fuel_work_states SET physical_transfer_gallons=2000 WHERE id={work_id}",
            f"UPDATE neoscorpion_fuel_tank_states SET actual_lbs=14000 WHERE fuel_work_state_id={work_id}",
            f"DELETE FROM neoscorpion_fuel_tank_states WHERE fuel_work_state_id={work_id}",
            f"UPDATE neoscorpion_fueling_events SET transfer_fuel_gallons=2000 WHERE id={event_id}",
            f"UPDATE neoscorpion_fueling_event_tank_snapshots SET actual_lbs=14000 WHERE fueling_event_id={event_id}",
            f"INSERT INTO neoscorpion_fueling_events (sort_date_operation_id,fuel_assignment_id,fuel_work_state_id,tail_number,fuel_truck_id,sequence_number,transfer_fuel_gallons,created_at,updated_at) SELECT sort_date_operation_id,fuel_assignment_id,fuel_work_state_id,tail_number,fuel_truck_id,99,2000,created_at,updated_at FROM neoscorpion_fueling_events WHERE id={event_id}",
        ):
            with self.assertRaises(IntegrityError):
                db.session.execute(text(statement))
            db.session.rollback()
        self.assertEqual(self.work.physical_transfer_gallons,3000)
        baseline=service.fueler_edit_state(self.assignment,self.mission,self.work,None,list(self.work.tank_states))
        service.begin_physical_visit(self.gateway,self.fueler,self.assignment.id,expected=baseline)
        db.session.commit()
        new_id=self.assignment.active_work_state_id
        self.assertNotEqual(new_id,work_id)
        db.session.commit()  # Release this context's read locks before startup DDL.
        with patch.dict(self.fixture.app.config,{"TESTING":False}):
            ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all()
        self.assertEqual(self.assignment.active_work_state_id,new_id)

    def legacy_live_visit(self, cycle=2, proven_on=True):
        """Recreate a closed event owner reused by the old mutable slot schema."""
        self.save(self.start+timedelta(minutes=1),**self.final_data()); self.off()
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        original_id=self.work.id
        original_event_id=NeoScorpionFuelingEvent.query.one().id
        # The pre-Batch-3 schema reused this same row on a follow-up cycle.
        # Its completed event and snapshots still refer to the row's old ID.
        db.session.execute(text("ALTER TABLE neoscorpion_fuel_work_states DROP CONSTRAINT uq_scorpion_work_assignment_visit"))
        db.session.execute(text("ALTER TABLE neoscorpion_fuel_work_states ADD CONSTRAINT uq_neoscorpion_fuel_work_state_assignment_tail UNIQUE (fuel_assignment_id,tail_number)"))
        db.session.execute(text("""UPDATE neoscorpion_fuel_assignments SET active_work_state_id=NULL,
            current_cycle_number=:cycle,current_cycle_type='uplift',completed_at_utc=NULL,
            review_status='assigned',transfer_fuel_gallons=1200 WHERE id=:id"""),{"id":self.assignment.id,"cycle":cycle})
        db.session.execute(text("""UPDATE neoscorpion_fuel_work_states SET visit_number=NULL,
            physical_sequence_number=NULL,physical_cycle_number=NULL,physical_cycle_type=NULL,
            physical_truck_id=NULL,physical_fueler_user_id=NULL,physical_transfer_gallons=NULL,
            physical_finalized_at_utc=NULL,physical_finalized_by_user_id=NULL,performance_context=NULL,
            on_at_utc=:legacy_on,manual_on_at_utc=:on_at,manual_on_cycle_number=:cycle,off_at_utc=NULL,
            actuals_received_at_utc=NULL WHERE id=:id"""),
            {"id":original_id,"on_at":self.start+timedelta(minutes=20) if proven_on else None,
             "legacy_on":self.start,"cycle":cycle})
        self.mission.fuel_status="assigned"
        self.mission.fuel_completed_at_utc=None
        db.session.commit()
        return original_id, original_event_id

    def test_legacy_live_visit_migration_does_not_reuse_a_closed_event_owner(self):
        """Release gate: production's mutable slot may contain a later live visit."""
        original_id, original_event_id = self.legacy_live_visit()
        old_tanks=[(t.id,t.tank_code,t.remaining_lbs,t.actual_lbs) for t in self.work.tank_states]
        event=NeoScorpionFuelingEvent.query.one()
        old_event=(event.started_at_utc,event.ended_at_utc,event.performance_snapshot)
        old_snapshots=[(t.id,t.tank_code,t.remaining_lbs,t.actual_lbs) for t in event.tank_snapshots]
        inventory=self.selection.current_gallons if hasattr(self,"selection") else NeoScorpionSortTruck.query.one().current_gallons
        audit_count=service.NeoScorpionFuelAuditEntry.query.count()
        db.session.commit()
        with patch.dict(self.fixture.app.config,{"TESTING":False}):
            ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all()
        self.assertEqual(db.session.get(NeoScorpionFuelingEvent,original_event_id).fuel_work_state_id,original_id)
        self.assertEqual(db.session.get(NeoScorpionFuelingEvent,original_event_id).transfer_fuel_gallons,3000)
        self.assertNotEqual(self.assignment.active_work_state_id,original_id,
            "Migration reused a completed event's legacy owner for the later live physical visit")
        live_id=self.assignment.active_work_state_id
        live=service._locked_current_fuel_work_state(self.assignment,self.mission)
        self.assertEqual(live.id,live_id)
        self.assertEqual(live.manual_on_at_utc,self.start+timedelta(minutes=20))
        self.assertIsNone(live.off_at_utc)
        self.assertIsNone(live.physical_transfer_gallons)
        self.assertTrue(live.physical_transfer_review)
        self.assertEqual(live.tank_states,[])  # No stale Actuals promoted as current measurements.
        self.assertEqual(self.assignment.transfer_fuel_gallons,1200)  # Raw legacy projection retained for review.
        self.assertEqual([(t.id,t.tank_code,t.remaining_lbs,t.actual_lbs) for t in self.work.tank_states],old_tanks)
        self.assertEqual((event.started_at_utc,event.ended_at_utc,event.performance_snapshot),old_event)
        self.assertEqual([(t.id,t.tank_code,t.remaining_lbs,t.actual_lbs) for t in event.tank_snapshots],old_snapshots)
        self.assertEqual(service.NeoScorpionFuelAuditEntry.query.count(),audit_count)
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,inventory)
        db.session.commit()
        with patch.dict(self.fixture.app.config,{"TESTING":False}):
            ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all()
        self.assertEqual(self.assignment.active_work_state_id,live_id)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),2)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.off(at=self.start+timedelta(minutes=30))
        self.assertEqual(live.off_at_utc,self.start+timedelta(minutes=30))
        self.assertEqual(self.work.off_at_utc,None)  # Original ambiguous raw slot is preserved, not rewritten.
        self.assertEqual(event.ended_at_utc,old_event[1])
        with self.assertRaisesRegex(ValueError,"dispatcher must verify"):
            self.save(self.start+timedelta(minutes=31),**{**self.final_data(),"transfer_fuel_gallons":"1200"})
        db.session.rollback()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,inventory)
        service.save_fueler_entry(self.gateway,self.dispatcher,dict(
            assignment_id=self.assignment.id,physical_work_id=live_id,physical_sequence=live.physical_sequence_number,
            **{**self.final_data(),"transfer_fuel_gallons":"1200"},
            segment_review_reason="Verified receipt belongs to the later 1200-gallon visit"),
            dispatcher=True,now_utc=self.start+timedelta(minutes=31))
        db.session.commit()
        events=NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id).all()
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        events=NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id).all()
        self.assertEqual([(e.fuel_work_state_id,e.transfer_fuel_gallons) for e in events],
                         [(original_id,3000),(live_id,1200)])
        self.assertEqual(live.physical_transfer_gallons,1200)
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,inventory-1200)
        self.assertFalse(service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id).changed)
        db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),2)
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,inventory-1200)

    def test_same_cycle_legacy_revisit_gets_independent_identity(self):
        old_id,event_id=self.legacy_live_visit(cycle=1)
        with patch.dict(self.fixture.app.config,{"TESTING":False}):
            ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all()
        live=db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id)
        self.assertNotEqual(live.id,old_id)
        self.assertEqual(live.physical_cycle_number,1)
        self.assertEqual(live.manual_on_at_utc,self.start+timedelta(minutes=20))
        self.assertEqual(db.session.get(NeoScorpionFuelingEvent,event_id).fuel_work_state_id,old_id)

    def test_ambiguous_legacy_on_is_retained_without_becoming_new_visit_evidence(self):
        old_id,event_id=self.legacy_live_visit(proven_on=False)
        with patch.dict(self.fixture.app.config,{"TESTING":False}):
            ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all()
        live=db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id)
        self.assertNotEqual(live.id,old_id)
        self.assertTrue(live.physical_transfer_review)
        self.assertIsNone(live.on_at_utc)
        self.assertIsNone(live.manual_on_at_utc)
        self.assertIsNotNone(self.work.on_at_utc)  # Preserve uncertain legacy timestamp, never reuse as proof.
        self.assertEqual(db.session.get(NeoScorpionFuelingEvent,event_id).transfer_fuel_gallons,3000)
        service.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,
            expected_cycle=2,expected_tail="N491UP",expected_truck_id=self.truck.id,
            expected_work_ref=live.id,require_work_ref=True,now_utc=self.start+timedelta(minutes=21))
        db.session.commit()
        self.assertTrue(live.physical_transfer_review)  # ON must not allocate ambiguous assignment-wide gallons.
        self.assertIsNone(live.physical_transfer_gallons)
        self.assertTrue(live.performance_context["exclusions"])

    def test_concurrent_legacy_bootstrap_allocates_one_live_identity(self):
        old_id,event_id=self.legacy_live_visit()
        db.session.remove()
        def upgrade(_):
            with self.fixture.app.app_context():
                return ensure_neoscorpion_production_schema(self.fixture.app)
        with patch.dict(self.fixture.app.config,{"TESTING":False}), ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(upgrade,(0,1))),[True,True])
        parent=db.session.get(service.NeoScorpionFuelAssignment,self.ids[3])
        self.assertNotEqual(parent.active_work_state_id,old_id)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),2)
        self.assertEqual(db.session.get(NeoScorpionFuelingEvent,event_id).fuel_work_state_id,old_id)
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,7000)

    test_returning_tail_has_independent_events_and_inventory = timeline.OperationalTimelineTest.test_returning_tail_cannot_overwrite_its_finalized_physical_work
    test_returning_tail_different_truck = timeline.OperationalTimelineTest.test_returning_tail_with_different_truck_preserves_each_visit
    test_pending_same_tail_visit_preserves_old_actuals = timeline.OperationalTimelineTest.test_same_tail_pending_actuals_survive_a_new_visit_and_stale_form

    def test_duplicate_new_visit_does_not_allocate_two_records(self):
        self.off()
        expected=service.fueler_context(self.gateway,self.fueler)["rows"][0]["edit_baseline"]
        result=self.concurrent(lambda i,g,f,d: service.begin_physical_visit(g,f,self.ids[3],expected=expected).changed)
        self.assertEqual(result.count("conflict"),1)
        self.assertEqual(result.count(True),1)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),2)

    def test_duplicate_on_does_not_create_extra_segment(self):
        self.off()
        expected=service.fueler_context(self.gateway,self.fueler)["rows"][0]["edit_baseline"]
        service.begin_physical_visit(self.gateway,self.fueler,self.assignment.id,expected=expected)
        db.session.commit()
        work_id=self.assignment.active_work_state_id
        result=self.concurrent(lambda i,g,f,d: service.confirm_fueler_truck_on(g,f,self.ids[3],
            expected_cycle=1,expected_tail="N491UP",expected_truck_id=self.truck.id,
            expected_work_ref=work_id,require_work_ref=True)[0])
        self.assertEqual(sorted(result),[False,True])
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),2)

    def test_competing_truck_on_freshly_rechecks_other_occupation(self):
        self.off()
        expected=service.fueler_context(self.gateway,self.fueler)["rows"][0]["edit_baseline"]
        service.begin_physical_visit(self.gateway,self.fueler,self.assignment.id,expected=expected)
        other_mission=self.fixture._mission("UPS902","N492UP",25000,2)
        other_assignment=self.fixture._assignment(other_mission,truck=self.truck)
        other_assignment.assigned_fueler_user_id=self.dispatcher.id
        db.session.commit()
        other_id=other_assignment.id
        operation_id,truck_id=self.operation.id,self.truck.id
        def press(index,g,f,d):
            # Intentionally preload both assignments/work to expose cached
            # ORM evidence before the serialized mutation lock is acquired.
            service._fuel_work_states_by_assignment_tail(service._assignments_by_mission(
                db.session.get(service.SortDateOperation,operation_id)).values())
            try:
                return service.confirm_fueler_truck_on(g,f if index==0 else d,
                    self.ids[3] if index==0 else other_id,expected_cycle=1,
                    expected_tail="N491UP" if index==0 else "N492UP",expected_truck_id=truck_id)[0]
            except ValueError as error:
                if "TRUCK STILL FUELING" not in str(error): raise
                return "busy"
        result=self.concurrent(press)
        self.assertEqual(result.count(True),1)
        self.assertEqual(result.count("busy"),1)
        active=NeoScorpionFuelWorkState.query.filter(NeoScorpionFuelWorkState.manual_on_at_utc.is_not(None),
            NeoScorpionFuelWorkState.off_at_utc.is_(None)).all()
        self.assertEqual(len(active),1)

    def test_overlapping_sort_requests_cannot_double_occupy_one_truck(self):
        self.off()
        expected=service.fueler_context(self.gateway,self.fueler)["rows"][0]["edit_baseline"]
        service.begin_physical_visit(self.gateway,self.fueler,self.assignment.id,expected=expected)
        first_operation_id,truck_id=self.operation.id,self.truck.id
        other_operation=service.SortDateOperation(generated_by_user_id=self.dispatcher.id,gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,sort_date=self.operation.sort_date+timedelta(days=1),sort_name="night",window_minutes=60)
        db.session.add(other_operation); db.session.flush()
        other_mission=self.fixture._mission("UPS902","N492UP",25000,2)
        other_mission.sort_date_operation_id=other_operation.id
        other_assignment=self.fixture._assignment(other_mission,truck=self.truck)
        other_assignment.sort_date_operation_id=other_operation.id
        other_assignment.assigned_fueler_user_id=self.dispatcher.id
        db.session.add(NeoScorpionSortTruck(sort_date_operation_id=other_operation.id,fuel_truck_id=truck_id,
            status="available",starting_gallons=10000,current_gallons=10000))
        db.session.commit()
        operation_ids=(first_operation_id,other_operation.id)
        assignment_ids=(self.assignment.id,other_assignment.id)
        bound=local()
        def operation(_gateway):
            return db.session.get(service.SortDateOperation,bound.operation_id)
        def press(index,g,f,d):
            bound.operation_id=operation_ids[index]
            try:
                return service.confirm_fueler_truck_on(g,f if index==0 else d,assignment_ids[index],
                    expected_cycle=1,expected_tail="N491UP" if index==0 else "N492UP",expected_truck_id=truck_id)[0]
            except ValueError as error:
                if "TRUCK STILL FUELING" not in str(error): raise
                db.session.rollback()  # No partial ON from the rejected start.
                return "busy"
        with patch.object(service,"current_sort_operation",side_effect=operation):
            result=self.concurrent(press)
        self.assertEqual(result.count(True),1)
        self.assertEqual(result.count("busy"),1)
        self.assertEqual(NeoScorpionFuelWorkState.query.filter(
            NeoScorpionFuelWorkState.physical_truck_id==truck_id,
            NeoScorpionFuelWorkState.manual_on_at_utc.is_not(None),NeoScorpionFuelWorkState.off_at_utc.is_(None)).count(),1)
