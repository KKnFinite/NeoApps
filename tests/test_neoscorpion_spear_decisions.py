"""Focused vNext phase-one source, evidence, failure and outcome contracts."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from itsdangerous import URLSafeSerializer

from app.extensions import db
from app.models import NeoScorpionSettings, NeoScorpionFuelingEvent
from app.models.neoscorpion_spear_observation import NeoScorpionSpearObservation as Observation
from app.services import neoscorpion_spear_decisions as decisions
from app.services.neoscorpion_spear import execute_spear_step, first_automatic_step
from tests import test_neoscorpion_spear_learning_capture as fixtures
from tests import test_neoscorpion_spear as planning


class SpearDecisionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SpearLearningCaptureTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.gateway, self.assignment, self.mission = self.fixture.gateway, self.fixture.assignment, self.fixture.mission
        self.operation, self.user = self.fixture.operation, self.fixture.user
        self.configured = patch.object(decisions, 'learning_vault_status', return_value=SimpleNamespace(configured=True))
        self.configured.start()
        self.addCleanup(self.configured.stop)
        self.before = decisions.scope(self.operation, self.mission, self.assignment, 12)
        self.now = datetime(2026, 9, 3, 4, 30)

    def reference(self, **changes):
        evidence = dict(gateway_id=self.gateway.id, actor_id=self.user.id,
            generated_at=decisions._plain(self.now), plan_token='original-rendered-plan',
            algorithm_version='spear-v1', scope=self.before,
            recommendation=dict(action_type='assign', fueler_id=self.user.id,
                truck_id=self.assignment.assigned_truck_id, projected_complete_at_utc='2026-09-03T05:20:00Z'),
            alternatives=[], conditions={'parking':'B06'})
        evidence.update(changes)
        return URLSafeSerializer(self.fixture.app.secret_key, salt='spear-rendered-decision-v1').dumps(evidence)

    def observe(self, **kwargs):
        return decisions.observe(self.gateway, self.assignment, self.before, 'reassignment',
            actor=self.user, reference=kwargs.pop('reference', self.reference()), now=kwargs.pop('now', self.now), **kwargs)

    def test_dispatch_has_only_spear_source_and_adapter_matches_exact_pair(self):
        source = Path('app/services/neoscorpion.py').read_text(encoding='utf-8')
        template = Path('app/templates/neonodes/neoscorpion/_fuel_dispatch_panel.html').read_text(encoding='utf-8')
        self.assertNotIn('recommend_assignment_resources', source)
        self.assertNotIn('SUGGESTED', template)
        self.assertIn('(SPEAR Recommended)', template)
        from app.services.neoscorpion import _attach_spear_plan
        row = planning._row()
        plan = planning._plan([row])
        _attach_spear_plan([row], [], plan)
        self.assertEqual(row['assignment_recommendation'].truck_id, plan.steps[0].truck_id)
        self.assertEqual(row['assignment_recommendation'].fueler_id, plan.steps[0].fueler_id)

    def test_partial_assignment_keeps_selected_resource_and_invalid_partial_has_no_pair(self):
        row = planning._row(assignment=SimpleNamespace(assigned_fueler_user_id=2, assigned_truck_id=None))
        plan = planning._plan([row], fuelers=(planning._fueler(1), planning._fueler(2)))
        self.assertEqual(plan.steps[0].fueler_id, 2)
        row['assignment'].assigned_fueler_user_id = 99
        plan = planning._plan([row])
        self.assertFalse(plan.steps)

    def test_top_off_is_never_automatic_and_execution_requires_explicit_approval(self):
        plan = planning._plan([planning._row(demand=2500)], trucks=(planning._truck(current=600, capacity=4000),))
        step = plan.steps[0]
        self.assertEqual(step.action_type, 'top_off')
        self.assertIsNone(first_automatic_step(plan))
        action = Mock()
        with self.assertRaisesRegex(ValueError, 'Dispatcher approval'):
            execute_spear_step(step, assign_action=Mock(), top_off_action=action)
        action.assert_not_called()
        execute_spear_step(step, assign_action=Mock(), top_off_action=action, dispatcher_approved=True)
        action.assert_called_once_with(step)

    def test_top_off_rendered_approval_is_actor_plan_and_truck_bound(self):
        from app.services.neoscorpion_spear import attach_top_off_approvals, validate_top_off_approval
        plan=planning._plan([planning._row(demand=2500)], trucks=(planning._truck(current=600,capacity=4000),))
        step=plan.steps[0]
        context={'spear_plan':plan,'operation':self.operation,'truck_visuals':[{'spear_recommendation':step}]}
        attach_top_off_approvals(context,self.user.id)
        token=context['truck_visuals'][0]['spear_top_off_approval']
        self.assertTrue(validate_top_off_approval(token,self.user.id,self.operation.id,plan,step))
        self.assertFalse(validate_top_off_approval(token,self.user.id+1,self.operation.id,plan,step))
        self.assertFalse(validate_top_off_approval('',self.user.id,self.operation.id,plan,step))

    def test_separate_physical_visits_are_separate_decisions(self):
        from app.services.neoscorpion_segments import new_work
        work1=new_work(self.assignment,self.assignment.confirmed_tail_number,self.user,self.now)
        one=self.observe()
        work2=new_work(self.assignment,self.assignment.confirmed_tail_number,self.user,self.now+timedelta(minutes=20))
        two=self.observe(now=self.now+timedelta(minutes=20))
        self.assertNotEqual(one,two)
        self.assertEqual(db.session.get(Observation,one).payload['payload']['work_id'],work1.id)
        self.assertEqual(db.session.get(Observation,two).payload['payload']['work_id'],work2.id)

    def test_separate_vault_contract_is_idempotent_and_checks_provider_failure(self):
        from tests import test_neoscorpion_learning_vault as vault_cases
        from app.services import neoscorpion_learning_vault as vault
        identifier=self.observe()
        record=db.session.get(Observation,identifier).payload
        client=vault_cases._Client()
        with patch.object(vault,'_r2_client',return_value=client):
            first=vault.export_decision_record(record,_config=vault_cases.CONFIG)
            second=vault.export_decision_record(record,_config=vault_cases.CONFIG)
        self.assertEqual(first['key'],second['key'])
        self.assertTrue(second['already_saved'])
        self.assertEqual(len(client.objects),1)
        self.assertTrue(first['key'].startswith('decision-observation/v1/'))

    def test_original_recommendation_survives_actual_changed_selection(self):
        reference = self.reference()
        expected_truck = self.assignment.assigned_truck_id
        self.assignment.assigned_truck_id = None
        identifier = self.observe(reference=reference)
        db.session.commit()
        record = db.session.get(Observation, identifier).payload['payload']
        self.assertEqual(record['original_spear']['recommendation']['truck_id'], expected_truck)
        self.assertEqual(record['original_spear']['plan_token'], 'original-rendered-plan')
        self.assertEqual(record['comparison'], 'partial_selection')
        self.assertIsNone(record['selected']['truck_id'])

    def test_full_match_difference_missing_partial_and_stale_are_honest(self):
        evidence = {'recommendation': {'action_type':'assign','fueler_id':1,'truck_id':10}}
        for selected, state, expected in [((1,10),'current','matched'), ((2,10),'current','different'),
            ((1,None),'current','partial_selection'), ((2,10),'stale','stale_recommendation')]:
            self.assertEqual(decisions.classify(evidence, state, *selected), expected)
        self.assertEqual(decisions.classify(None,'missing',2,10),'no_recommendation')
        self.assertEqual(decisions.classify({'recommendation':None},'current',2,10),'no_recommendation')

    def test_signature_actor_scope_and_operational_staleness(self):
        for raw, state in [(self.reference()+'bad','invalid'), (self.reference(actor_id=self.user.id+999),'invalid'),
                           (self.reference(generated_at=decisions._plain(self.now-timedelta(hours=1))),'stale')]:
            with self.subTest(state=state):
                evidence, actual = decisions._reference(raw,self.gateway,self.user,self.before,self.now)
                self.assertEqual(actual,state)
        changed = {**self.before,'revision':13}
        evidence,state=decisions._reference(self.reference(),self.gateway,self.user,changed,self.now)
        self.assertEqual(state,'stale')
        self.assertEqual(evidence['plan_token'],'original-rendered-plan')

    def test_off_or_unconfigured_creates_no_observation_or_export(self):
        settings=NeoScorpionSettings.query.one()
        settings.spear_learning_capture_enabled=False
        db.session.flush()
        self.assertIsNone(self.observe())
        settings.spear_learning_capture_enabled=True
        self.configured.stop()
        self.assertIsNone(self.observe())
        self.assertEqual(Observation.query.count(),0)

    def test_capture_failure_does_not_rollback_canonical_change(self):
        self.assignment.transfer_fuel_gallons=1250
        db.session.flush()
        with patch.object(decisions,'_enqueue',side_effect=RuntimeError('Vault/outbox unavailable')):
            self.assertIsNone(self.observe())
        db.session.commit()
        self.assertEqual(self.assignment.transfer_fuel_gallons,1250)

    def test_duplicate_capture_is_idempotent_but_separate_decisions_remain_distinct(self):
        first=self.observe()
        self.assertEqual(first,self.observe())
        second=self.observe(now=self.now+timedelta(seconds=1))
        self.assertNotEqual(first,second)
        self.assertEqual(Observation.query.count(),2)
        with self.assertRaisesRegex(ValueError,'immutable'):
            row=db.session.get(Observation,first)
            row.payload={**row.payload,'training_eligible':True}
            db.session.flush()
        db.session.rollback()

    def test_retry_is_durable_and_disabled_capture_never_exports(self):
        identifier=self.observe()
        db.session.commit()
        with patch('app.services.neoscorpion_learning_vault.export_decision_record',side_effect=RuntimeError('offline')) as export:
            self.assertEqual(decisions.drain(self.gateway.id),0)
            self.assertGreater(db.session.get(Observation,identifier).attempts,0)
            self.assertIsNone(db.session.get(Observation,identifier).delivered_at)
            NeoScorpionSettings.query.one().spear_learning_capture_enabled=False
            db.session.commit()
            export.reset_mock()
            self.assertEqual(decisions.drain(self.gateway.id),0)
            export.assert_not_called()

    def test_outcome_reconciles_original_cycle_without_inventing_visit_or_quality(self):
        identifier=self.observe()
        db.session.commit()
        self.assignment.current_cycle_number=2
        db.session.commit()
        decisions.reconcile(self.gateway.id)
        outcome=Observation.query.filter_by(kind='outcome').one()
        self.assertEqual(outcome.cycle_number,1)
        self.assertEqual(outcome.payload['payload']['decision_id'],identifier)
        self.assertEqual(outcome.payload['payload']['event_ids'],[NeoScorpionFuelingEvent.query.one().id])
        self.assertFalse(outcome.payload['payload']['qualified'])
        self.assertEqual(outcome.payload['payload']['comparison_conclusion'],'observed_only')
        self.assertFalse(outcome.payload['training_eligible'])

    def test_optional_reason_is_separate_evidence_not_a_recommendation_or_ground_truth(self):
        original=self.observe()
        db.session.commit()
        identifier=decisions.add_reason(self.gateway,self.user,original,'operational_judgment','<script>text</script>')
        db.session.commit()
        self.assertEqual(db.session.get(Observation,identifier).kind,'reason')
        self.assertEqual(db.session.get(Observation,identifier).payload['payload']['decision_id'],original)
        self.assertFalse(db.session.get(Observation,identifier).payload['training_eligible'])

    def test_contract_rejects_invalid_identity_unrecognized_shape_and_oversize(self):
        identifier=self.observe()
        record=db.session.get(Observation,identifier).payload
        decisions.validate_record(record)
        for changed in ({**record,'mission_id':0},{**record,'unexpected':1},
                        {**record,'payload':{'invented':'data'}},{**record,'training_eligible':True}):
            with self.assertRaises(ValueError):
                decisions.validate_record(changed)
