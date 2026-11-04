"""Fresh private authority and equivalent operational rendering, less SQL."""
from unittest.mock import patch
import unittest

from sqlalchemy import event
from flask import g
from app.extensions import db
from app.models import GatewayNodeRole, GatewayMembership, NeoNode
from app.services import neoscorpion as service
from app.services import operational_request_policy as policy
from tests import test_dispatch_refresh_fragments as fixtures
from tests import test_neoscorpion_spear_learning_capture as learning_fixtures


class Phase2LiveTest(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.DispatchFragmentsTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.ready()

    def test_observe_panels_are_identical_without_reloading_learning_review(self):
        original = service._attach_fueling_timing
        def review(*args, **kwargs):
            kwargs['review_learning'] = True
            return original(*args, **kwargs)
        for screen, actor in (('fueler', self.case.fueler), ('fueling-board', self.case.dispatcher),
                              ('truck-driver', self.case.dispatcher), ('hanzo', self.case.dispatcher)):
            with self.subTest(screen=screen):
                self.case.login(actor)
                path = '/neoscorpion/' + screen + '/live-panel'
                with patch.object(service, 'load_observation_jobs', wraps=service.load_observation_jobs) as history:
                    optimized = self.case.client.get(path)
                self.assertEqual(optimized.status_code, 200)
                history.assert_not_called()
                with patch.object(service, '_attach_fueling_timing', side_effect=review):
                    reviewed = self.case.client.get(path)
                self.assertEqual(optimized.json['panel_digest'], reviewed.json['panel_digest'])

    def test_apply_keeps_verified_learning_and_spear_observe_keeps_review(self):
        self.case.settings.spear_live_calibration_mode = 'apply'
        db.session.commit()
        self.case.login(self.case.fueler)
        with patch.object(service, 'load_observation_jobs', wraps=service.load_observation_jobs) as history:
            self.assertEqual(self.case.client.get('/neoscorpion/fueler/live-panel').status_code, 200)
        history.assert_called_once()
        self.case.settings.spear_live_calibration_mode = 'observe'
        db.session.commit()
        self.case.login(self.case.dispatcher)
        with patch.object(service, 'load_observation_jobs', wraps=service.load_observation_jobs) as history:
            self.assertEqual(self.case.client.get('/neoscorpion/fuel-dispatch/live-panel?spear_only=1').status_code, 200)
        history.assert_called_once()

    def test_new_private_read_paths_enforce_revocation_on_every_request(self):
        self.case.login(self.case.fueler)
        paths = ('fueler/live-panel', 'fueling-board/live-panel', 'truck-driver/live-panel', 'hanzo/live-panel',
                 'fueling-board/revision', 'truck-driver/revision', 'hanzo/revision')
        for path in paths:
            self.assertEqual(self.case.client.get('/neoscorpion/' + path).status_code, 200, path)
        node = NeoNode.query.filter_by(code='scorpion').one()
        membership = GatewayMembership.query.filter_by(user_id=self.case.fueler.id,
            gateway_id=self.case.gateway.id).one()
        grant = GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id, node_id=node.id).one()
        grant.is_active = False
        db.session.commit()
        for path in paths:
            with self.subTest(path=path):
                result = self.case.client.get('/neoscorpion/' + path,
                    headers={'X-Neo-Panel-Digest': '0' * 64})
                self.assertIn(result.status_code, (302, 403))
                self.assertNotIn('My Fuel Assignments', result.get_data(as_text=True))

    def test_input_reuse_is_get_local_and_transaction_invalidated(self):
        def statements():
            return service._effective_aircraft_fuel_configuration(self.case.gateway.id)
        queries = []
        def record(c, cur, sql, *args):
            if sql.lstrip().upper().startswith('SELECT') and 'neoscorpion_aircraft_fuel_settings' in sql:
                queries.append(sql)
        event.listen(db.engine, 'before_cursor_execute', record)
        self.addCleanup(event.remove, db.engine, 'before_cursor_execute', record)
        with self.case.client.application.test_request_context('/', method='GET'):
            service.assignment_planning_settings(self.case.gateway, settings=self.case.settings)
            statements()
            self.assertEqual(len(queries), 1)
            db.session.rollback()
            statements()
            self.assertEqual(len(queries), 2)
        with self.case.client.application.test_request_context('/', method='GET'):
            statements()
            self.assertEqual(len(queries), 3)
        with self.case.client.application.test_request_context('/', method='POST'):
            statements(); statements()
            self.assertEqual(len(queries), 5)

    def test_cold_private_reads_do_not_provision_or_rewrite_access(self):
        self.case.login(self.case.fueler)
        writes = []
        def record(c, cur, sql, *args):
            if sql.lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'}:
                writes.append(sql)
        event.listen(db.engine, 'before_cursor_execute', record)
        self.addCleanup(event.remove, db.engine, 'before_cursor_execute', record)
        for path in ('fueler/live-panel', 'fueling-board/live-panel', 'truck-driver/live-panel', 'hanzo/live-panel',
                     'fueling-board/revision', 'truck-driver/revision', 'hanzo/revision'):
            g.__dict__.clear()
            db.session.expire_all()
            self.assertEqual(self.case.client.get('/neoscorpion/' + path).status_code, 200, path)
        self.assertEqual(writes, [])

    def test_scoped_policy_never_exempts_unsafe_or_other_endpoints(self):
        for endpoint in policy.SCORPION_READONLY_LIVE_ENDPOINTS:
            self.assertTrue(policy.is_lightweight_live_state_request(endpoint, 'GET'))
            self.assertTrue(policy.is_lightweight_live_state_request(endpoint, 'HEAD'))
            self.assertFalse(policy.is_lightweight_live_state_request(endpoint, 'POST'))
            self.assertEqual(policy.lightweight_live_state_scope_spec(endpoint)['node_code'], 'scorpion')
        self.assertFalse(policy.is_lightweight_live_state_request('neoscorpion.fueler_save', 'GET'))


class ArchiveOriginTest(unittest.TestCase):
    setUp = learning_fixtures.SpearLearningCaptureTest.setUp
    tearDown = learning_fixtures.SpearLearningCaptureTest.tearDown

    def test_origin_metadata_is_preserved_and_strictly_validated(self):
        from copy import deepcopy
        import gzip
        import json
        from app.services.neoscorpion_spear_learning_capture import build_completed_learning_record
        from app.services.neoscorpion_learning_vault import _validate_learning_record
        from app.services import neoscorpion_learning_vault as vault
        from tests import test_neoscorpion_learning_vault as transport
        record = build_completed_learning_record(self.gateway, self.operation, self.assignment, self.mission)
        self.assertTrue(record['operational_timelines'])
        client = transport._Client()
        for origin in (None, 'production', 'test'):
            candidate = deepcopy(record)
            candidate['operational_timelines'][0]['facts']['learning_origin'] = origin
            before = deepcopy(candidate)
            _validate_learning_record(candidate)
            self.assertEqual(candidate, before)
            with patch.object(vault, '_r2_client', return_value=client):
                saved = vault.export_learning_record(candidate, _config=transport.CONFIG)
                repeated = vault.export_learning_record(candidate, _config=transport.CONFIG)
            self.assertTrue(saved['verified'])
            self.assertTrue(repeated['already_saved'])
            stored = json.loads(gzip.decompress(client.objects[saved['key']]))
            self.assertEqual(stored['operational_timelines'][0]['facts']['learning_origin'], origin)
        for origin in ('untrusted-provider', {}, [], 1):
            candidate = deepcopy(record)
            candidate['operational_timelines'][0]['facts']['learning_origin'] = origin
            with self.assertRaisesRegex(ValueError, 'Invalid SPEAR learning record'):
                _validate_learning_record(candidate)
        _validate_learning_record(record)  # Untagged historical records still validate.


if __name__ == '__main__':
    unittest.main()
