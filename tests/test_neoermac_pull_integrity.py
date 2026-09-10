import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from flask import g
from sqlalchemy import event

from app.extensions import db
from app import create_app
from app.models import NeoErmacDoorPull, SortDateMission, SortDateOperation
from app.services.neoermac_pull_aggregation import recompute_current_sort_door_pull_aggregates
from tests.neoermac_pull_forms import pull_cards, pull_form
from tests import test_neoermac_routes as fixtures


class NeoErmacPullIntegrityTest(unittest.TestCase):
    _assign_lineup_destination = fixtures.NeoErmacRoutesTest._assign_lineup_destination
    _add_operation_departure = fixtures.NeoErmacRoutesTest._add_operation_departure
    _login_approved_user = fixtures.NeoErmacRoutesTest._login_approved_user
    tearDown = fixtures.NeoErmacRoutesTest.tearDown

    def setUp(self):
        fixtures.NeoErmacRoutesTest.setUp(self)
        self._assign_lineup_destination('green_runout', 'east_destination_1', 'SDF')
        self._assign_lineup_destination('green_runout', 'west_destination_1', 'SDF')
        self.mission = self._add_operation_departure('UPS501', 'SDF', tail='N501UP')
        self.operation = db.session.get(SortDateOperation, self.mission.sort_date_operation_id)
        db.session.commit()
        self._login_approved_user(role='operator')
        self.client.post('/neoermac/door-view/supervision', data={'doors': ['D1', 'D4'], 'active_door': 'D1'})

    def form(self, key='pure', value='01:45', door='D1', both=False):
        return pull_form(self.gateway, {'door': door, 'destination': 'SDF', 'pull_key': key,
                                       'actual_pull': value, 'no_pull': '0', 'apply_to_both': '1' if both else '0'})

    def post(self, form):
        g.__dict__.clear()
        db.session.expire_all()
        return self.client.post('/neoermac/door-view/pull-autosave', data=form)

    def test_repeated_destination_advances_rejects_old_identity_and_other_tail(self):
        second = self._add_operation_departure('UPS502', 'SDF', tail='N502UP',
                                              planned_datetime_local=datetime(2026, 6, 11, 3))
        db.session.commit()
        old = self.form()
        self.mission.departure_status = 'departed'
        db.session.commit()
        self.assertEqual(self.post(old).status_code, 409)
        self.assertEqual(NeoErmacDoorPull.query.count(), 0)
        fresh = self.form()
        self.assertEqual(fresh['mission_id'], second.id)
        self.assertEqual(self.post(fresh).status_code, 200)
        self.assertEqual(NeoErmacDoorPull.query.one().sort_date_mission_id, second.id)

    def test_prior_sort_and_forged_identity_are_rejected(self):
        original = self.form()
        for changed, status in (({'operation_id': 99999},409), ({'mission_id': 99999},409),
                                ({'door': 'D4'},409), ({'destination': 'OAK'},400), ({'original': ''},409)):
            with self.subTest(changed=changed):
                self.assertEqual(self.post({**original, **changed}).status_code, status)
                self.assertEqual(NeoErmacDoorPull.query.count(), 0)
        next_operation = SortDateOperation(gateway_id=self.gateway.id, gateway_code=self.gateway.code,
                                          sort_date=date(2026, 6, 12), sort_name='night')
        db.session.add(next_operation)
        db.session.commit()
        with patch('app.services.neoermac_door_view.current_door_view_operation', return_value=next_operation):
            self.assertEqual(self.post(original).status_code, 409)
        self.assertEqual(NeoErmacDoorPull.query.count(), 0)

    def test_both_sides_bind_same_mission_and_conflict_is_atomic(self):
        second = self._add_operation_departure('UPS502', 'SDF', tail='N502UP')
        db.session.commit()
        old_both = self.form(both=True)
        self.assertEqual(self.post(self.form(door='D4', value='01:50')).status_code, 200)
        self.assertEqual(self.post(old_both).status_code, 409)
        self.assertEqual(NeoErmacDoorPull.query.count(), 1)
        self.assertEqual(self.post(self.form(both=True)).status_code, 200)
        rows = NeoErmacDoorPull.query.order_by(NeoErmacDoorPull.door).all()
        self.assertEqual([(r.door, r.sort_date_mission_id, r.actual_pure_pull_time_local) for r in rows],
                         [('D1', self.mission.id, time(1, 45)), ('D4', self.mission.id, time(1, 45))])
        self.assertIsNone(second.actual_pure_pull_time_local)

    def test_stale_same_field_and_stale_blank_rejected_intentional_clear_works(self):
        old = self.form()
        blank = self.form(value='')
        self.assertEqual(self.post(self.form(value='01:50')).status_code, 200)
        self.assertEqual(self.post(old).status_code, 409)
        self.assertEqual(self.post(blank).status_code, 409)
        self.assertEqual(NeoErmacDoorPull.query.one().actual_pure_pull_time_local, time(1, 50))
        self.assertEqual(self.post(self.form(value='')).status_code, 200)
        self.assertIsNone(NeoErmacDoorPull.query.one().actual_pure_pull_time_local)

    def test_same_snapshot_independent_pure_mix_edits_survive_in_both_orders(self):
        pure, mix = self.form(), self.form('mix', '01:55')
        self.assertEqual(self.post(pure).status_code, 200)
        self.assertEqual(self.post(mix).status_code, 200)
        row = NeoErmacDoorPull.query.one()
        self.assertEqual((row.actual_pure_pull_time_local, row.actual_mix_pull_time_local), (time(1,45), time(1,55)))
        pure, mix = self.form(value='01:46'), self.form('mix', '01:56')
        self.assertEqual(self.post(mix).status_code, 200)
        self.assertEqual(self.post(pure).status_code, 200)
        self.assertEqual((row.actual_pure_pull_time_local, row.actual_mix_pull_time_local), (time(1,46), time(1,56)))

    def test_two_connections_serialize_first_creation_and_preserve_independent_fields(self):
        # Real independent connections, not two calls sharing an ORM identity
        # map. SQLite exercises the same write-reservation ordering locally.
        cookie = self.client.get_cookie('session').value
        forms = [self.form(), self.form('mix', '01:55')]
        db.session.commit()
        for same_field in (False, True):
            with self.subTest(same_field=same_field), TemporaryDirectory() as directory:
                path = Path(directory) / 'pulls.sqlite'
                with db.engine.connect() as connection, closing(sqlite3.connect(path)) as target:
                    connection.connection.driver_connection.backup(target)
                config = type('ConcurrentPullConfig', (), {
                    'SECRET_KEY': 'test', 'TESTING': True,
                    'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + path.as_posix(),
                    'SQLALCHEMY_TRACK_MODIFICATIONS': False,
                    'CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE': datetime(2026, 6, 11, 1),
                })
                app = create_app(config, auto_bootstrap=False)
                barrier = Barrier(2)
                def submit(form):
                    client = app.test_client()
                    client.set_cookie('session', cookie)
                    barrier.wait(timeout=10)
                    return client.post('/neoermac/door-view/pull-autosave', data=form).status_code
                second = {**forms[0], 'actual_pull': '01:46'} if same_field else forms[1]
                try:
                    with ThreadPoolExecutor(max_workers=2) as workers:
                        statuses = list(workers.map(submit, [forms[0], second]))
                    self.assertEqual(sorted(statuses), [200,409] if same_field else [200,200])
                    with app.app_context():
                        row = NeoErmacDoorPull.query.one()
                        if not same_field:
                            self.assertEqual((row.actual_pure_pull_time_local, row.actual_mix_pull_time_local),
                                             (time(1,45), time(1,55)))
                finally:
                    with app.app_context():
                        db.session.remove()
                        db.engine.dispose()

    def legacy(self, value=time(2, 0)):
        row = NeoErmacDoorPull(gateway_id=self.gateway.id, sort_date_operation_id=self.operation.id,
                              door='D4', destination='SDF', actual_pure_pull_time_local=value)
        db.session.add(row)
        db.session.commit()
        return row

    def test_ambiguous_legacy_is_excluded_even_when_only_active_mission_is_recomputed(self):
        self._add_operation_departure('UPS502', 'SDF', tail='N502UP')
        self.legacy()
        self.assertEqual(self.post(self.form()).status_code, 200)
        self.assertEqual(self.mission.actual_pure_pull_time_local, time(1,45))

    def test_unambiguous_legacy_contributes_and_promotion_preserves_other_field(self):
        self.legacy()
        self.assertEqual(self.post(self.form()).status_code, 200)
        self.assertEqual(self.mission.actual_pure_pull_time_local, time(2,0))
        self.assertEqual(self.post(self.form('mix', '01:55', door='D4')).status_code, 200)
        canonical = NeoErmacDoorPull.query.filter_by(door='D4', sort_date_mission_id=self.mission.id).one()
        self.assertEqual(canonical.actual_pure_pull_time_local, time(2,0))

    def test_canonical_wins_even_when_legacy_timestamp_is_newer(self):
        self.assertEqual(self.post(self.form(door='D4')).status_code, 200)
        self.legacy()
        recompute_current_sort_door_pull_aggregates(self.gateway, operation=self.operation)
        self.assertEqual(self.mission.actual_pure_pull_time_local, time(1,45))

    def test_full_form_unchanged_blank_does_not_clear_newer_other_field(self):
        full = pull_form(self.gateway, {'action':'save_pulls', 'door':'D1', 'destination_count':'1',
                                        'destination_0':'SDF', 'actual_pure_0':'01:45', 'actual_mix_0':''})
        self.assertEqual(self.post(self.form('mix', '01:55')).status_code, 200)
        response = self.client.post('/neoermac/door-view', data=full)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(NeoErmacDoorPull.query.one().actual_mix_pull_time_local, time(1,55))

    def test_refresh_has_no_writes_and_supplies_same_signed_field_snapshot(self):
        before = self.form()
        writes = []
        def capture(_conn, _cursor, statement, *_args):
            if statement.lstrip().split()[0].upper() in ('INSERT','UPDATE','DELETE'):
                writes.append(statement)
        event.listen(db.engine, 'before_cursor_execute', capture)
        try:
            response = self.client.get('/neoermac/door-view/state?door=D1&revision=old')
        finally:
            event.remove(db.engine, 'before_cursor_execute', capture)
        self.assertEqual(response.status_code, 200)
        card = response.get_json()['state']['destinations'][0]
        self.assertEqual(card['original']['pure'], before['original'])
        self.assertEqual(writes, [])

    def test_full_form_stale_clear_rolls_back_other_fields(self):
        self.assertEqual(self.post(self.form(value='01:40')).status_code, 200)
        full = pull_form(self.gateway, {'action':'save_pulls', 'door':'D1', 'destination_count':'1',
                                        'destination_0':'SDF', 'actual_pure_0':'', 'actual_mix_0':'01:55'})
        self.assertEqual(self.post(self.form(value='01:50')).status_code, 200)
        response = self.client.post('/neoermac/door-view', data=full)
        self.assertEqual(response.status_code, 409)
        row = NeoErmacDoorPull.query.one()
        self.assertEqual(row.actual_pure_pull_time_local, time(1,50))
        self.assertIsNone(row.actual_mix_pull_time_local)
