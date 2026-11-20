"""Real no-transfer FOB -> independent physical UPLIFT, and optional app decisions."""
import json
from types import SimpleNamespace
from unittest import TestCase

from app.extensions import db
from app.models import (AccountAccessDecision, AccountAccessRequest, NeoScorpionFuelCycleHistory,
    NeoScorpionFuelingEvent, NeoScorpionTruckInventoryAudit, PortalAppAccess, SortDateMission)
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_uplift_defuel as visit_fixtures
from tests import test_accounts_workspace as account_fixtures


class FobUpliftTest(TestCase):
    setUp = visit_fixtures.NeoScorpionUpliftDefuelTest.setUp
    tearDown = visit_fixtures.NeoScorpionUpliftDefuelTest.tearDown
    _add_user = visit_fixtures.NeoScorpionUpliftDefuelTest._add_user
    _operation = visit_fixtures.NeoScorpionUpliftDefuelTest._operation
    _assignment = visit_fixtures.NeoScorpionUpliftDefuelTest._assignment
    _truck = visit_fixtures.NeoScorpionUpliftDefuelTest._truck
    _save_cycle = visit_fixtures.NeoScorpionUpliftDefuelTest._save_cycle
    _reassign_cycle = visit_fixtures.NeoScorpionUpliftDefuelTest._reassign_cycle
    _complete_cycle = visit_fixtures.NeoScorpionUpliftDefuelTest._complete_cycle
    _revision = visit_fixtures.NeoScorpionUpliftDefuelTest._revision
    _login = visit_fixtures.NeoScorpionUpliftDefuelTest._login

    def fob(self):
        operation = self._operation()
        mission, assignment = self._assignment(operation, required=25000)
        work = self._save_cycle(assignment, remaining=(9, 9, 9), actual=(10, 10, 10), transfer=0).fuel_work_state
        db.session.commit()
        self.original_baseline = fuel.fueler_context(self.gateway, self.fueler, assignment_id=assignment.id)['rows'][0]['edit_baseline']
        fuel.complete_fuel_on_board(self.gateway, self.dispatcher, assignment.id)
        db.session.commit()
        return operation, mission, assignment, work

    def start(self, assignment, **kwargs):
        return fuel.start_follow_up_fuel_cycle(self.gateway, self.dispatcher, assignment.id,
            'uplift', '32.0', None, None, expected_cycle=1,
            expected_tail=assignment.sort_date_mission.assigned_tail_number, **kwargs)

    def test_fueler_remaining_count_tracks_current_departures_and_reopened_uplift(self):
        from datetime import timedelta

        operation, _, assignment, _ = self.fob()
        _, other = self._assignment(operation, flight="UPS902", tail="N413UP")
        other.assigned_fueler_user_id = self.other_fueler.id
        self._truck(operation, other, "OTHER", 500)
        past = self._operation(day=operation.sort_date - timedelta(days=7))
        for index, (sort, kind, status) in enumerate((
            (operation, "departure", None),
            (operation, "departure", "cancelled"),
            (operation, "arrival", None),
            (past, "departure", None),
        ), 1):
            db.session.add(SortDateMission(
                sort_date_operation_id=sort.id, sort_date=sort.sort_date, gateway_code=sort.gateway_code,
                sort_name=sort.sort_name, mission_type=kind, mission_source="manual", flight_number=f"UPS50{index}",
                origin="RFD", destination="SDF", departure_status=status,
            ))
        db.session.commit()

        def assert_count(expected):
            text = f"{expected} fueling assignments remaining".encode()
            for user in (self.fueler, self.other_fueler):
                self.client.post("/logout")
                self._login(user)
                page = self.client.get("/neoscorpion/fueler")
                self.assertIn(text, page.data)
                revision = self.client.get("/neoscorpion/fuel-assignments/revision").json
                self.assertEqual(revision["operation_id"], operation.id)
                self.assertEqual(revision["remaining_fueling_assignments"], expected)
                panel = self.client.get("/neoscorpion/fueler/live-panel").json
                self.assertIn(text.decode(), panel["html"])

        assert_count(2)
        self.start(assignment)
        db.session.commit()
        assert_count(3)
        truck, _ = self._truck(operation, SimpleNamespace(assigned_truck_id=None), "UPLIFT", 500)
        self._reassign_cycle(assignment, truck.id)
        self._complete_cycle(operation, assignment, remaining=(10, 10, 10), actual=(12, 10, 10), transfer=300)
        self.assertEqual(NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=assignment.id).count(), 1)
        assert_count(2)

    def test_fob_uplift_full_lifecycle_preserves_evidence_and_posts_only_uplift_once(self):
        operation, mission, assignment, original = self.fob()
        evidence = fuel._fuel_rollback_values(original)
        tanks = [fuel._fuel_rollback_values(t) for t in original.tank_states]
        fob_at = assignment.fuel_on_board_at_utc
        started = self.start(assignment)
        db.session.commit()
        self.assertNotEqual(original.id, started.fuel_work_state.id)
        self.assertEqual(assignment.current_cycle_number, 2)
        self.assertIsNone(assignment.fuel_on_board_at_utc)
        self.assertEqual({t.tank_code: t.remaining_lbs for t in started.fuel_work_state.tank_states},
            {'left': 10000, 'ctr': 10000, 'right': 10000})
        self.assertTrue(all(t.actual_lbs is None for t in started.fuel_work_state.tank_states))
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)
        self.assertEqual(NeoScorpionTruckInventoryAudit.query.count(), 0)
        history = NeoScorpionFuelCycleHistory.query.one()
        self.assertEqual(history.label, 'FOB')
        self.assertEqual(history.snapshot['_uplift_rollbacks'][0]['assignment']['fuel_on_board_at_utc'], fob_at.isoformat())
        truck, nightly = self._truck(operation, SimpleNamespace(assigned_truck_id=None), 'FOB-UP', 500)
        self._reassign_cycle(assignment, truck.id)
        with self.assertRaises(fuel.FuelerDataConflict):
            fuel.save_fueler_entry(self.gateway, self.fueler, dict(assignment_id=str(assignment.id),
                expected=json.dumps(self.original_baseline), actual_left='1'), require_expected=True)
        db.session.rollback()
        self._complete_cycle(operation, assignment, remaining=(10, 10, 10), actual=(12, 10, 10), transfer=300)
        event = NeoScorpionFuelingEvent.query.one()
        self.assertEqual((event.event_type, event.cycle_number, event.transfer_fuel_gallons), ('uplift', 2, 300))
        self.assertEqual(nightly.current_gallons, 200)
        self.assertFalse(fuel.complete_fueled_assignment(self.gateway, self.dispatcher, assignment.id).changed)
        db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 1)
        self.assertEqual(nightly.current_gallons, 200)
        self.assertEqual(fuel._fuel_rollback_values(original), evidence)
        self.assertEqual([fuel._fuel_rollback_values(t) for t in original.tank_states], tanks)

    def test_duplicate_stale_and_failed_start_do_not_change_fob(self):
        operation, mission, assignment, original = self.fob()
        before = fuel._fuel_rollback_values(assignment)
        from unittest.mock import patch
        with patch.object(fuel, 'new_work', side_effect=RuntimeError('synthetic rollback')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic rollback'):
                self.start(assignment)
        db.session.rollback()
        self.assertEqual(fuel._fuel_rollback_values(assignment), before)
        self.assertEqual(NeoScorpionFuelCycleHistory.query.count(), 0)
        self.start(assignment)
        db.session.commit()
        with self.assertRaises(ValueError):
            self.start(assignment)
        db.session.rollback()
        self.assertEqual(assignment.current_cycle_number, 2)
        fuel.cancel_uplift(self.gateway, self.dispatcher, assignment.id, expected_cycle=2,
            expected_tail=mission.assigned_tail_number)
        db.session.commit()
        self.assertEqual(assignment.fuel_on_board_at_utc.isoformat(), before['fuel_on_board_at_utc'])
        self.assertEqual(assignment.active_work_state_id, original.id)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)
        self.assertEqual(NeoScorpionTruckInventoryAudit.query.count(), 0)

    def test_fob_does_not_enable_defuel_or_borrow_different_tail(self):
        operation, mission, assignment, original = self.fob()
        with self.assertRaises(ValueError):
            fuel.start_follow_up_fuel_cycle(self.gateway, self.dispatcher, assignment.id,
                'defuel', '20.0', self.fueler.id, 999, expected_cycle=1, expected_tail=mission.assigned_tail_number)
        db.session.rollback()
        mission.assigned_tail_number = 'N999UP'  # Synthetic MotherBrain tail replacement.
        db.session.commit()
        with self.assertRaisesRegex(ValueError, 'FOB|verified|aircraft'):
            self.start(assignment)
        db.session.rollback()
        self.assertEqual(assignment.active_work_state_id, original.id)

    def test_dispatcher_offers_and_starts_uplift_after_fob(self):
        operation, mission, assignment, original = self.fob()
        self._login(self.dispatcher)
        page = self.client.get('/neoscorpion/fuel-dispatch')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'name="cycle_type" value="uplift"', page.data)
        self.assertNotIn(b'name="cycle_type" value="defuel"', page.data)
        response = self.client.post('/neoscorpion/fuel-dispatch/start-follow-up', data=dict(
            assignment_id=assignment.id, cycle_type='uplift', required_fuel='32.0',
            expected_cycle='1', expected_tail=mission.assigned_tail_number))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(assignment.current_cycle_number, 2)


class OptionalDecisionReasonTest(TestCase):
    setUp = account_fixtures.AccountsWorkspaceTest.setUp
    tearDown = account_fixtures.AccountsWorkspaceTest.tearDown
    login = account_fixtures.AccountsWorkspaceTest.login
    review = account_fixtures.AccountsWorkspaceTest.review
    action = account_fixtures.AccountsWorkspaceTest.action
    my_request = account_fixtures.AccountsWorkspaceTest.my_request

    def test_blank_and_short_approval_denial_reasons_preserve_decision_history(self):
        for outcome, reason in [('denied', ''), ('approved', 'x'), ('denied', 'Supplied reason'), ('approved', '')]:
            with self.subTest(outcome=outcome, reason=reason):
                self.login(self.target)
                self.assertEqual(self.my_request().status_code, 302)
                self.login(self.admin)
                result = self.action('application', dict(app_code='neostaffing', outcome=outcome, role='operator', reason=reason))
                self.assertEqual(result.status_code, 302)
                access = PortalAppAccess.query.filter_by(user_id=self.target.id, app_code='neostaffing').one()
                self.assertEqual(access.status, outcome)
                decision = AccountAccessDecision.query.order_by(AccountAccessDecision.id.desc()).first()
                self.assertEqual(decision.reason, reason or 'Administrator decision')
                if outcome == 'approved':
                    # A new request is intentionally unnecessary after approval.
                    self.login(self.admin)
                    self.assertEqual(self.action('application', dict(app_code='neostaffing', outcome='denied', reason='Reset fixture request state')).status_code, 302)

    def test_optional_rendering_does_not_weaken_revocation_or_applicant_justification(self):
        self.login(self.target)
        self.assertEqual(self.my_request(reason='').status_code, 400)
        self.assertEqual(self.my_request().status_code, 302)
        self.login(self.admin)
        response = self.client.get(f'/accounts/people/{self.target.id}?tab=access')
        import re
        attrs = re.search(rb'<input name="reason"([^>]*)>', response.data).group(1)
        self.assertNotIn(b'required', attrs)
        self.assertNotIn(b'minlength', attrs)
        self.assertEqual(self.action('application', dict(app_code='neostaffing', outcome='approved', role='operator', reason='')).status_code, 302)
        self.assertEqual(self.action('application', dict(app_code='neostaffing', outcome='revoked', reason='')).status_code, 400)
        self.assertTrue(PortalAppAccess.query.filter_by(user_id=self.target.id, app_code='neostaffing').one().is_active)


from tests import test_scoped_authorization as scoped_fixtures


class ScopedOptionalDecisionTest(TestCase):
    setUp = scoped_fixtures.ScopedAuthorizationTest.setUp
    tearDown = scoped_fixtures.ScopedAuthorizationTest.tearDown
    as_user = scoped_fixtures.ScopedAuthorizationTest.as_user
    cutover = scoped_fixtures.ScopedAuthorizationTest.cutover

    def test_future_scoped_application_decisions_accept_omitted_reasons_only(self):
        from app.services.account_authority import Scope
        from app.services.accounts_workspace import review_token
        from app.services import scoped_access_management as management
        self.cutover()  # Synthetic database only; production remains pending.
        scope = Scope('application', 'neostaffing')
        access = PortalAppAccess(user_id=self.target.id, app_code='neostaffing', status='pending', role='watcher', is_active=True)
        db.session.add(access)
        db.session.commit()
        for outcome, reason in [('approved', ''), ('denied', 'x'), ('approved', '')]:
            management.decide_application(self.target.id, scope, 'operator', outcome, review_token(self.target), reason)
            db.session.commit()
            self.assertEqual(access.status, outcome)
        with self.assertRaisesRegex(ValueError, 'reason'):
            management.decide_application(self.target.id, scope, 'operator', 'revoked', review_token(self.target), '')
        db.session.rollback()
        self.assertTrue(access.is_active)
