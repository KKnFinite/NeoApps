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
    SpearSettings,
    build_spear_plan,
    execute_spear_step,
    save_spear_settings,
)


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
    def test_normal_recommendation_is_deterministic_and_covered(self):
        plan = _plan([_row()])

        self.assertEqual(len(plan.steps), 1)
        step = plan.steps[0]
        self.assertEqual((step.action_type, step.truck_id, step.fueler_id), ("assign", 10, 1))
        self.assertEqual(step.risk, "COVERED")
        self.assertEqual(plan.status_text, "SPEAR: ALL LOADS COVERED")

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
        self.assertEqual(saved.spear_minimum_truck_reserve_gallons, 650)
        self.assertEqual(tuple(__import__("json").loads(saved.spear_priority_order_json)), reversed_order)

        root = Path(__file__).resolve().parents[1]
        template = (root / "app/templates/neonodes/neoscorpion/spear_settings.html").read_text(encoding="utf-8")
        script = (root / "app/static/js/neoscorpion_spear_settings.js").read_text(encoding="utf-8")
        self.assertIn("SPEAR Fleet Optimizer", template)
        self.assertIn("data-spear-priority-list", template)
        self.assertIn('addEventListener("dragover"', script)

    def test_schema_contains_settings_and_execution_audit(self):
        inspector = inspect(db.engine)

        self.assertIn("neoscorpion_spear_audit_entries", inspector.get_table_names())
        columns = {
            column["name"]
            for column in inspector.get_columns("neoscorpion_settings")
        }
        self.assertIn("spear_automation_enabled", columns)
        self.assertIn("spear_priority_order_json", columns)


if __name__ == "__main__":
    unittest.main()
