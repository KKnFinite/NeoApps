import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from flask import g
from sqlalchemy import event
from app.extensions import db
from app import create_app
from app.models import MasterFlightSchedule, NeoErmacBuildingLineup, SortDateMission
from app.services.neoermac_dashboard import upcoming_pulls_revision
from app.services.neoermac_door_view import door_view_poll_revision, door_view_operational_state, _destination_cards_for_door
from tests import test_neoermac_routes as fixtures
from tests.neoermac_lineup_forms import lineup_form


class NeoErmacLiveIntegrityTest(unittest.TestCase):
    _add_master_departure = fixtures.NeoErmacRoutesTest._add_master_departure
    _add_operation_departure = fixtures.NeoErmacRoutesTest._add_operation_departure
    _assign_lineup_destination = fixtures.NeoErmacRoutesTest._assign_lineup_destination
    _login_approved_user = fixtures.NeoErmacRoutesTest._login_approved_user
    tearDown = fixtures.NeoErmacRoutesTest.tearDown

    def setUp(self):
        fixtures.NeoErmacRoutesTest.setUp(self)
        self._add_master_departure('UPS501', 'SDF')
        self.master = MasterFlightSchedule.query.filter_by(flight_number='UPS501').one()
        self._add_master_departure('UPS502', 'ONT')
        self._add_master_departure('UPS503', 'PHX')
        self.mission = self._add_operation_departure('UPS501', 'SDF', tail='N501UP')
        self._assign_lineup_destination('green_runout', 'east_destination_1', 'SDF')
        db.session.commit()
        self._login_approved_user(role='simulator')
        self.client.get('/neoermac/building-lineup')
        self.field = 'lineup_green_runout_east_destination_1'
        self.other = 'lineup_green_runout_west_destination_1'

    def post(self, form):
        g.__dict__.clear()
        db.session.expire_all()
        return self.client.post('/neoermac/building-lineup/destination', data=form)

    def form(self, destination, field=None):
        return lineup_form(self.gateway, {'field': field or self.field, 'destination': destination})

    def test_same_slot_conflict_clear_and_independent_slot(self):
        older, clear = self.form('PHX'), self.form('')
        independent = self.form('PHX', self.other)
        self.assertEqual(self.post(self.form('ONT')).status_code, 200)
        self.assertEqual(self.post(older).status_code, 409)
        self.assertEqual(self.post(clear).status_code, 409)
        self.assertEqual(self.post(independent).status_code, 200)
        row = NeoErmacBuildingLineup.query.filter_by(runout_key='green_runout').one()
        self.assertEqual((row.east_destination_1, row.west_destination_1), ('ONT', 'PHX'))
        self.assertEqual(self.post(self.form('')).status_code, 200)
        self.assertIsNone(row.east_destination_1)

    def test_full_form_only_changed_slots_and_atomic_conflict(self):
        old = lineup_form(self.gateway, {self.field: 'SDF', self.other: 'PHX'})
        self.assertEqual(self.post(self.form('ONT')).status_code, 200)
        self.assertEqual(self.client.post('/neoermac/building-lineup', data=old).status_code, 302)
        row = NeoErmacBuildingLineup.query.filter_by(runout_key='green_runout').one()
        self.assertEqual(row.east_destination_1, 'ONT')
        old[self.field] = ''
        self.assertEqual(self.client.post('/neoermac/building-lineup', data=old).status_code, 409)
        self.assertEqual(row.east_destination_1, 'ONT')

    def test_missing_or_wrong_slot_original_rejected(self):
        form = self.form('ONT')
        form['field'] = self.other
        self.assertEqual(self.post(form).status_code, 409)
        self.assertEqual(self.post({'field': self.field, 'destination': ''}).status_code, 409)

    def test_retired_master_choice_is_not_rendered_as_an_intentional_blank(self):
        self.master.active = False
        db.session.commit()
        response = self.client.get('/neoermac/building-lineup')
        self.assertIn(b'<option value="SDF" selected>SDF</option>', response.data)
        form = lineup_form(self.gateway, {self.field: 'SDF', self.other: 'ONT'})
        self.assertEqual(self.client.post('/neoermac/building-lineup', data=form).status_code, 302)
        row = NeoErmacBuildingLineup.query.filter_by(runout_key='green_runout').one()
        self.assertEqual((row.east_destination_1, row.west_destination_1), ('SDF', 'ONT'))

    def test_two_connections_serialize_same_slot_but_preserve_independent_slots(self):
        cookie = self.client.get_cookie('session').value
        forms = [self.form('ONT'), self.form('PHX', self.other)]
        db.session.commit()
        for same_slot in (True, False):
            with self.subTest(same_slot=same_slot), TemporaryDirectory() as directory:
                path = Path(directory) / 'lineup.sqlite'
                with db.engine.connect() as connection, closing(sqlite3.connect(path)) as target:
                    connection.connection.driver_connection.backup(target)
                config = type('ConcurrentLineupConfig', (), {
                    'SECRET_KEY': 'test', 'TESTING': True,
                    'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + path.as_posix(),
                    'SQLALCHEMY_TRACK_MODIFICATIONS': False,
                    'CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE': datetime(2026, 6, 11, 1),
                })
                app = create_app(config, auto_bootstrap=False)
                barrier = Barrier(2)
                def submit(form):
                    client = app.test_client(); client.set_cookie('session', cookie)
                    barrier.wait(timeout=10)
                    return client.post('/neoermac/building-lineup/destination', data=form).status_code
                second = {**forms[0], 'destination': 'PHX'} if same_slot else forms[1]
                try:
                    with ThreadPoolExecutor(max_workers=2) as workers:
                        statuses = list(workers.map(submit, [forms[0], second]))
                    self.assertEqual(sorted(statuses), [200, 409] if same_slot else [200, 200])
                    with app.app_context():
                        row = NeoErmacBuildingLineup.query.filter_by(runout_key='green_runout').one()
                        if not same_slot:
                            self.assertEqual((row.east_destination_1, row.west_destination_1), ('ONT', 'PHX'))
                finally:
                    with app.app_context():
                        db.session.remove(); db.engine.dispose()

    def test_lineup_master_dependency_and_irrelevant_arrival(self):
        def revision():
            return upcoming_pulls_revision(self.gateway, include_lineup_choices=True)
        before = revision()
        # Different master rows of the same operation-independent choice source.
        self.master.destination = 'LAX'
        db.session.commit()
        self.assertNotEqual(before, revision())
        response = self.client.get('/neoermac/building-lineup/state?revision=' + before).get_json()
        self.assertTrue(response['changed'])
        self.assertIn('LAX', response['state']['destination_choices'])
        self.assertNotIn('SDF', response['state']['destination_choices'])
        current = revision()
        self._add_master_departure('UPS900', 'ORD')
        other = MasterFlightSchedule.query.filter_by(flight_number='UPS900').one()
        other.mission_type = 'arrival'
        db.session.commit()
        self.assertEqual(current, revision())

    def test_assumed_arrival_boundary_changes_revision_not_clock_ticks(self):
        from app.models import SortDateOperation
        operation = db.session.get(SortDateOperation, self.mission.sort_date_operation_id)
        boundary = datetime(2026, 6, 11, 5, 30)
        arrival = SortDateMission(sort_date_operation_id=operation.id, gateway_code=self.gateway.code,
            sort_date=operation.sort_date, sort_name=operation.sort_name,
            mission_type='arrival', flight_number='UPS101', origin='SDF', destination=self.gateway.code,
            assigned_tail_number='N501UP', planned_datetime_utc=boundary - timedelta(hours=1),
            planned_datetime_local=boundary - timedelta(hours=6), api_assumed_arrived_time_utc=boundary)
        db.session.add(arrival); db.session.commit()
        def revision(now):
            return door_view_poll_revision(self.gateway, 'D1', 1, operation=operation, now=now)
        before = revision(boundary - timedelta(seconds=1))
        self.assertEqual(before, revision(boundary - timedelta(minutes=1)))
        after = revision(boundary)
        self.assertNotEqual(before, after)
        self.assertEqual(after, revision(boundary + timedelta(minutes=1)))
        self.assertEqual(after, revision(boundary.replace(tzinfo=timezone.utc)))
        for now, present in ((boundary - timedelta(seconds=1), False), (boundary, True)):
            bundle = door_view_operational_state(self.gateway, operation=operation, initialize_lineup=False)
            with patch('app.services.neoermac_tail_presence.datetime') as clock:
                clock.now.return_value = now.replace(tzinfo=timezone.utc)
                cards = _destination_cards_for_door(self.gateway, 'D1', operation, bundle=bundle)
            self.assertEqual(cards[0]['tail_presence']['is_present'], present)

    def test_lineup_state_is_bounded_read_only_and_unchanged_is_lightweight(self):
        statements = []
        def capture(c, cursor, statement, *args): statements.append(statement)
        g.__dict__.clear(); db.session.expire_all()
        event.listen(db.engine, 'before_cursor_execute', capture)
        try:
            response = self.client.get('/neoermac/building-lineup/state?revision=old')
        finally:
            event.remove(db.engine, 'before_cursor_execute', capture)
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload['state']['slots']), 96)
        self.assertEqual(payload['state']['slots'][self.field]['destination'], 'SDF')
        self.assertLess(len(response.data), 22000)
        self.assertFalse(any(s.lstrip().lower().startswith(('insert', 'update', 'delete')) for s in statements))
        self.assertLessEqual(sum(s.lstrip().lower().startswith('select') for s in statements), 20)
        with patch('app.neonodes.neoermac.routes.building_lineup_state_payload') as build:
            unchanged = self.client.get('/neoermac/building-lineup/state?revision=' + payload['revision']).get_json()
        self.assertFalse(unchanged['changed']); self.assertNotIn('state', unchanged); build.assert_not_called()

    def test_door_changed_state_contains_membership_fragment_without_shell_or_writes(self):
        statements = []
        def capture(c, cursor, statement, *args): statements.append(statement)
        event.listen(db.engine, 'before_cursor_execute', capture)
        try:
            response = self.client.get('/neoermac/door-view/state?door=D1&revision=old')
        finally:
            event.remove(db.engine, 'before_cursor_execute', capture)
        self.assertEqual(response.status_code, 200)
        html = response.get_json()['state']['pull_content_html']
        self.assertIn('data-door-destination="SDF"', html)
        self.assertIn('data-pull-mission', html)
        self.assertNotIn('<script', html); self.assertNotIn('<html', html)
        self.assertFalse(any(s.lstrip().lower().startswith(('insert', 'update', 'delete')) for s in statements))
