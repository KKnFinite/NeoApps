"""Authenticated read-only deltas retain the canonical rendering contract."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import event
from app.extensions import db
from tests import test_dispatch_refresh_fragments as fixtures


class NeoScorpionBandwidthTest(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.DispatchFragmentsTest();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.ready()
        self.case.settings.spear_recommendations_enabled=True
        db.session.commit()

    def spear(self, fingerprints=None, **args):
        query=dict(spear_only='1',operation_id=self.case.operation.id);query.update(args)
        return self.case.client.get('/neoscorpion/fuel-dispatch/live-panel',
            query_string=query,
            headers={'X-Neo-Spear-Fingerprints':json.dumps(fingerprints or {})})

    def test_spear_recalculates_without_operational_forms_or_history(self):
        writes=[]
        def record(c,cur,sql,*args):
            if sql.lstrip().split()[0].upper() in ('UPDATE','INSERT','DELETE'):writes.append(sql)
        event.listen(db.engine,'before_cursor_execute',record)
        try: result=self.spear()
        finally:event.remove(db.engine,'before_cursor_execute',record)
        self.assertEqual(result.status_code,200)
        payload=result.json;html=''.join(payload['spear_fragments'].values())
        self.assertIn('SPEAR',html)
        self.assertIn('data-live-spear="overview"',html)
        self.assertIn('data-live-spear="mission-',html)
        for control in ('data-dispatch-completion-slot','data-dispatch-action-center','data-assets-workspace',
                        'data-dispatch-work-timeline','name="actual_left"'):
            self.assertNotIn(control,html)
        self.assertNotIn('html',payload)
        self.assertEqual(writes,[])
        self.assertLess(len(result.data),len(self.case.client.get('/neoscorpion/fuel-dispatch/live-panel').data)/3)

    def test_identical_spear_fragments_omit_unchanged_markup(self):
        first=self.spear().json
        second=self.spear(first['spear_fingerprints']).json
        self.assertEqual(second['spear_fragments'],{})
        self.assertEqual(first['spear_fingerprints'],second['spear_fingerprints'])
        self.assertLess(len(json.dumps(second)),3500)

    def test_bad_fingerprint_input_cannot_hide_authorized_content(self):
        for raw in ('[]','bad','x'*7000):
            r=self.case.client.get('/neoscorpion/fuel-dispatch/live-panel?spear_only=1',
                headers={'X-Neo-Spear-Fingerprints':raw})
            self.assertEqual(r.status_code,200)
            self.assertTrue(r.json['spear_fragments'])

    def test_sort_and_permission_checks_apply_before_fragment_delivery(self):
        self.assertEqual(self.spear(operation_id=self.case.operation.id+10).status_code,409)
        with patch('app.neonodes.neoscorpion.routes.permission_access',return_value={'can_view':False,'can_edit':False}):
            r=self.spear()
        self.assertEqual(r.status_code,403)
        self.assertNotIn('spear_fragments',r.json)

    def test_readonly_spear_does_not_offer_operational_actions(self):
        with patch('app.neonodes.neoscorpion.routes.permission_access',return_value={'can_view':True,'can_edit':False}):
            html=''.join(self.spear().json['spear_fragments'].values())
        self.assertNotIn('data-spear-action-form',html)

    def test_fueler_board_and_driver_omit_identical_private_panels(self):
        for path,user in (('fueler',self.case.fueler),('fueling-board',self.case.dispatcher),
                          ('truck-driver',self.case.dispatcher),('hanzo',self.case.dispatcher)):
            with self.subTest(screen=path):
                self.case.login(user)
                url='/neoscorpion/'+path+'/live-panel'
                first=self.case.client.get(url).json
                second=self.case.client.get(url,headers={'X-Neo-Panel-Digest':first['panel_digest']})
                self.assertTrue(second.json['unchanged'])
                self.assertEqual(second.json['html'],'')
                self.assertLess(len(second.data),350)
                self.assertIn('no-store',second.headers['Cache-Control'])
                third=self.case.client.get(url,headers={'X-Neo-Panel-Digest':'invalid'}).json
                self.assertFalse(third['unchanged']);self.assertTrue(third['html'])

    def test_fueler_changed_reading_cannot_be_hidden_by_prior_digest(self):
        self.case.login(self.case.fueler)
        url='/neoscorpion/fueler/live-panel'
        first=self.case.client.get(url).json
        self.case.work.notes='New saved aircraft note';db.session.commit()
        result=self.case.client.get(url,headers={'X-Neo-Panel-Digest':first['panel_digest']}).json
        self.assertFalse(result['unchanged'])
        self.assertIn('New saved aircraft note',result['html'])

    def test_digest_does_not_bypass_authorization(self):
        url='/neoscorpion/fueler/live-panel';self.case.login(self.case.fueler)
        digest=self.case.client.get(url).json['panel_digest']
        with patch('app.neonodes.neoscorpion.routes.permission_access',return_value={'can_view':False,'can_edit':False}):
            result=self.case.client.get(url,headers={'X-Neo-Panel-Digest':digest})
        self.assertEqual(result.status_code,403);self.assertNotIn('html',result.json)

    def test_spear_keeps_actor_bound_learning_references_without_archive_io(self):
        from itsdangerous import URLSafeSerializer
        self.case.settings.spear_learning_capture_enabled=True;db.session.commit()
        with patch('app.services.neoscorpion_spear_decisions.learning_vault_status',
                   return_value=SimpleNamespace(configured=True)):
            result=self.spear().json
        reference=result['spear_references'][str(self.case.mission.id)]
        evidence=URLSafeSerializer(self.case.fixture.app.secret_key,salt='spear-rendered-decision-v1').loads(reference)
        self.assertEqual(evidence['actor_id'],self.case.dispatcher.id)
        self.assertEqual(evidence['gateway_id'],self.case.gateway.id)
        self.assertEqual(evidence['scope']['mission_id'],self.case.mission.id)
