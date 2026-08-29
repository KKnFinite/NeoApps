from datetime import date, datetime
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    GatewayMembership,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    User,
)
from app.neonodes.neorain.services import (
    neorain_outbound_context,
    neorain_outbound_revision,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from app.services.live_collaboration import entity_version


class NeoRainOutboundTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = Gateway(code="RFD", name="RFD")
        db.session.add(self.gateway)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_current_operation_rows_are_departures_ordered_and_display_canonical_fields(self):
        operation = self._operation()
        later = self._mission(
            operation,
            "UPS200",
            "LAX",
            planned=datetime(2026, 8, 30, 2, 10),
            tail="N200UP",
            wave="2",
            status="blocked_out",
        )
        earlier = self._mission(
            operation,
            "UPS100",
            "SDF",
            planned=datetime(2026, 8, 30, 1, 5),
            tail="N100UP",
            wave="1",
            status="departed",
        )
        earlier.elmac_completed_at_utc = datetime(2026, 8, 30, 6, 1)
        earlier.elmac_completed_source = "manual"
        earlier.ramp_load_completed_at_utc = datetime(2026, 8, 30, 6, 5)
        earlier.crew_load_completed_at_utc = datetime(2026, 8, 30, 6, 10)
        earlier.actual_block_out_datetime_utc = datetime(2026, 8, 30, 6, 15)
        self._mission(
            operation,
            "ARR900",
            "RFD",
            planned=datetime(2026, 8, 30, 0, 30),
            mission_type="arrival",
        )
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=operation.id,
                tail_number="N100UP",
                position_code="A12",
            )
        )
        db.session.commit()

        rows = neorain_outbound_context(self.gateway, operation=operation)["rows"]

        self.assertEqual([row["flight_number"] for row in rows], ["UPS100", "UPS200"])
        self.assertEqual(rows[0]["wave"], "1")
        self.assertEqual(rows[1]["wave"], "2")
        self.assertEqual(rows[0]["tail"], "N100UP")
        self.assertEqual(rows[0]["destination"], "SDF")
        self.assertEqual(rows[0]["parking"], "A12")
        self.assertEqual(rows[0]["planned_time"], "01:05")
        self.assertEqual(rows[0]["elmac"], "01:01")
        self.assertEqual(earlier.elmac_completed_source, "manual")
        self.assertEqual(rows[0]["ramp_load_complete"], "01:05")
        self.assertEqual(rows[0]["crew_load_complete"], "01:10")
        self.assertEqual(rows[0]["official_block_out"], "01:15")
        self.assertEqual(rows[0]["no_return"], "NO RETURN")
        self.assertEqual(rows[0]["version"], entity_version(earlier))
        self.assertEqual(rows[1]["no_return"], "")
        self.assertEqual(rows[1]["status"], "BLOCKED OUT")
        self.assertEqual(later.id, rows[1]["mission_id"])

    def test_wave_fallback_and_no_current_sort_are_clean(self):
        operation = self._operation()
        self._mission(operation, "UPS300", "ONT", planned=datetime(2026, 8, 30, 3), wave=None)
        db.session.commit()

        self.assertEqual(
            neorain_outbound_context(self.gateway, operation=operation)["rows"][0]["wave"],
            "-",
        )
        self.assertEqual(neorain_outbound_context(self.gateway, operation=None)["rows"], [])

    def test_revision_changes_for_mission_and_parking_changes_without_writes(self):
        operation = self._operation()
        mission = self._mission(operation, "UPS400", "OAK", planned=datetime(2026, 8, 30, 4))
        db.session.commit()
        first = neorain_outbound_revision(self.gateway, operation=operation)

        mission.updated_at = datetime(2026, 8, 30, 12)
        db.session.commit()
        second = neorain_outbound_revision(self.gateway, operation=operation)
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=operation.id,
                tail_number="N400UP",
                position_code="B2",
            )
        )
        db.session.commit()
        third = neorain_outbound_revision(self.gateway, operation=operation)

        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)

    def test_watcher_can_view_current_board_and_no_rain_role_is_denied(self):
        operation = self._operation()
        self._mission(operation, "UPS500", "SDF", planned=datetime(2026, 8, 30, 5))
        db.session.commit()
        watcher = self._user("rain_watcher", "watcher")
        self._login(watcher)

        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=operation,
        ):
            response = self.client.get("/neorain/outbound")
            revision = self.client.get("/neorain/outbound/revision")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPS500", response.data)
        self.assertIn(b"data-neorain-outbound-live", response.data)
        for column in (
            b">Wave<",
            b">Flight #<",
            b">eLMAC<",
            b">Ramp Load Complete<",
            b">Crew Load Complete<",
            b">Official Block-Out<",
            b">No Return<",
        ):
            self.assertIn(column, response.data)
        self.assertEqual(revision.status_code, 200)
        self.assertTrue(revision.get_json()["ok"])

        self.client.get("/logout")
        denied = self._user("not_rain", "watcher", rain_role=None)
        self._login(denied)
        self.assertIn(self.client.get("/neorain/outbound").status_code, (302, 403))

    def test_no_current_sort_renders_the_normal_empty_state(self):
        watcher = self._user("rain_empty", "watcher")
        self._login(watcher)

        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=None,
        ):
            response = self.client.get("/neorain/outbound")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No current sort.", response.data)

    def _operation(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code="RFD",
            sort_date=date(2026, 8, 30),
            sort_name="night",
        )
        db.session.add(operation)
        db.session.flush()
        return operation

    def _mission(
        self,
        operation,
        flight_number,
        destination,
        *,
        planned,
        tail=None,
        wave="1",
        status="scheduled",
        mission_type="departure",
    ):
        mission = SortDateMission(
            sort_date_operation_id=operation.id,
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
            mission_type=mission_type,
            mission_source="master",
            wave=wave,
            flight_number=flight_number,
            origin="RFD",
            destination=destination,
            timezone="America/Chicago",
            planned_datetime_local=planned,
            planned_datetime_utc=planned,
            planned_source="master",
            assigned_tail_number=tail or f"N{flight_number[-3:]}UP",
            departure_status=status if mission_type == "departure" else None,
        )
        db.session.add(mission)
        db.session.flush()
        return mission

    def _user(self, username, role, *, rain_role="watcher"):
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name="Rain",
            last_name="User",
            full_name="Rain User",
            employee_id=f"EMP-{username}",
            email_verified_at=datetime.utcnow(),
            role=role,
            is_active=True,
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        if rain_role is None:
            membership = GatewayMembership.query.filter_by(user_id=user.id).one()
            membership.is_active = False
        db.session.commit()
        return user

    def _login(self, user):
        return self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
            follow_redirects=False,
        )
