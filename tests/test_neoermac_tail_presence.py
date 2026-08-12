from datetime import date, datetime, time
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoErmacBuildingLineup,
    NeoNode,
    SortDateGoogleMissionLink,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoermac_door_view import door_view_uld_state
from app.services.neoermac_tail_presence import (
    PRESENCE_EVIDENCE_ACTUAL_BLOCK_IN,
    PRESENCE_EVIDENCE_API_ASSUMED_ARRIVED,
    PRESENCE_EVIDENCE_GOOGLE_HERE,
    TAIL_PRESENCE_ARRIVED,
    TAIL_PRESENCE_ASSUMED_HERE,
    TAIL_PRESENCE_NOT_ARRIVED,
    TAIL_PRESENCE_TBD,
    arrival_presence_by_tail,
    departure_tail_presence,
    tail_presence_status_override,
)
from app.services.neoermac_view_outbound import view_outbound_context
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoErmacTailPresenceTest(unittest.TestCase):
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
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date=date(2026, 8, 11),
        )
        db.session.add(self.operation)
        db.session.flush()
        db.session.add(
            NeoErmacBuildingLineup(
                gateway_id=self.gateway.id,
                runout_key="runout_10",
                runout_name="D32-D34 Belts",
                west_destination_1="SDF",
            )
        )
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_no_tail_displays_tail_tbd_and_hides_door_parking(self):
        mission = self._departure(tail=None)
        assignment = self._parking("NUNASSIGNED", "A01")
        db.session.commit()

        card = self._door_card()
        outbound = self._outbound_row()

        self.assertEqual(card["status"], "TAIL TBD")
        self.assertEqual(card["parking"], "-")
        self.assertEqual(card["tail_presence"]["state"], TAIL_PRESENCE_TBD)
        self.assertFalse(card["tail_presence"]["is_present"])
        self.assertEqual(outbound["status"], "TAIL TBD")
        self.assertIsNone(mission.assigned_tail_number)
        self.assertEqual(assignment.position_code, "A01")

    def test_no_matching_arrival_assumes_tail_is_already_here(self):
        self._departure(tail="N123UP")
        self._parking("N123UP", "A01")
        db.session.commit()

        card = self._door_card()

        self.assertEqual(card["status"], "Scheduled")
        self.assertEqual(card["parking"], "A01")
        self.assertEqual(
            card["tail_presence"]["state"],
            TAIL_PRESENCE_ASSUMED_HERE,
        )
        self.assertTrue(card["tail_presence"]["is_present"])
        self.assertFalse(card["tail_presence"]["has_matching_arrival"])

    def test_matching_arrival_without_block_in_hides_parking_only_in_door_view(self):
        self._departure(tail="N123UP")
        self._arrival(tail="N123UP")
        assignment = self._parking("N123UP", "A01")
        db.session.commit()

        card = self._door_card()
        outbound = self._outbound_row()

        self.assertEqual(card["status"], "TAIL NOT ARRIVED")
        self.assertEqual(card["parking"], "-")
        self.assertEqual(
            card["tail_presence"]["state"],
            TAIL_PRESENCE_NOT_ARRIVED,
        )
        self.assertEqual(outbound["status"], "TAIL NOT ARRIVED")
        self.assertEqual(outbound["parking"], "A01")
        self.assertEqual(assignment.position_code, "A01")

    def test_cancelled_arrival_without_block_in_remains_not_arrived(self):
        self._departure(tail="N123UP")
        self._arrival(tail="N123UP", arrival_status="cancelled")
        self._parking("N123UP", "A01")
        db.session.commit()

        card = self._door_card()

        self.assertEqual(card["status"], "TAIL NOT ARRIVED")
        self.assertEqual(card["parking"], "-")
        self.assertFalse(card["tail_presence"]["has_actual_block_in"])

    def test_cancelled_arrival_with_block_in_is_treated_as_here(self):
        self._departure(tail="N123UP")
        self._arrival(
            tail="N123UP",
            arrival_status="cancelled",
            actual_block_in=datetime(2026, 8, 12, 1, 30),
        )
        self._parking("N123UP", "A01")
        db.session.commit()

        card = self._door_card()

        self.assertEqual(card["status"], "Scheduled")
        self.assertEqual(card["parking"], "A01")
        self.assertEqual(card["tail_presence"]["state"], TAIL_PRESENCE_ARRIVED)
        self.assertTrue(card["tail_presence"]["has_actual_block_in"])

    def test_api_assumed_arrived_is_present_without_fake_block_in(self):
        self._departure(tail="N123UP")
        arrival = self._arrival(tail="N123UP")
        arrival.api_assumed_arrived_time_utc = datetime(2026, 8, 11, 20, 0)
        self._parking("N123UP", "A01")
        db.session.commit()

        card = self._door_card()

        self.assertEqual(card["status"], "Scheduled")
        self.assertEqual(card["parking"], "A01")
        self.assertEqual(card["tail_presence"]["state"], TAIL_PRESENCE_ARRIVED)
        evidence = arrival_presence_by_tail(self.operation)["N123UP"]
        self.assertEqual(
            evidence["presence_evidence"],
            PRESENCE_EVIDENCE_API_ASSUMED_ARRIVED,
        )
        self.assertIsNone(arrival.actual_block_in_datetime_utc)

    def test_google_here_is_present_without_fake_block_in(self):
        self._departure(tail="N123UP")
        arrival = self._arrival(tail="N123UP")
        self._google_link(arrival, tail="N123UP", status="HERE")
        self._parking("N123UP", "A01")
        db.session.commit()

        card = self._door_card()

        self.assertEqual(card["status"], "Scheduled")
        self.assertEqual(card["parking"], "A01")
        self.assertEqual(card["tail_presence"]["state"], TAIL_PRESENCE_ARRIVED)
        evidence = arrival_presence_by_tail(self.operation)["N123UP"]
        self.assertEqual(
            evidence["presence_evidence"],
            PRESENCE_EVIDENCE_GOOGLE_HERE,
        )
        self.assertIsNone(arrival.actual_block_in_datetime_utc)

    def test_actual_block_in_wins_over_api_and_google_presence(self):
        self._departure(tail="N123UP")
        arrival = self._arrival(tail="N123UP")
        arrival.api_assumed_arrived_time_utc = datetime(2026, 8, 11, 20, 0)
        self._google_link(arrival, tail="N123UP", status="HERE")
        db.session.commit()

        before = arrival_presence_by_tail(self.operation)["N123UP"]
        arrival.actual_block_in_datetime_utc = datetime(2026, 8, 12, 1, 30)
        arrival.actual_block_in_source = "manual"
        db.session.commit()
        after = arrival_presence_by_tail(self.operation)["N123UP"]

        self.assertEqual(
            before["presence_evidence"],
            PRESENCE_EVIDENCE_GOOGLE_HERE,
        )
        self.assertEqual(
            after["presence_evidence"],
            PRESENCE_EVIDENCE_ACTUAL_BLOCK_IN,
        )
        self.assertTrue(after["has_actual_block_in"])
        self.assertEqual(
            arrival.actual_block_in_datetime_utc,
            datetime(2026, 8, 12, 1, 30),
        )

    def test_stale_google_here_for_different_tail_is_ignored(self):
        self._departure(tail="N123UP")
        arrival = self._arrival(tail="N123UP")
        self._google_link(arrival, tail="NOLDUP", status="HERE")
        self._parking("N123UP", "A01")
        db.session.commit()

        card = self._door_card()

        self.assertEqual(card["status"], "TAIL NOT ARRIVED")
        self.assertEqual(card["parking"], "-")
        self.assertFalse(card["tail_presence"]["is_present"])

    def test_cancelled_arrival_with_google_here_is_present(self):
        self._departure(tail="N123UP")
        arrival = self._arrival(tail="N123UP", arrival_status="cancelled")
        self._google_link(arrival, tail="N123UP", status="HERE")
        self._parking("N123UP", "A01")
        db.session.commit()

        card = self._door_card()

        self.assertEqual(card["status"], "Scheduled")
        self.assertEqual(card["parking"], "A01")
        self.assertIsNone(arrival.actual_block_in_datetime_utc)

    def test_google_block_in_and_normalized_tail_match_are_authoritative(self):
        self._departure(tail=" n123 up ")
        arrival = self._arrival(
            tail="N123UP",
            actual_block_in=datetime(2026, 8, 12, 1, 30),
        )
        arrival.actual_block_in_source = "google_motherbrain"
        self._parking("N123UP", "A01")
        db.session.commit()

        card = self._door_card()

        self.assertEqual(card["tail_presence"]["tail"], "N123UP")
        self.assertEqual(card["tail_presence"]["state"], TAIL_PRESENCE_ARRIVED)
        self.assertEqual(card["parking"], "A01")
        self.assertEqual(arrival.actual_block_in_source, "google_motherbrain")

    def test_mid_sort_tail_swap_recalculates_presence_from_new_tail_live(self):
        departure = self._departure(tail="NOLDUP")
        self._arrival(
            tail="NOLDUP",
            actual_block_in=datetime(2026, 8, 12, 0, 30),
        )
        new_arrival = self._arrival(tail="NNEWUP", flight_number="UPS102")
        old_assignment = self._parking("NOLDUP", "A01")
        new_assignment = self._parking("NNEWUP", "B02")
        db.session.commit()
        self._login()

        before = self.client.get(
            "/neoermac/door-view/state?door=D34"
        ).get_json()["state"]["destinations"][0]

        departure.assigned_tail_number = "NNEWUP"
        db.session.commit()
        after_swap = self.client.get(
            "/neoermac/door-view/state?door=D34"
        ).get_json()["state"]["destinations"][0]

        new_arrival.actual_block_in_datetime_utc = datetime(2026, 8, 12, 1, 45)
        new_arrival.actual_block_in_source = "api"
        db.session.commit()
        after_arrival = self.client.get(
            "/neoermac/door-view/state?door=D34"
        ).get_json()["state"]["destinations"][0]

        self.assertEqual(before["tail"], "NOLDUP")
        self.assertEqual(before["parking"], "A01")
        self.assertEqual(before["tail_presence"]["state"], TAIL_PRESENCE_ARRIVED)
        self.assertEqual(after_swap["tail"], "NNEWUP")
        self.assertEqual(after_swap["status"], "TAIL NOT ARRIVED")
        self.assertEqual(after_swap["parking"], "-")
        self.assertEqual(
            after_swap["tail_presence"]["state"],
            TAIL_PRESENCE_NOT_ARRIVED,
        )
        self.assertEqual(after_arrival["tail"], "NNEWUP")
        self.assertEqual(after_arrival["status"], "Scheduled")
        self.assertEqual(after_arrival["parking"], "B02")
        self.assertEqual(after_arrival["tail_presence"]["state"], TAIL_PRESENCE_ARRIVED)
        self.assertEqual(old_assignment.position_code, "A01")
        self.assertEqual(new_assignment.position_code, "B02")

    def test_meaningful_departure_statuses_are_never_masked(self):
        mission = self._departure(tail="N123UP")
        self._arrival(tail="N123UP")
        db.session.commit()
        presence = departure_tail_presence(
            mission,
            arrival_presence_by_tail(self.operation),
        )

        statuses = (
            "loading",
            "last_uld_enroute",
            "ramp_load_complete",
            "crew_load_complete",
            "blocked_out",
            "departed",
            "cancelled",
        )
        for status in statuses:
            with self.subTest(status=status):
                mission.departure_status = status
                self.assertIsNone(tail_presence_status_override(mission, presence))

        mission.departure_status = "loading"
        db.session.flush()
        card = self._door_card()
        self.assertEqual(card["status"], "Loading")
        self.assertEqual(card["parking"], "-")

    def test_live_page_and_payload_include_selective_presence_reconciliation(self):
        self._departure(tail="N123UP")
        self._arrival(tail="N123UP")
        self._parking("N123UP", "A01")
        db.session.commit()
        self._login()

        page = self.client.get("/neoermac/door-view?door=D34")
        payload = self.client.get(
            "/neoermac/door-view/state?door=D34"
        ).get_json()["state"]["destinations"][0]

        self.assertEqual(page.status_code, 200)
        self.assertIn(b'data-tail-presence="not_arrived"', page.data)
        self.assertIn(b"card.dataset.tailPresence", page.data)
        self.assertIn(b"tail.textContent = cardData.tail", page.data)
        self.assertIn(b"parking.textContent = cardData.parking", page.data)
        self.assertIn(b"status.textContent = cardData.status", page.data)
        self.assertNotIn(b"window.location.reload", page.data)
        self.assertEqual(payload["status"], "TAIL NOT ARRIVED")
        self.assertEqual(payload["parking"], "-")
        self.assertEqual(payload["tail_presence"]["state"], TAIL_PRESENCE_NOT_ARRIVED)

        arrival = SortDateMission.query.filter_by(mission_type="arrival").one()
        self._google_link(arrival, tail="N123UP", status="HERE")
        db.session.commit()
        reconciled = self.client.get(
            "/neoermac/door-view/state?door=D34"
        ).get_json()["state"]["destinations"][0]

        self.assertEqual(reconciled["status"], "Scheduled")
        self.assertEqual(reconciled["parking"], "A01")
        self.assertEqual(
            reconciled["tail_presence"]["state"],
            TAIL_PRESENCE_ARRIVED,
        )

    def _door_card(self):
        return door_view_uld_state(self.gateway, "D34")["destinations"][0]

    def _outbound_row(self):
        return view_outbound_context(self.gateway)["rows"][0]

    def _departure(self, *, tail, departure_status="scheduled"):
        mission = SortDateMission(
            sort_date=self.operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name=self.operation.sort_name,
            sort_date_operation_id=self.operation.id,
            mission_type="departure",
            mission_source="master",
            wave="1",
            flight_number="UPS101",
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 12, 3, 0),
            planned_datetime_utc=datetime(2026, 8, 12, 8, 0),
            planned_source="master",
            assigned_tail_number=tail,
            departure_status=departure_status,
            pure_pull_time_local=time(2, 15),
            mix_pull_time_local=time(2, 30),
        )
        db.session.add(mission)
        db.session.flush()
        return mission

    def _arrival(
        self,
        *,
        tail,
        flight_number="UPS100",
        arrival_status="scheduled",
        actual_block_in=None,
    ):
        mission = SortDateMission(
            sort_date=self.operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name=self.operation.sort_name,
            sort_date_operation_id=self.operation.id,
            mission_type="arrival",
            mission_source="master",
            wave="1",
            flight_number=flight_number,
            origin="SDF",
            destination=self.gateway.code,
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 11, 23, 30),
            planned_datetime_utc=datetime(2026, 8, 12, 4, 30),
            planned_source="master",
            assigned_tail_number=tail,
            arrival_status=arrival_status,
            actual_block_in_datetime_utc=actual_block_in,
            actual_block_in_source="manual" if actual_block_in else "unknown",
        )
        db.session.add(mission)
        db.session.flush()
        return mission

    def _parking(self, tail, position):
        assignment = SortDateParkingAssignment(
            sort_date_operation_id=self.operation.id,
            tail_number=tail,
            ramp_code=position[0],
            position_code=position,
            lane_number=1,
        )
        db.session.add(assignment)
        db.session.flush()
        return assignment

    def _google_link(self, arrival, *, tail, status):
        link = SortDateGoogleMissionLink(
            sort_date_operation_id=self.operation.id,
            sort_date_mission_id=arrival.id,
            mission_type="arrival",
            source_sheet="Inbound",
            source_row=4 + SortDateGoogleMissionLink.query.count(),
            last_flight_number=arrival.flight_number,
            last_tail_number=tail,
            last_status_raw=status,
        )
        db.session.add(link)
        db.session.flush()
        return link

    def _login(self):
        user = User(
            username="ermac_presence_operator",
            email="ermac_presence_operator@example.test",
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
        db.session.commit()
        return self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
