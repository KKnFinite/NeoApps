import copy
import unittest
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewaySortMatrix,
    GatewayNodeRole,
    LiveScreenRefreshSetting,
    NeoNode,
    NeoScorpionFuelAssignment,
    NeoScorpionAircraftFuelSetting,
    NeoScorpionFuelingEvent,
    NeoScorpionFuelerNickname,
    NeoScorpionFuelTruck,
    NeoScorpionFuelWorkState,
    NeoScorpionSettings,
    NeoScorpionSortAssetState,
    NeoScorpionSortFueler,
    NeoScorpionSortTruck,
    PortalAppAccess,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    SortTimelineSettings,
    SortTimelineSortSetting,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion import _fueling_board_progress
from app.services.neoscorpion import fuel_dispatch_context
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoScorpionLiveAssignmentsTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.add(NeoScorpionSettings(gateway_id=self.gateway.id))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _performance_fixture(self):
        fueler = self._add_user("performance_fueler", "operator")
        replacement = self._add_user("performance_replacement", "operator")
        self._configure_active_night_sort()
        operation, mission = self._add_operation_with_mission()
        trucks = [NeoScorpionFuelTruck(gateway_id=self.gateway.id, truck_number=str(number),
                                      remaining_fuel_gallons=5000) for number in (7, 8)]
        db.session.add_all(trucks)
        db.session.flush()
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id, sort_date_mission_id=mission.id,
            assigned_fueler_user_id=replacement.id, assigned_truck_id=trucks[1].id,
        )
        db.session.add(assignment)
        db.session.flush()
        work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id, tail_number="N500UP")
        db.session.add(work)
        db.session.add_all([
            NeoScorpionSortFueler(sort_date_operation_id=operation.id, user_id=user.id)
            for user in (fueler, replacement)
        ])
        db.session.add_all([
            NeoScorpionSortTruck(sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
                                 status="available", starting_gallons=5000, current_gallons=5000)
            for truck in trucks
        ])
        db.session.add(NeoScorpionFuelerNickname(gateway_id=self.gateway.id, user_id=fueler.id,
                                                 nickname="Falcon", nickname_key="falcon"))
        db.session.add(NeoScorpionSortAssetState(sort_date_operation_id=operation.id, revision=1))
        db.session.commit()
        return operation, assignment, work, fueler, replacement, trucks

    def _performance_event(self, operation, assignment, work, fueler, truck, sequence,
                           start, end, *, gallons=300, event_type="fuel", tail="N500UP"):
        event = NeoScorpionFuelingEvent(
            sort_date_operation_id=operation.id, fuel_assignment_id=assignment.id,
            fuel_work_state_id=work.id, tail_number=tail, fuel_truck_id=truck.id,
            fueler_user_id=fueler.id if fueler else None, sequence_number=sequence,
            event_type=event_type, cycle_number=1, transfer_fuel_gallons=gallons,
            started_at_utc=start, ended_at_utc=end,
        )
        db.session.add(event)
        return event

    def _canonical_performance_cycle(self, operation, fueler, dispatcher, truck, number, tail, start, minutes):
        from tests.neoscorpion_evidence_fixture import FuelCycle, transfer_readings
        cycle = FuelCycle.create(self.gateway, operation, fueler, dispatcher, truck,
            flight=number, tail=tail)
        cycle.record_arrival(start - timedelta(minutes=5))
        cycle.assign(start - timedelta(minutes=1), current_gallons=5000)
        cycle.park(start - timedelta(minutes=1), 'A', 'A01' if tail == 'N491UP' else 'A02')
        cycle.save(start - timedelta(minutes=1), apu_running='no',
            remaining_left='6', remaining_ctr='0', remaining_right='6')
        event = cycle.finish(start, start + timedelta(minutes=minutes),
            **transfer_readings(gallons='1500'))
        return cycle, event

    def test_dispatch_performance_aggregates_events_and_refreshes_with_dispatch(self):
        operation, assignment, work, fueler, replacement, trucks = self._performance_fixture()
        dispatcher = self._add_user('performance_dispatcher', 'simulator')
        db.session.commit()
        cycle, first_event = self._canonical_performance_cycle(operation, fueler, dispatcher, trucks[0],
            'UPS-W3-1', 'N491UP', datetime(2026, 8, 18, 2), 15)
        context = fuel_dispatch_context(self.gateway)
        by_fueler = {row['id']: row for row in context['fueling_performance']['fuelers']}
        by_truck = {row['id']: row for row in context['fueling_performance']['trucks']}
        self.assertEqual((by_fueler[fueler.id]['name'], by_fueler[fueler.id]['jobs'],
            by_fueler[fueler.id]['prof']), ('Falcon', 1, 100))
        self.assertEqual((by_fueler[replacement.id]['jobs'], by_fueler[replacement.id]['prof']), (0, None))
        # No earlier physical truck origin exists for the first visit. Do not invent one.
        self.assertEqual((by_truck[trucks[0].id]['jobs'], by_truck[trucks[0].id]['prof']), (0, None))
        self.assertEqual((by_truck[trucks[1].id]['jobs'], by_truck[trucks[1].id]['prof']), (0, None))
        self._login(replacement)
        first = self.client.get('/neoscorpion/fuel-dispatch/live-panel').get_json()
        self.assertIn('data-fueling-performance', first['html'])
        self.assertIn('1 job · PROF 100', first['html'])
        self.assertIn('0 jobs · PROF —', first['html'])
        self.assertIn('Falcon', first['html'])
        frozen = copy.deepcopy(first_event.performance_snapshot)
        second, second_event = self._canonical_performance_cycle(operation, fueler, dispatcher, trucks[0],
            'UPS-W3-2', 'N492UP', datetime(2026, 8, 18, 2, 30), 30)
        changed = self.client.get('/neoscorpion/fuel-dispatch/live-panel').get_json()
        self.assertGreater(changed['revision'], first['revision'])
        self.assertIn('2 jobs · PROF 67', changed['html'])
        from app.services.neoscorpion_prof import event_math
        self.assertEqual(event_math(second_event)['truck_prof'], 16)
        self.assertEqual(assignment.assigned_fueler_user_id, replacement.id)
        self.assertEqual(assignment.assigned_truck_id, trucks[1].id)
        db.session.add(NeoScorpionAircraftFuelSetting(gateway_id=self.gateway.id, aircraft_type='B757',
            apu_rate_thousand_lbs_per_hour=0.3, assignment_pump_rate_gallons_per_minute=150))
        db.session.commit()
        configured = fuel_dispatch_context(self.gateway)['fueling_performance']
        self.assertEqual(next(row['prof'] for row in configured['fuelers'] if row['id'] == fueler.id), 67)
        self.assertEqual(first_event.performance_snapshot, frozen)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 2)
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id,
            fuel_truck_id=trucks[0].id).one().current_gallons, 2000)

    def test_dispatch_performance_excludes_unreliable_events_and_other_sorts(self):
        operation, assignment, work, fueler, replacement, trucks = self._performance_fixture()
        dispatcher = self._add_user('performance_dispatcher', 'simulator')
        db.session.commit()
        canonical, valid = self._canonical_performance_cycle(operation, fueler, dispatcher, trucks[0],
            'UPS-W3-1', 'N491UP', datetime(2026, 8, 18, 2), 15)
        # Controlled legacy imports remain explicitly unverified; they cannot
        # become positive samples merely because their raw times/gallons look plausible.
        base = datetime(2026, 8, 18, 2)
        invalid = [self._performance_event(operation, assignment, work, replacement, trucks[1], 1,
            base, base+timedelta(minutes=1), gallons=300)]
        # Retain all original unreliable-import cases. Each remains an explicitly
        # unverified legacy record, never a canonical positive performance sample.
        for sequence, kind, gallons, start, end, actor, tail in (
            (2,'defuel',300,base+timedelta(minutes=2),base+timedelta(minutes=3),fueler,'N500UP'),
            (3,'fuel',0,base+timedelta(minutes=4),base+timedelta(minutes=5),fueler,'N500UP'),
            (4,'fuel',300,None,base+timedelta(minutes=7),fueler,'N500UP'),
            (5,'fuel',300,base+timedelta(minutes=9),base+timedelta(minutes=8),fueler,'N500UP'),
            (6,'fuel',300,base+timedelta(minutes=10),base+timedelta(minutes=11),fueler,'N500UP'),
            (7,'fuel',300,base+timedelta(minutes=12),base+timedelta(minutes=13),None,'N500UP'),
            (8,'fuel',300,base+timedelta(minutes=14),base+timedelta(minutes=15),fueler,'N777UP')):
            legacy = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,tail_number=tail,visit_number=sequence)
            db.session.add(legacy); db.session.flush()
            invalid.append(self._performance_event(operation, assignment, legacy, actor, trucks[0], sequence,
                start,end,gallons=gallons,event_type=kind,tail=tail))
        # Controlled interrupted/held historical import, with no trusted ON.
        work.ended_early_at_utc=base+timedelta(minutes=11)
        assignment.hold_at_utc=base+timedelta(minutes=10,seconds=30)
        db.session.commit()
        prior_operation = SortDateOperation(gateway_id=self.gateway.id, gateway_code=self.gateway.code,
            sort_date=date(2026, 8, 16), sort_name='night', window_minutes=360)
        db.session.add(prior_operation); db.session.commit()
        prior, prior_event = self._canonical_performance_cycle(prior_operation, fueler, dispatcher, trucks[0],
            'UPS-W3-PRIOR', 'N493UP', datetime(2026, 8, 17, 2), 15)
        scores = fuel_dispatch_context(self.gateway)['fueling_performance']
        by_fueler = {row['id']: row for row in scores['fuelers']}
        self.assertEqual((by_fueler[fueler.id]['jobs'], by_fueler[fueler.id]['prof']), (1, 100))
        self.assertEqual(by_fueler[replacement.id]['jobs'], 0)
        from app.services.neoscorpion_prof import load_observation_jobs
        jobs = load_observation_jobs(self.gateway.id, operation_id=operation.id)
        self.assertEqual({j['event'].id for j in jobs}, {valid.id, *(e.id for e in invalid)})
        self.assertNotIn(prior_event.id, {j['event'].id for j in jobs})
        for job in jobs:
            if job['event'].id != valid.id:
                self.assertTrue(job['fueler_reasons'])
                self.assertIsNone(job['fueler_prof'])
                self.assertIsNone(job['truck_prof'])

    def test_dispatch_performance_empty_roster(self):
        self._configure_active_night_sort()
        self._add_operation_with_mission()
        db.session.commit()
        self.assertEqual(fuel_dispatch_context(self.gateway)["fueling_performance"],
                         {"fuelers": (), "trucks": ()})

    def test_fueling_board_watcher_access_and_server_side_denial(self):
        watcher = self._add_user("board_watcher", "watcher")
        db.session.commit()
        self._login(watcher)
        for path in ("/neoscorpion/fueling-board", "/neoscorpion/fueling-board/live-panel",
                     "/neoscorpion/fueling-board/revision"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"neoscorpion-fueler-form", response.data)
                self.assertEqual(self.client.post(path).status_code, 405)
        with patch("app.neonodes.neoscorpion.routes.permission_access", return_value={"can_view": False}):
            for path in ("/neoscorpion/fueling-board", "/neoscorpion/fueling-board/live-panel",
                         "/neoscorpion/fueling-board/revision"):
                self.assertEqual(self.client.get(path).status_code, 403)

    def test_fueling_board_orders_active_assignments_and_uses_nicknames(self):
        watcher = self._add_user("board_viewer", "watcher")
        fueler = self._add_user("board_fueler", "operator")
        idle = self._add_user("board_idle", "operator")
        self._configure_active_night_sort()
        operation, later = self._add_operation_with_mission()
        earlier = SortDateMission(
            sort_date=operation.sort_date, gateway_code=self.gateway.code,
            sort_name="night", sort_date_operation_id=operation.id,
            mission_type="departure", mission_source="manual", flight_number="UPS400",
            origin=self.gateway.code, destination="ORD", timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 17, 22, 30),
            planned_datetime_utc=datetime(2026, 8, 18, 3, 30), planned_source="manual",
            assigned_tail_number="N400UP", tail_source="manual", fuel_status="waiting",
        )
        unassigned = SortDateMission(
            sort_date=operation.sort_date, gateway_code=self.gateway.code,
            sort_name="night", sort_date_operation_id=operation.id,
            mission_type="departure", mission_source="manual", flight_number="UPS300",
            origin=self.gateway.code, destination="MIA", timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 17, 21, 30),
            planned_datetime_utc=datetime(2026, 8, 18, 2, 30), planned_source="manual",
            assigned_tail_number="N300UP", tail_source="manual", fuel_status="waiting",
        )
        db.session.add_all([earlier, unassigned])
        db.session.flush()
        early_assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id, sort_date_mission_id=earlier.id,
            assigned_fueler_user_id=fueler.id, current_cycle_type="defuel",
        )
        late_assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id, sort_date_mission_id=later.id,
            assigned_fueler_user_id=fueler.id, current_cycle_type="uplift",
        )
        db.session.add_all([
            early_assignment, late_assignment,
            NeoScorpionFuelerNickname(gateway_id=self.gateway.id, user_id=fueler.id,
                                      nickname="Falcon", nickname_key="falcon"),
            NeoScorpionSortAssetState(sort_date_operation_id=operation.id, revision=4),
        ])
        db.session.commit()
        self._login(watcher)

        statements = []
        def capture_statement(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)
        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            response = self.client.get("/neoscorpion/fueling-board")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertLess(body.index('data-board-assignment-id="' + str(early_assignment.id)),
                        body.index('data-board-assignment-id="' + str(late_assignment.id)))
        self.assertIn("Falcon", body)
        self.assertIn("DEFUEL", body)
        self.assertIn("UPLIFT", body)
        self.assertNotIn("UPS300", body)
        self.assertNotIn(idle.display_name, body)
        self.assertNotIn("neoscorpion-fueler-form", body)
        self.assertFalse(any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements))

        later.eta_datetime_utc = datetime(2026, 8, 18, 3, 0)
        db.session.commit()
        reordered = self.client.get("/neoscorpion/fueling-board/live-panel").get_json()["html"]
        self.assertLess(reordered.index(f'data-board-assignment-id="{late_assignment.id}"'),
                        reordered.index(f'data-board-assignment-id="{early_assignment.id}"'))

        early_assignment.review_status = "complete"
        db.session.commit()
        panel = self.client.get("/neoscorpion/fueling-board/live-panel").get_json()
        self.assertNotIn(f'data-board-assignment-id="{early_assignment.id}"', panel["html"])
        self.assertIn(f'data-board-assignment-id="{late_assignment.id}"', panel["html"])
        self.assertEqual(panel["revision"], 4)

        late_assignment.completed_at_utc = datetime(2026, 8, 18, 2, 45)
        asset_state = NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation.id).one()
        asset_state.revision = 5
        db.session.commit()
        panel = self.client.get("/neoscorpion/fueling-board/live-panel").get_json()
        self.assertNotIn('data-board-assignment-id=', panel["html"])
        self.assertEqual(self.client.get("/neoscorpion/fueling-board/revision").get_json()["revision"], 5)

    def test_fueling_board_progress_uses_canonical_milestones(self):
        # Read-only Board displays the primary status produced by the shared
        # classifier, including truckless FOB and genuine review.
        for label in ("PENDING", "READY", "ASSIGNED", "FUELING", "OFF", "FOB READY", "COMPLETE", "FOB", "REVIEW"):
            with self.subTest(label=label):
                self.assertEqual(_fueling_board_progress({"dispatch_status_label": label}), label)

    def test_fueling_board_refresh_contract(self):
        with open("app/static/js/neoscorpion_fueling_board_live.js", encoding="utf-8") as source:
            script = source.read()
        self.assertIn("NeoLiveUpdates.create", script)
        self.assertIn("[data-board-assignment-id]", script)
        self.assertIn("card.isEqualNode(nextCard)", script)
        self.assertIn("currentList.insertBefore", script)
        self.assertIn("window.scrollTo(scrollX, scrollY)", script)
        self.assertNotIn("window.location.reload", script)

    def test_revision_endpoint_authorization_and_no_current_sort(self):
        operator = self._add_user("fueler_operator", "operator")
        watcher = self._add_user("fueler_watcher", "watcher")
        db.session.commit()

        self._login(operator)
        response = self.client.get("/neoscorpion/fuel-assignments/revision")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "ok": True,
                "current_operation": False,
                "operation_id": None,
                "revision": 0,
                "tail_notes_revision": 0,
                },
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")

        self._login(watcher)
        response = self.client.get("/neoscorpion/fuel-assignments/revision")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "current_operation": False,
                                              "operation_id": None, "revision": 0, "tail_notes_revision": 0})

    def test_revision_endpoint_is_one_fingerprint_query_and_never_writes(self):
        operator = self._add_user("query_operator", "operator")
        self._configure_active_night_sort()
        operation, _mission = self._add_operation_with_mission()
        db.session.commit()
        self._login(operator)

        statements = []

        def capture_statement(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement.strip())

        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
                response = self.client.get(
                    "/neoscorpion/fuel-assignments/revision"
                )
                self.assertEqual(commit.call_count, 0)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["operation_id"], operation.id)
        self.assertEqual(response.get_json()["revision"], 0)
        self.assertIsNone(
            NeoScorpionSortAssetState.query.filter_by(
                sort_date_operation_id=operation.id
            ).first()
        )
        fingerprint_queries = [
            statement
            for statement in statements
            if "neoscorpion_sort_asset_states" in statement.lower()
        ]
        self.assertEqual(len(fingerprint_queries), 1)
        self.assertFalse(
            any(
                statement.lstrip().upper().startswith(
                    ("INSERT", "UPDATE", "DELETE")
                )
                for statement in statements
            )
        )

    def test_fuel_dispatch_revision_authorization_and_no_current_sort(self):
        operator = self._add_user("dispatch_operator", "operator")
        watcher = self._add_user("dispatch_watcher", "watcher")
        db.session.commit()

        self._login(operator)
        response = self.client.get("/neoscorpion/fuel-dispatch/revision")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "current_operation": False,
                "operation_id": None,
                "revision": 0,
                "tail_notes_revision": 0, "tail_revision": "",
            },
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")

        self._login(watcher)
        response = self.client.get("/neoscorpion/fuel-dispatch/revision")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"current_operation": False,
                                              "operation_id": None, "revision": 0, "tail_notes_revision": 0, "tail_revision": ""})

    def test_fuel_dispatch_revision_active_operation_is_read_only_and_bounded(self):
        operator = self._add_user("dispatch_query_operator", "operator")
        self._configure_active_night_sort()
        operation, _mission = self._add_operation_with_mission()
        db.session.commit()
        self._login(operator)
        statements = []

        def capture_statement(_connection, _cursor, statement, _params, _context, _many):
            statements.append(statement.strip())

        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            response = self.client.get("/neoscorpion/fuel-dispatch/revision")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["operation_id"], operation.id)
        self.assertEqual(response.get_json()["revision"], 0)
        self.assertFalse(
            any(
                statement.lstrip().upper().startswith(
                    ("INSERT", "UPDATE", "DELETE")
                )
                for statement in statements
            )
        )
        selects = [statement for statement in statements if statement.upper().startswith("SELECT")]
        self.assertEqual(sum("FROM neoscorpion_vendor_accounts" in q for q in selects), 1)
        self.assertEqual(sum("max(sort_date_missions.tail_updated_at)" in q for q in selects), 1)
        self.assertEqual(len(selects), 10)  # Eight original reads + vendor guard + tail fingerprint.

    def test_revision_and_assignment_identifiers_render_for_current_fueler(self):
        operator = self._add_user("assigned_operator", "operator")
        self._configure_active_night_sort()
        operation, mission = self._add_operation_with_mission()
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            assigned_fueler_user_id=operator.id,
        )
        db.session.add_all(
            [
                assignment,
                NeoScorpionSortAssetState(
                    sort_date_operation_id=operation.id,
                    revision=8,
                ),
                LiveScreenRefreshSetting(
                    gateway_id=self.gateway.id,
                    screen_key="neoscorpion.fuel_assignments",
                    interval_seconds=15,
                ),
            ]
        )
        db.session.commit()
        self._login(operator)

        response = self.client.get("/neoscorpion/fueler")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(f'data-operation-id="{operation.id}"', body)
        self.assertIn('data-revision="8"', body)
        self.assertIn(f'data-current-user-id="{operator.id}"', body)
        self.assertIn('data-refresh-interval-ms="15000"', body)
        self.assertIn('data-refresh-source="override"', body)
        self.assertIn('data-panel-url="/neoscorpion/fueler/live-panel"', body)
        self.assertIn('data-fuel-assignments-panel', body)
        self.assertIn(f'data-fuel-assignment-id="{assignment.id}"', body)
        self.assertNotIn("NEW ASSIGNMENT", body)
        self.assertIn("data-fueler-operational-warning", body)
        self.assertIn("data-fueler-submission-status", body)
        self.assertNotIn("data-fueler-cycle-chip", body)
        self.assertIn("neoscorpion_fuel_assignments_live.js", body)
        self.assertIn(mission.flight_number, body)
        self.assertNotIn("KEEP LIVE / MONITOR MODE", body)

        revision = self.client.get(
            "/neoscorpion/fuel-assignments/revision"
        ).get_json()
        self.assertEqual(revision["operation_id"], operation.id)
        self.assertEqual(revision["revision"], 8)

    def test_live_panel_renders_fragment_without_page_reload(self):
        operator = self._add_user("panel_operator", "operator")
        self._configure_active_night_sort()
        operation, mission = self._add_operation_with_mission()
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            assigned_fueler_user_id=operator.id,
        )
        db.session.add_all(
            [
                assignment,
                NeoScorpionSortAssetState(
                    sort_date_operation_id=operation.id,
                    revision=9,
                ),
            ]
        )
        db.session.commit()
        self._login(operator)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.get("/neoscorpion/fueler/live-panel")
            self.assertEqual(commit.call_count, 0)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["operation_id"], operation.id)
        self.assertEqual(payload["revision"], 9)
        self.assertIn('data-fuel-assignments-panel', payload["html"])
        self.assertIn(f'data-fuel-assignment-id="{assignment.id}"', payload["html"])
        self.assertNotIn("<html", payload["html"].lower())
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_live_script_uses_effective_visible_polling_and_session_alert_state(self):
        with open(
            "app/static/js/neoscorpion_fuel_assignments_live.js",
            encoding="utf-8",
        ) as source:
            script = source.read()

        self.assertIn("root.dataset.refreshIntervalMs", script)
        self.assertIn("continuousWhileVisible: true", script)
        self.assertNotIn("setMonitorMode", script)
        self.assertIn("sessionStorage", script)
        self.assertNotIn("data-new-assignment-marker", script)
        self.assertIn("AudioContext", script)
        self.assertIn("root.dataset.panelUrl", script)
        self.assertIn("[data-fuel-assignments-panel]", script)
        self.assertIn("window.scrollTo", script)
        self.assertIn("NeoScorpionFuelData?.initialize?.(nextPanel)", script)
        self.assertIn("neoscorpion:fuel-data-saved", script)
        self.assertIn('window.addEventListener("pagehide"', script)
        self.assertNotIn("window.location.reload()", script)

    def _add_operation_with_mission(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 8, 17),
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=360,
        )
        db.session.add(operation)
        db.session.flush()
        mission = SortDateMission(
            sort_date=operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date_operation_id=operation.id,
            mission_type="departure",
            mission_source="manual",
            flight_number="UPS500",
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 17, 23, 30),
            planned_datetime_utc=datetime(2026, 8, 18, 4, 30),
            planned_source="manual",
            assigned_tail_number="N500UP",
            tail_source="manual",
            planned_fuel_load=50500,
            fuel_status="waiting",
            departure_status="loading",
        )
        db.session.add_all(
            [
                mission,
                SortDateTailState(
                    sort_date=operation.sort_date,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    tail_number="N500UP",
                    aircraft_type="A300",
                    aircraft_type_source="derived",
                ),
            ]
        )
        db.session.flush()
        return operation, mission

    def _configure_active_night_sort(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026, 8, 17, 22, 0
        )
        settings = SortTimelineSettings(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
        )
        db.session.add(settings)
        db.session.flush()
        db.session.add_all(
            [
                GatewaySortMatrix(
                    gateway_id=self.gateway.id,
                    gateway_code=self.gateway.code,
                    day_of_week="monday",
                    sort_name="night",
                    is_active=True,
                ),
                SortTimelineSortSetting(
                    timeline_settings=settings,
                    gateway_id=self.gateway.id,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    planning_start_local=time(18, 0),
                    sort_window_start_local=time(20, 0),
                    sort_window_end_local=time(4, 0),
                ),
            ]
        )

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
