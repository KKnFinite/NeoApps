"""Fresh, locked Conductor request budgets; no production database required."""
import unittest
from contextlib import nullcontext
from datetime import date, datetime, time
from unittest.mock import patch

from sqlalchemy import event, select

from app.extensions import db
from app.models import SortDateMission, User
from app.services import gateway_matrix
from app.services.neosektor_live_counts import NeoSektorOperationalStateBundle
from app.services.request_cache import MISSING, get_request_cached
from tests import test_neosektor_routes as fixtures


class ConductorMutationQueriesTest(unittest.TestCase):
    def workflow(self, *, reuse=True, active=False):
        fixture = fixtures.NeoSektorRoutesTest()
        fixture.setUp()
        try:
            fixture._login_approved_user(role='simulator')
            # Chicago's previous-date Night must survive midnight in both runs.
            day = date(2026, 9, 9)
            operation = fixture._add_sort_operation(day, 'night')
            fixture._set_sort_window('night', time(22), time(2))
            fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026, 9, 10, 0, 30)
            if active:
                operation.generated_by_user_id = User.query.first().id
                db.session.add(SortDateMission(sort_date_operation=operation, sort_date=day,
                    gateway_code=fixture.gateway.code, sort_name='night', mission_type='arrival',
                    mission_source='manual', wave='1', flight_number='TEST1', origin='OAK', destination='RFD',
                    actual_block_in_datetime_utc=datetime(2026, 9, 10, 4)))
                db.session.commit()
            self.assertEqual(fixture.client.post('/neosektor/tunnel-conductor/wave',
                json={'wave': 'first', 'value': 10}).status_code, 200)
            old_order = ['Bay 5', 'Bay 4', 'Bay 3', 'Bay 2', 'Bay 1']
            new_order = ['Bay 2', 'Bay 5', 'Bay 1', 'Bay 3', 'Bay 4']
            cases = [
                ('override_west', 'settings', {'first_override': 'west'}, 200),
                ('override_noop', 'settings', {'first_override': 'west'}, 200),
                ('override_auto', 'settings', {'first_override': 'auto'}, 200),
                ('override_east', 'settings', {'second_override': 'east'}, 200),
                ('bay_disable', 'settings', {'bay_priority_enabled': {'Bay 2': False}}, 200),
                ('bay_disable_noop', 'settings', {'bay_priority_enabled': {'Bay 2': False}}, 200),
                ('bay_enable', 'settings', {'bay_priority_enabled': {'Bay 2': True}}, 200),
                ('bay_status', 'ballmat', {'side': 'east', 'bay_statuses': {'Bay 2': 'Overflowing'}}, 200),
                ('bay_status_noop', 'ballmat', {'side': 'east', 'bay_statuses': {'Bay 2': 'Overflowing'}}, 200),
                ('back_pickup', 'ballmat', {'side': 'east', 'back_pickups': {'Bay 2': True}}, 200),
                ('back_pickup_noop', 'ballmat', {'side': 'east', 'back_pickups': {'Bay 2': True}}, 200),
                ('back_pickup_invalid', 'ballmat', {'side': 'east', 'back_pickups': {'Bay 1': True}}, 400),
                ('back_pickup_clear', 'ballmat', {'side': 'east', 'bay_statuses': {'Bay 2': 'Full'}}, 200),
                ('priority', 'discharge-controls', {'action': 'priority', 'order': new_order, 'expected_order': old_order}, 200),
                ('priority_noop', 'discharge-controls', {'action': 'priority', 'order': new_order, 'expected_order': new_order}, 200),
                ('priority_stale', 'discharge-controls', {'action': 'priority', 'order': old_order, 'expected_order': old_order}, 409),
                ('priority_invalid', 'discharge-controls', {'action': 'priority', 'order': ['Bay 1'], 'expected_order': new_order}, 400),
                ('cut_on', 'discharge-controls', {'action': 'cut', 'enabled': True, 'expected_cut': False}, 200),
                ('cut_noop', 'discharge-controls', {'action': 'cut', 'enabled': True, 'expected_cut': True}, 200),
                ('cut_stale', 'discharge-controls', {'action': 'cut', 'enabled': False, 'expected_cut': False}, 409),
                ('cut_off', 'discharge-controls', {'action': 'cut', 'enabled': False, 'expected_cut': True}, 200),
                ('modifiers', 'settings', {'first_modifier': 48, 'second_modifier': 39, 'down_timer_minutes': 12}, 200),
                ('modifiers_noop', 'settings', {'first_modifier': 48, 'second_modifier': 39, 'down_timer_minutes': 12}, 200),
                ('offset', 'offset', {'west_offset': 3}, 200),
                ('offset_noop', 'offset', {'west_offset': 3}, 200),
                ('wave', 'wave', {'wave': 'first', 'value': 12}, 200),
                ('wave_noop', 'wave', {'wave': 'first', 'value': 12}, 200),
                ('invalid_side', 'ballmat', {'side': 'unknown'}, 400),
            ]
            original_get = gateway_matrix.get_request_cached

            def without_reuse(namespace, key):
                return MISSING if namespace == 'neosektor.locked_operation_scope' else original_get(namespace, key)

            def stored():
                db.session.expire_all()
                return {table.name: [tuple(row) for row in db.session.execute(select(table).order_by(table.c.id))]
                        for table in db.metadata.sorted_tables if table.name.startswith('neosektor_')}

            results = {}
            for name, endpoint, command, status in cases:
                before = stored()
                sql, commits, rollbacks = [], [], []
                def capture(conn, cursor, statement, parameters, context, many):
                    sql.append(' '.join(statement.lower().split()))
                def committed(conn):
                    commits.append(True)
                def rolled_back(conn):
                    rollbacks.append(True)
                # Separate app context includes fresh authentication and ORM state.
                with fixture.app.app_context():
                    engine = db.engine
                    event.listen(engine, 'before_cursor_execute', capture)
                    event.listen(engine, 'commit', committed)
                    event.listen(engine, 'rollback', rolled_back)
                    try:
                        with (nullcontext() if reuse else patch.object(
                                gateway_matrix, 'get_request_cached', side_effect=without_reuse)):
                            response = fixture.client.post('/neosektor/tunnel-conductor/' + endpoint, json=command)
                    finally:
                        event.remove(engine, 'before_cursor_execute', capture)
                        event.remove(engine, 'commit', committed)
                        event.remove(engine, 'rollback', rolled_back)
                self.assertEqual(response.status_code, status, (name, response.json))
                self.assertEqual(len(commits), int(status == 200), name)
                after = stored()
                if status != 200:
                    self.assertEqual(after, before, name)
                    if name != 'invalid_side':
                        self.assertTrue(rollbacks, name)
                writes = [s for s in sql if s.startswith(('update ', 'insert ', 'delete '))]
                counts = tuple(sum(s.startswith(v + ' ') for s in sql) for v in ('select', 'insert', 'update', 'delete'))
                if status in (200, 409):
                    self.assertEqual(response.json['state']['summary']['sort_date'], day.isoformat())
                    if active:
                        self.assertEqual(response.json['state']['routing']['routes']['first']['display_state'], 'route')
                results[name] = (counts, response.json, sql, writes, after)
            return results
        finally:
            fixture.tearDown()

    def test_locked_operation_reuse_preserves_state_writes_and_conflicts(self):
        self.compare_workflow(active=False)

    def test_active_block_in_routing_keeps_mission_reads_and_identical_writes(self):
        self.compare_workflow(active=True)

    def compare_workflow(self, *, active):
        before = self.workflow(reuse=False, active=active)
        after = self.workflow(reuse=True, active=active)
        candidate_query = 'from sort_date_operations where sort_date_operations.gateway_code ='
        def without_timestamps(state):
            return {table: [tuple('<timestamp>' if isinstance(v, datetime) else v for v in row)
                            for row in rows] for table, rows in state.items()}
        for name, (counts, payload, sql, writes, stored) in before.items():
            with self.subTest(path=name):
                new_counts, new_payload, new_sql, new_writes, new_stored = after[name]
                early_failure = name in ('invalid_side', 'back_pickup_invalid', 'priority_invalid')
                baseline = 7 if name == 'invalid_side' else 16 if early_failure else 23 + active
                self.assertEqual(counts[0], baseline)
                self.assertEqual(new_counts, (baseline - (not early_failure), *counts[1:]))
                self.assertEqual(counts[1], 0)
                self.assertEqual(counts[3], 0)
                update_budget = (0 if name == 'invalid_side' else 1 if early_failure or name.endswith('_stale')
                                 else 2 if name.endswith('_noop') else 4 if name == 'back_pickup_clear' else 3)
                self.assertEqual(counts[2], update_budget)
                self.assertEqual(new_writes, writes)  # Exact tables AND columns unchanged.
                self.assertEqual(new_payload, payload)
                self.assertEqual(without_timestamps(new_stored), without_timestamps(stored))
                if name != 'invalid_side':
                    lock = new_sql.index('update gateways set id=id where id=?')
                    candidates = [i for i, s in enumerate(new_sql) if candidate_query in s]
                    self.assertEqual(len(candidates), 1)
                    self.assertLess(lock, candidates[0])
                    if not early_failure:
                        old_candidates = [s for s in sql if candidate_query in s]
                        self.assertEqual(len(old_candidates), 2)
                        self.assertEqual(old_candidates[0], old_candidates[1])
                        # Only this duplicate SELECT was removed, not permission,
                        # mission, lifecycle, settings or authoritative state reads.
                        expected_sql = list(sql)
                        del expected_sql[max(i for i, s in enumerate(sql) if candidate_query in s)]
                        self.assertEqual(new_sql, expected_sql)
                print(f'{name} (active={active}): SELECT/INSERT/UPDATE/DELETE {counts} -> {new_counts}')

    def test_reuse_is_opt_in_after_lock_and_cleared_at_transaction_boundaries(self):
        fixture = fixtures.NeoSektorRoutesTest()
        fixture.setUp()
        try:
            scope = (fixture.gateway.id, fixture.gateway.code)
            for finish in ('commit', 'rollback'):
                with self.subTest(finish=finish), fixture.app.test_request_context(
                        '/neosektor/tunnel-conductor/settings', method='POST'):
                    self.assertIs(get_request_cached('neosektor.locked_operation_scope', scope), MISSING)
                    NeoSektorOperationalStateBundle.load(fixture.gateway, for_update=True, include_routing=True)
                    self.assertIs(get_request_cached('neosektor.locked_operation_scope', scope), True)
                    getattr(db.session, finish)()
                    self.assertIs(get_request_cached('neosektor.locked_operation_scope', scope), MISSING)
            for path, method in (('/neosektor/tunnel-conductor/state', 'GET'),
                                 ('/neosektor/tunnel-conductor/settings', 'POST')):
                with fixture.app.test_request_context(path, method=method):
                    NeoSektorOperationalStateBundle.load(fixture.gateway, initialize=False, include_routing=True)
                    self.assertIs(get_request_cached('neosektor.locked_operation_scope', scope), MISSING)
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
