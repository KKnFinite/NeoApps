import unittest
from datetime import date, datetime, time

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoErmacDoorPull,
    NeoNode,
    SortDateMission,
    SortDateOperation,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoermac_building_lineup import get_building_lineup_rows
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoErmacLinkedDoorPullsTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoErmacLinkedDoorPullsConfig",
            (),
            {
                "SECRET_KEY": "linked-door-pulls-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "DEFAULT_GATEWAY_TIMEZONE": "America/Chicago",
                "CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE": datetime(
                    2026, 8, 10, 23, 0
                ),
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 8, 10),
            gateway_code=self.gateway.code,
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.flush()
        self._assign("green_runout", "east_destination_1", "SDF")
        self._assign("green_runout", "west_destination_1", "SDF")
        self.mission = SortDateMission(
            sort_date=self.operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name=self.operation.sort_name,
            sort_date_operation_id=self.operation.id,
            mission_type="departure",
            mission_source="master",
            flight_number="UPS302",
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 11, 2, 30),
            planned_datetime_utc=datetime(2026, 8, 11, 7, 30),
            departure_status="scheduled",
            pure_pull_time_local=time(1, 30),
            mix_pull_time_local=time(2, 0),
        )
        db.session.add(self.mission)
        self.user = self._add_user()
        db.session.commit()
        self.client = self.app.test_client()
        self.client.post(
            "/login",
            data={"email": self.user.email, "password": "TestPassword123!"},
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_linked_pure_writes_both_selected_opposite_doors(self):
        self._supervise("D1", "D4")

        response = self._save("D1", "pure", "01:45")

        self.assertEqual(response.status_code, 200)
        pulls = self._pulls_by_door()
        self.assertEqual(set(pulls), {"D1", "D4"})
        self.assertEqual(pulls["D1"].actual_pure_pull_time_local, time(1, 45))
        self.assertEqual(pulls["D4"].actual_pure_pull_time_local, time(1, 45))

    def test_linked_mix_writes_both_selected_opposite_doors(self):
        self._supervise("D1", "D4")

        response = self._save("D4", "mix", "02:12")

        self.assertEqual(response.status_code, 200)
        pulls = self._pulls_by_door()
        self.assertEqual(pulls["D1"].actual_mix_pull_time_local, time(2, 12))
        self.assertEqual(pulls["D4"].actual_mix_pull_time_local, time(2, 12))

    def test_editing_a_linked_time_updates_both_records(self):
        self._supervise("D1", "D4")
        self._save("D1", "pure", "01:40")

        self._save("D4", "pure", "01:52")

        pulls = self._pulls_by_door()
        self.assertEqual(pulls["D1"].actual_pure_pull_time_local, time(1, 52))
        self.assertEqual(pulls["D4"].actual_pure_pull_time_local, time(1, 52))

    def test_clearing_a_linked_time_clears_both_records(self):
        self._supervise("D1", "D4")
        self._save("D1", "pure", "01:45")

        self._save("D1", "pure", "")

        for pull in self._pulls_by_door().values():
            self.assertIsNone(pull.actual_pure_pull_time_local)
            self.assertFalse(pull.no_pure_pull)

    def test_no_pure_and_no_mix_propagate_to_both_records(self):
        self._supervise("D1", "D4")
        for pull_key in ("pure", "mix"):
            with self.subTest(pull_key=pull_key):
                self._save("D1", pull_key, no_pull=True)

        for pull in self._pulls_by_door().values():
            self.assertTrue(pull.no_pure_pull)
            self.assertTrue(pull.no_mix_pull)
            self.assertIsNone(pull.actual_pure_pull_time_local)
            self.assertIsNone(pull.actual_mix_pull_time_local)

    def test_unselected_counterpart_remains_independent(self):
        self._supervise("D1")

        self._save("D1", "pure", "01:45")

        pulls = self._pulls_by_door()
        self.assertEqual(set(pulls), {"D1"})
        self.assertEqual(pulls["D1"].actual_pure_pull_time_local, time(1, 45))

    def test_same_destination_at_an_unrelated_belt_is_not_changed(self):
        self._assign("runout_4", "east_destination_1", "SDF")
        self._assign("runout_4", "west_destination_1", "SDF")
        db.session.commit()
        self._supervise("D1", "D4", "D13", "D17")

        self._save("D1", "pure", "01:45")

        pulls = self._pulls_by_door()
        self.assertEqual(set(pulls), {"D1", "D4"})
        self.assertNotIn("D13", pulls)
        self.assertNotIn("D17", pulls)

    def test_aggregation_still_uses_latest_pure_and_mix_independently(self):
        self._assign("runout_4", "east_destination_1", "SDF")
        db.session.commit()
        self._supervise("D1", "D4", "D13")
        self._save("D1", "pure", "01:45")
        self._save("D1", "mix", "02:20")

        self._save("D13", "pure", "01:55")
        self._save("D13", "mix", "02:05")

        mission = db.session.get(SortDateMission, self.mission.id)
        self.assertEqual(mission.actual_pure_pull_time_local, time(1, 55))
        self.assertEqual(mission.actual_mix_pull_time_local, time(2, 20))

    def test_existing_single_door_behavior_is_unchanged_without_link_selection(self):
        response = self._save("D1", "mix", "02:10")

        self.assertEqual(response.status_code, 200)
        pulls = self._pulls_by_door()
        self.assertEqual(set(pulls), {"D1"})
        self.assertEqual(pulls["D1"].actual_mix_pull_time_local, time(2, 10))

    def _assign(self, runout_key, field_name, destination):
        row = next(
            row
            for row in get_building_lineup_rows(self.gateway)
            if row.runout_key == runout_key
        )
        setattr(row, field_name, destination)
        db.session.flush()

    def _supervise(self, *doors):
        response = self.client.post(
            "/neoermac/door-view/supervision",
            data={"doors": list(doors), "active_door": doors[0] if doors else ""},
        )
        self.assertEqual(response.status_code, 302)

    def _save(self, door, pull_key, value="", no_pull=False):
        return self.client.post(
            "/neoermac/door-view/pull-autosave",
            data={
                "door": door,
                "destination": "SDF",
                "pull_key": pull_key,
                "actual_pull": value,
                "no_pull": "1" if no_pull else "0",
            },
        )

    def _pulls_by_door(self):
        return {
            pull.door: pull
            for pull in NeoErmacDoorPull.query.filter_by(
                gateway_id=self.gateway.id,
                sort_date_operation_id=self.operation.id,
                destination="SDF",
            ).all()
        }

    def _add_user(self):
        user = User(
            username="linked_door_operator",
            email="linked-door-operator@example.test",
            role="watcher",
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=self.gateway.id,
            status="approved",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()
        ermac = NeoNode.query.filter_by(code="ermac").one()
        db.session.add(
            GatewayNodeRole(
                gateway_membership_id=membership.id,
                node_id=ermac.id,
                role="operator",
                is_active=True,
            )
        )
        return user


if __name__ == "__main__":
    unittest.main()
