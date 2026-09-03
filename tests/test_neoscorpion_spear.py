from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest

from sqlalchemy import inspect

from app import create_app
from app.extensions import db
from app.models import Gateway, NeoScorpionSettings, User
from app.services.neoscorpion_spear import (
    SPEAR_DEFAULT_PRIORITY_ORDER,
    SpearPlan,
    SpearSettings,
    build_spear_plan,
    execute_spear_step,
    save_spear_settings,
    spear_dispatch_status,
)
from app.services.neoscorpion_spear_learning import (
    SPEAR_LEARNING_PAYLOAD_VERSION,
    build_learning_recommendation_payload,
)
from app.services.neoscorpion_learning_vault import export_learning_record


NOW = datetime(2026, 9, 3, 1, 0)


class _PlanningSettings:
    setup_minutes = Decimal("5")
    finishing_minutes = Decimal("5")
    eta_safety_buffer_minutes = Decimal("5")

    @staticmethod
    def pump_rate_for(_aircraft_type):
        return Decimal("100")

    @staticmethod
    def is_complete_for(_aircraft_type):
        return True


def _fueler(identifier=1, name="Smith"):
    return SimpleNamespace(
        id=identifier,
        first_name=name,
        last_name="",
        username=name.lower(),
        display_name=name,
    )


def _truck(identifier=10, *, current=2000, capacity=3000, status="available"):
    return {
        "truck": SimpleNamespace(
            id=identifier,
            truck_number=str(identifier),
            capacity_gallons=capacity,
            is_active=True,
            is_out_of_service=False,
        ),
        "selection": SimpleNamespace(status=status, current_gallons=current),
    }


def _row(identifier=100, *, demand=500, assignment=None, work_has_begun=False):
    departure = NOW + timedelta(hours=2)
    return {
        "mission": SimpleNamespace(
            id=identifier,
            flight_number=f"UPS{identifier}",
            eta_datetime_utc=None,
            planned_datetime_utc=departure,
        ),
        "arrival_mission": SimpleNamespace(
            actual_block_in_datetime_utc=NOW,
            eta_datetime_utc=NOW,
            planned_datetime_utc=NOW,
        ),
        "administratively_complete": False,
        "planning_demand_gallons": demand,
        "parking_position": "Charlie4",
        "detailed_aircraft_type": "B757",
        "assignment": assignment,
        "work_has_begun": work_has_begun,
    }


def _plan(rows, *, trucks=None, settings=None):
    return build_spear_plan(
        rows,
        operation=SimpleNamespace(id=1),
        planning_settings=_PlanningSettings(),
        spear_settings=settings or SpearSettings(),
        nightly_fuelers=(_fueler(),),
        nightly_trucks=trucks or (_truck(),),
        now_utc=NOW,
    )


class NeoScorpionSpearPlanningTest(unittest.TestCase):
    def test_compact_dispatch_status_prioritizes_risk_over_automation(self):
        plan = SpearPlan((), {}, {}, 0, 0, 0, 0, "", "token")
        self.assertEqual(
            spear_dispatch_status(plan, SpearSettings(recommendations_enabled=False))["state"],
            "off",
        )
        self.assertEqual(spear_dispatch_status(plan, SpearSettings())["state"], "ready")
        self.assertEqual(
            spear_dispatch_status(plan, SpearSettings(automation_enabled=True))["state"],
            "auto",
        )
        at_risk = SpearPlan((), {}, {}, 0, 1, 0, 0, "", "token")
        self.assertEqual(
            spear_dispatch_status(at_risk, SpearSettings(automation_enabled=True))["state"],
            "at-risk",
        )
        late = SpearPlan((), {}, {}, 0, 0, 1, 0, "", "token")
        self.assertEqual(
            spear_dispatch_status(late, SpearSettings(automation_enabled=True))["state"],
            "late",
        )

    def test_normal_recommendation_is_deterministic_and_covered(self):
        plan = _plan([_row()])

        self.assertEqual(len(plan.steps), 1)
        step = plan.steps[0]
        self.assertEqual((step.action_type, step.truck_id, step.fueler_id), ("assign", 10, 1))
        self.assertEqual(step.risk, "COVERED")
        self.assertEqual(plan.status_text, "SPEAR: ALL LOADS COVERED")

    def test_incomplete_fuel_data_is_waiting_not_covered_or_at_risk(self):
        plan = _plan([_row(demand=None)])

        self.assertEqual(plan.waiting_for_data_count, 1)
        self.assertEqual(plan.covered_count, 0)
        self.assertEqual(plan.at_risk_count, 0)
        self.assertEqual(plan.status_text, "SPEAR: 1 WAITING FOR DATA")
        self.assertEqual(
            plan.readiness_by_mission_id[100],
            ("required_fuel", "inbound_fuel", "estimated_gallons"),
        )

    def test_temporary_resource_shortage_is_evaluable_not_waiting_for_data(self):
        plan = _plan([_row()], trucks=(_truck(status="unavailable_oos"),))

        self.assertEqual(plan.waiting_for_data_count, 0)
        self.assertEqual(plan.unplanned_count, 1)
        self.assertEqual(plan.readiness_by_mission_id[100], ())

    def test_assignment_explanation_uses_planned_values_and_ranked_alternatives(self):
        plan = _plan([_row()], trucks=(_truck(10), _truck(20)))
        explanation = plan.steps[0].explanation

        self.assertEqual(explanation["kind"], "assignment")
        self.assertEqual(explanation["safe_completion_target"], "21:40")
        self.assertEqual(explanation["truck_gallons_before"], 2000)
        self.assertEqual(explanation["truck_gallons_after"], 1500)
        self.assertEqual(len(explanation["alternatives"]), 2)
        self.assertIn("Selected", explanation["alternatives"][0]["reason"])

    def test_why_spear_identifies_the_same_active_calibration_used_by_planning(self):
        calibration = SimpleNamespace(
            active=True, configured=Decimal("100"), effective=Decimal("120"), samples=3
        )
        plan = build_spear_plan(
            [_row()], operation=SimpleNamespace(id=1), planning_settings=_PlanningSettings(),
            spear_settings=SpearSettings(), nightly_fuelers=(_fueler(),),
            nightly_trucks=(_truck(),), now_utc=NOW,
            calibrations={("pump_rate", "B757"): calibration},
        )
        self.assertEqual(plan.steps[0].explanation["live_calibration"][0]["samples"], 3)

    def test_top_off_explanation_identifies_reserve_protection(self):
        plan = _plan([_row(demand=100)], trucks=(_truck(current=550, capacity=2000),))
        explanation = plan.steps[0].explanation

        self.assertEqual(explanation["kind"], "top_off")
        self.assertEqual(explanation["current_gallons"], 550)
        self.assertEqual(explanation["reserve_gallons"], 500)
        self.assertIn("Reserve", explanation["reason"])

    def test_learning_payload_is_versioned_deterministic_and_has_no_persistence(self):
        step = _plan([_row()]).steps[0]
        kwargs = {
            "captured_at_utc": NOW,
            "gateway_id": 1,
            "operation_id": 2,
            "mission_id": 100,
            "recommendation_token": "stable-token",
            "soft_priority_order": SPEAR_DEFAULT_PRIORITY_ORDER,
            "recommendation": step,
            "mission_facts": {"required_fuel": 500, "mission_ramp": "Charlie"},
            "candidate_trucks": ({"truck_id": 10, "location": "Remote"},),
            "candidate_fuelers": ({"user_id": 1, "location": "Remote"},),
        }
        self.assertEqual(
            build_learning_recommendation_payload(**kwargs),
            build_learning_recommendation_payload(**kwargs),
        )
        payload = build_learning_recommendation_payload(**kwargs)
        self.assertEqual(payload["schema_version"], SPEAR_LEARNING_PAYLOAD_VERSION)
        self.assertEqual(payload["record_type"], "recommendation_snapshot")
        with self.assertRaisesRegex(ValueError, "durable Learning Vault"):
            export_learning_record(payload)

    def test_reserve_shortfall_recommends_existing_top_off_workflow(self):
        plan = _plan([_row(demand=100)], trucks=(_truck(current=550, capacity=2000),))

        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].action_type, "top_off")
        self.assertEqual(plan.steps[0].truck_id, 10)

    def test_sent_work_locks_valid_resources_but_invalidity_replans(self):
        assignment = SimpleNamespace(
            id=7,
            assigned_fueler_user_id=1,
            assigned_truck_id=10,
            operational_status="active",
            completed_at_utc=None,
        )
        locked = _plan([_row(assignment=assignment)])
        self.assertEqual(locked.steps, ())

        replanned = _plan(
            [_row(assignment=assignment, work_has_begun=True)],
            trucks=(
                _truck(10, status="unavailable_oos"),
                _truck(20),
            ),
        )
        self.assertEqual(replanned.steps[0].truck_id, 20)
        self.assertEqual(replanned.steps[0].reason, "Replace invalid sent resource")
        self.assertFalse(replanned.steps[0].automatic_eligible)

    def test_automation_execution_uses_the_same_canonical_action_boundary(self):
        step = _plan([_row()]).steps[0]
        calls = []
        result = execute_spear_step(
            step,
            assign_action=lambda selected: calls.append(("assign", selected.mission_id)) or "saved",
            top_off_action=lambda selected: calls.append(("top_off", selected.truck_id)),
        )

        self.assertEqual(result, "saved")
        self.assertEqual(calls, [("assign", 100)])


class NeoScorpionSpearSettingsTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "spear-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config, auto_bootstrap=False)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = Gateway(code="RFD", name="Rockford")
        self.user = User(username="dispatcher", password_hash="x", role="master")
        db.session.add_all((self.gateway, self.user))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_settings_and_drag_order_persist(self):
        reversed_order = tuple(reversed(SPEAR_DEFAULT_PRIORITY_ORDER))
        result = save_spear_settings(
            self.gateway,
            self.user,
            {
                "recommendations_enabled": "1",
                "automation_enabled": "1",
                "minimum_truck_reserve_gallons": "650",
                "do_not_top_off_above_percent": "75",
                "truck_minutes_per_ramp_move": "3.5",
                "fueler_begins_at": "Alpha",
                "truck_begins_at": "Bravo",
                "truck_after_top_off": "Remote",
                "incoming_early_staging_minutes": "15",
                "recalculation_interval_minutes": "2",
                "automation_stability_delay_seconds": "5",
                "priority_order": ",".join(reversed_order),
            },
        )
        db.session.commit()
        db.session.expire_all()
        saved = NeoScorpionSettings.query.filter_by(gateway_id=self.gateway.id).one()

        self.assertTrue(result.automation_just_enabled)
        self.assertTrue(saved.spear_automation_enabled)
        self.assertFalse(saved.spear_learning_capture_enabled)
        self.assertEqual(saved.spear_minimum_truck_reserve_gallons, 650)
        self.assertEqual(tuple(__import__("json").loads(saved.spear_priority_order_json)), reversed_order)

        root = Path(__file__).resolve().parents[1]
        template = (root / "app/templates/neonodes/neoscorpion/spear_settings.html").read_text(encoding="utf-8")
        script = (root / "app/static/js/neoscorpion_spear_settings.js").read_text(encoding="utf-8")
        self.assertIn("SPEAR Fleet Optimizer", template)
        self.assertIn("data-spear-priority-list", template)
        self.assertIn('addEventListener("dragover"', script)

    def test_learning_capture_defaults_off_and_cannot_enable_without_vault(self):
        with self.assertRaisesRegex(ValueError, "durable Learning Vault"):
            save_spear_settings(
                self.gateway,
                self.user,
                {
                    "recommendations_enabled": "1",
                    "learning_capture_enabled": "1",
                    "minimum_truck_reserve_gallons": "500",
                    "do_not_top_off_above_percent": "70",
                    "truck_minutes_per_ramp_move": "2",
                    "fueler_begins_at": "Remote",
                    "truck_begins_at": "Remote",
                    "truck_after_top_off": "Remote",
                    "incoming_early_staging_minutes": "15",
                    "recalculation_interval_minutes": "2",
                    "automation_stability_delay_seconds": "5",
                    "priority_order": ",".join(SPEAR_DEFAULT_PRIORITY_ORDER),
                },
            )
        self.assertIsNone(
            NeoScorpionSettings.query.filter_by(gateway_id=self.gateway.id).first()
        )

    def test_teach_spear_is_visible_but_disabled_while_learning_is_off(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/templates/neonodes/neoscorpion/fuel_dispatch.html").read_text(encoding="utf-8")
        settings_template = (root / "app/templates/neonodes/neoscorpion/spear_settings.html").read_text(encoding="utf-8")

        self.assertIn("TEACH SPEAR", template)
        self.assertIn("LEARNING OFF", template)
        self.assertIn("SPEAR Learning Capture is not enabled yet.", template)
        self.assertIn("LEARNING VAULT NOT CONFIGURED", settings_template)

    def test_dispatch_renders_compact_readiness_and_collapsed_why_hook(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/templates/neonodes/neoscorpion/fuel_dispatch.html").read_text(encoding="utf-8")

        self.assertIn("SPEAR DATA READINESS", template)
        self.assertIn("WAITING FOR DATA", template)
        self.assertIn("<details class=\"neoscorpion-spear-why\"", template)
        self.assertIn("WHY SPEAR?", template)

    def test_dispatch_splash_requires_explicit_close_before_marking_seen(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/templates/neonodes/neoscorpion/fuel_dispatch.html").read_text(encoding="utf-8")
        script = (root / "app/static/js/neoscorpion_fuel_dispatch_live.js").read_text(encoding="utf-8")

        self.assertIn("data-spear-splash", template)
        self.assertIn("images/neoscorpion/spear-promo.png", template)
        self.assertIn("can_view_spear_settings", template)
        self.assertTrue((root / "app/static/images/neoscorpion/spear-promo.png").is_file())
        self.assertIn("neoapps.neoscorpion.spear-splash.v1", script)
        self.assertIn("window.localStorage.getItem", script)
        self.assertIn("data-spear-splash-close", template)
        self.assertIn("CLOSE", template)
        self.assertIn("data-spear-splash-close", script)
        self.assertIn('window.localStorage.setItem(storageKey, "seen")', script)
        self.assertNotIn("dismissTimer", script)
        self.assertNotIn("4700", script)

    def test_schema_contains_settings_and_execution_audit(self):
        inspector = inspect(db.engine)

        self.assertIn("neoscorpion_spear_audit_entries", inspector.get_table_names())
        self.assertIn("neoscorpion_spear_calibration_resets", inspector.get_table_names())
        columns = {
            column["name"]
            for column in inspector.get_columns("neoscorpion_settings")
        }
        self.assertIn("spear_automation_enabled", columns)
        self.assertIn("spear_learning_capture_enabled", columns)
        self.assertIn("spear_priority_order_json", columns)
        assignment_columns = {
            column["name"] for column in inspector.get_columns("neoscorpion_fuel_assignments")
        }
        self.assertIn("ready_for_fuel_at_utc", assignment_columns)


if __name__ == "__main__":
    unittest.main()
