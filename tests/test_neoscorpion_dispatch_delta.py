"""Canonical Dispatcher transport deltas, without cached operational authority."""
import json
import re
import unittest
from unittest.mock import patch
from sqlalchemy import event
from app.extensions import db
from app.services.neoscorpion_dispatch_fragments import _digest
from tests import test_dispatch_refresh_fragments as fixtures

PART = re.compile(r'<!--neo-dispatch:([\w-]+):([a-f0-9]{32}):(-|[a-f0-9]{64})-->')


def fingerprints(html):
    full, critical = {}, {}
    for key, digest, small in PART.findall(html):
        full[key] = digest
        if small != '-': critical[key.removeprefix('mission-')] = small
    return full, critical


class DispatchDeltaTest(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.DispatchFragmentsTest(); self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.ready()

    def initial(self):
        response = self.case.client.get('/neoscorpion/fuel-dispatch')
        self.assertEqual(response.status_code, 200)
        full, critical = fingerprints(response.text)
        self.assertIn('mission-' + str(self.case.mission.id), full)
        self.assertIn(str(self.case.mission.id), critical)
        return full, critical

    def delta(self, full=None, critical=None, **query):
        return self.case.client.get('/neoscorpion/fuel-dispatch/live-panel',
            query_string=dict(missions_delta='1', **query), headers={
                'X-Neo-Dispatch-Parts':json.dumps(full or {}),
                'X-Neo-Dispatch-Fingerprints':json.dumps(critical or {})})

    def test_initial_snapshot_seeds_critical_fingerprints_without_another_request(self):
        full, critical = self.initial()
        response = self.case.client.get('/neoscorpion/fuel-dispatch/live-panel?completion_only=1&delta=1',
            headers={'X-Neo-Dispatch-Fingerprints':json.dumps(critical)})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('data-dispatch-mission-id', response.json['html'])
        self.assertNotIn('data-assets-workspace', response.json['html'])
        self.assertEqual(set(response.json['mission_manifest']), {str(self.case.mission.id)})

    def test_identical_full_snapshot_reuses_missions_and_assets(self):
        full, critical = self.initial()
        response = self.delta(full, critical)
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-dispatch-reuse="mission-', response.json['html'])
        self.assertIn('data-dispatch-reuse="assets"', response.json['html'])
        self.assertNotIn('data-dispatch-mission-id', response.json['html'])

    def test_changed_mission_and_added_mission_are_full_canonical_groups(self):
        full, critical = self.initial()
        self.case.mission.assigned_tail_number = 'N999UP'
        new = self.case.fixture._mission('UPS777', 'N123UP', 30000, 25)
        db.session.commit()
        response = self.delta(full, critical)
        self.assertEqual(response.status_code, 200)
        self.assertIn('N999UP', response.json['html'])
        self.assertIn('UPS777', response.json['html'])
        self.assertIn('data-dispatch-sort-group="' + str(new.id) + '"', response.json['html'])

    def test_wrong_sort_and_revoked_view_return_no_operational_fragments(self):
        full, critical = self.initial()
        response = self.delta(full, critical, operation_id=self.case.operation.id + 1)
        self.assertEqual(response.status_code, 409)
        with patch('app.neonodes.neoscorpion.routes.permission_access',return_value={'can_view':False,'can_edit':False}):
            response = self.delta(full, critical)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('html', response.json)

    def test_stale_operation_rejects_before_optional_planning_or_rendering(self):
        with patch('app.services.neoscorpion.build_spear_plan',side_effect=AssertionError('No stale planning')):
            response = self.delta(operation_id=self.case.operation.id + 1)
        self.assertEqual(response.status_code,409)
        self.assertNotIn('html',response.json)

    def test_deleted_mission_and_reordering_have_complete_authoritative_manifest(self):
        c = self.case
        extra = c.fixture._mission('UPS777','N123UP',30000,25)
        c.fixture._mission('UPS778','N124UP',30000,26)
        db.session.commit(); extra_id = str(extra.id)
        full, critical = self.initial()
        old = self.delta(full,critical).json['mission_manifest']
        from datetime import timedelta
        extra.planned_datetime_utc += timedelta(hours=2)
        db.session.commit()
        result = self.delta(full,critical).json
        self.assertEqual(set(result['mission_manifest']),set(old))
        self.assertNotEqual(result['mission_manifest'][extra_id],old[extra_id])
        db.session.delete(extra);db.session.commit()
        for query in ({'missions_delta':'1'},{'completion_only':'1','delta':'1'}):
            result = c.client.get('/neoscorpion/fuel-dispatch/live-panel',query_string=query).json
            self.assertEqual(set(result['mission_manifest']),set(old)-{extra_id})
            self.assertNotIn('mission-'+extra_id,result['html'])

    def test_only_transport_renewals_and_explicit_order_metadata_are_normalized(self):
        old = '<tr data-dispatch-etd-rank="1"><input name="spear_decision_reference" value="old"><input name="csrf_token" value="old"><button>COMPLETE</button></tr>'
        new = old.replace('rank="1"','rank="2"').replace('value="old"','value="new"')
        self.assertEqual(_digest(old,'same-user-sort','mission-1'),_digest(new,'same-user-sort','mission-1'))
        self.assertNotEqual(_digest(old,'same-user-sort','mission-1'),_digest(old,'another-user-sort','mission-1'))
        self.assertNotEqual(_digest(old,'same-user-sort','mission-1'),_digest(new.replace('COMPLETE','OFF'),'same-user-sort','mission-1'))

    def test_edit_revocation_never_reuses_previously_writable_controls(self):
        full, critical = self.initial()
        with patch('app.neonodes.neoscorpion.routes.permission_access',return_value={'can_view':True,'can_edit':False}):
            response = self.delta(full, critical)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json['can_edit'])
        self.assertNotIn('data-dispatch-reuse="mission-', response.json['html'])
        self.assertNotIn('>COMPLETE</button>', response.json['html'])

    def test_invalid_or_foreign_fingerprints_fall_back_to_complete_authorized_markup(self):
        for raw in ('[]', 'null', 'not json', 'x' * 7000, '{"mission-1":123}'):
            response = self.case.client.get('/neoscorpion/fuel-dispatch/live-panel?missions_delta=1',
                headers={'X-Neo-Dispatch-Parts':raw})
            self.assertEqual(response.status_code, 200)
            self.assertIn('data-dispatch-mission-id', response.json['html'])
        full, critical = self.initial()
        self.case.login(self.case.fueler)
        response = self.delta(full, critical)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('data-dispatch-reuse="mission-', response.json['html'])

    def test_critical_seed_does_not_add_database_roundtrips(self):
        statements = []
        def record(c, cur, sql, *args): statements.append(sql.lstrip().split()[0].upper())
        event.listen(db.engine, 'before_cursor_execute', record)
        try:
            self.delta(); baseline = statements.count('SELECT'); statements.clear()
            full, critical = self.initial(); initial = statements.count('SELECT'); statements.clear()
            response = self.delta(full, critical)
            self.assertEqual(response.status_code, 200)
            self.assertLessEqual(statements.count('SELECT'), baseline)
            self.assertEqual(sum(statements.count(k) for k in ('INSERT','UPDATE','DELETE')), 0)
        finally: event.remove(db.engine, 'before_cursor_execute', record)


if __name__ == '__main__': unittest.main()
