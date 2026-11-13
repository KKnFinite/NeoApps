"""NeoSubZero management authorization uses the RFD node role."""
from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import patch

from app.extensions import db
from app.models import (
    GatewayNodeRole, NeoNode, NeoSubZeroCalloutAssignment, NeoSubZeroTruck,
    NeoSubZeroUccAssignment, NeoSubZeroUccTruckAssignment, PermissionRule,
    SortDateParkingAssignment, StaffingDailyAttendance, StaffingPersonQualification,
)
from app.services.access_control import get_user_gateway_membership
from app.services.live_collaboration import entity_version
from app.services.neostaffing import deice_attendance_originals
from app.services.neosubzero_staffing import neosubzero_callout_context
from app.services.neosubzero_ucc import neosubzero_ucc_context, ucc_staffing_version
from app.services.permission_rules import (
    ensure_default_permission_rules, grouped_permission_rules, neosubzero_management_minimums,
)
from tests import test_neosubzero_ucc as fixtures
from tests.test_neosubzero_deicer_management import canonical_roster


ROLES = ("watcher", "operator", "simulator", "master", "grandmaster")
MANAGEMENT = "/neosubzero/deicer-management"
FLEET = "/neosubzero/truck-management"
UCC = "/neosubzero/ucc"


def set_subzero_role(user, role):
    membership = get_user_gateway_membership(user, "RFD")
    node = NeoNode.query.filter_by(code="subzero").one()
    node_role = GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id, node_id=node.id).one()
    node_role.role = role
    db.session.commit()


class SubZeroPermissionTest(TestCase):
    def setUp(self):
        self.f = fixtures.NeoSubZeroUccTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.operation = self.f.operation
        self.client = self.f.app.test_client()
        for target, value in (
            ("current_neosubzero_operation", self.operation),
            ("neosubzero_weather_context", self.f._weather_context_fixture()),
        ):
            patcher = patch("app.neonodes.neosubzero.routes." + target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def post(self, path, **values):
        return self.client.post(path, data={"operation_id": self.operation.id, **values},
                                headers={"X-UCC-Request": "1"})

    def test_seed_upgrades_only_untouched_legacy_callouts_and_registers_truck_rules(self):
        callout = PermissionRule.query.filter_by(permission_key="neosubzero.callouts.edit").one()
        callout.minimum_role = "master"
        callout.updated_at = callout.created_at
        PermissionRule.query.filter(PermissionRule.permission_key.like("neosubzero.truck_management.%")).delete()
        db.session.commit()
        ensure_default_permission_rules()
        db.session.commit()
        self.assertEqual(neosubzero_management_minimums(), {
            "neosubzero.callouts.edit": "simulator", "neosubzero.qualifications.edit": "simulator",
            "neosubzero.truck_management.view": "watcher", "neosubzero.truck_management.edit": "simulator",
            "neosubzero.ucc.edit": "simulator", "neosubzero.settings.edit": "master",
        })
        groups = grouped_permission_rules(PermissionRule.query.all())
        truck = next(item for group in groups for item in group["items"] if item["key"] == "neosubzero.truck_management")
        self.assertEqual([action["rule"].permission_key for action in truck["actions"]],
                         ["neosubzero.truck_management.edit"])
        callout.minimum_role = "master"
        callout.updated_at = callout.created_at + timedelta(days=1)
        db.session.commit()
        ensure_default_permission_rules()
        self.assertEqual(callout.minimum_role, "master")

    def test_roles_authorize_actual_management_and_ducc_mutations(self):
        authority_fixture = self.f._user("scope-fixture", "watcher")
        people, candidate = canonical_roster(self.f, authority_fixture)
        for role in ROLES:
            with self.subTest(role=role):
                self.exercise_role(role, people, candidate)

    def exercise_role(self, role, people, candidate):
        # A global Watcher with a higher node role can edit. A global Master
        # with a lower node role cannot.
        actor = self.f._user("node-" + role, "watcher" if role in ROLES[2:] else "master")
        set_subzero_role(actor, role)
        self.f._login(self.client, actor)
        editable = role in ROLES[2:]
        qualify = dict(person_id=candidate.id, action="qualify", expected_version=entity_version(
            StaffingPersonQualification.query.filter_by(person_id=candidate.id).first()))
        callout = NeoSubZeroCalloutAssignment.query.filter_by(
            sort_date_operation_id=self.operation.id, person_id=candidate.id).first()
        add = dict(person_id=candidate.id, action="add", expected_version=entity_version(callout))
        result = self.post(MANAGEMENT + "?tab=eligibility", **qualify)
        self.assertEqual(result.status_code, 302 if editable else 403)
        result = self.post(MANAGEMENT + "?tab=callouts", **add)
        self.assertEqual(result.status_code, 302 if editable else 403)
        number = "ROLE-" + role.upper()
        result = self.post(FLEET, action="add_truck", truck_number=number)
        self.assertEqual(result.status_code, 302 if editable else 403)
        slot = dict(ramp="Alpha", position_number=1)
        if not editable:
            self.assertIsNone(NeoSubZeroTruck.query.filter_by(number=number).first())
            for path, values in (
                (UCC, {**slot, "team_role": "driver", "person_id": people[0].id}),
                ("/neosubzero/truck", {**slot, "board": "ucc", "truck_number": number}),
                (UCC + "/unit", {"resolution": "move"}),
                (UCC + "/queue", {"direction": "forward"}),
            ):
                self.assertEqual(self.post(path, **values).status_code, 403, path)
            return

        qualification = StaffingPersonQualification.query.filter_by(person_id=candidate.id).one()
        callout = NeoSubZeroCalloutAssignment.query.filter_by(
            sort_date_operation_id=self.operation.id, person_id=candidate.id).one()
        self.assertTrue(qualification.active and callout.active)
        self.post(MANAGEMENT + "?tab=eligibility", person_id=candidate.id, action="unqualify",
                  expected_version=qualify["expected_version"])
        self.post(MANAGEMENT + "?tab=callouts", person_id=candidate.id, action="remove",
                  expected_version=add["expected_version"])
        self.assertTrue(qualification.active and callout.active)
        self.post(MANAGEMENT + "?tab=callouts", person_id=candidate.id, action="remove",
                  expected_version=entity_version(callout))
        self.assertFalse(callout.active)
        self.post(MANAGEMENT + "?tab=eligibility", person_id=candidate.id, action="unqualify",
                  expected_version=entity_version(qualification))
        self.assertFalse(qualification.active)

        truck = NeoSubZeroTruck.query.filter_by(number=number).one()
        initial = entity_version(truck)
        self.post(FLEET, action="edit_truck", truck_id=truck.id, expected_version=initial, truck_number=number + "-EDIT")
        self.assertEqual(truck.number, number + "-EDIT")
        self.post(FLEET, action="deactivate_truck", truck_id=truck.id, expected_version=initial, confirm="yes")
        self.assertTrue(truck.active)
        self.post(FLEET, action="deactivate_truck", truck_id=truck.id, expected_version=entity_version(truck), confirm="yes")
        self.assertFalse(truck.active)
        self.assertEqual(self.post("/neosubzero/truck", board="ucc", truck_number=truck.number, **slot).status_code, 409)
        self.post(FLEET, action="activate_truck", truck_id=truck.id, expected_version=entity_version(truck), confirm="yes")
        self.assertTrue(truck.active)

        for ramp in ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Remote", "Early Out"):
            slot = dict(ramp=ramp, position_number=1)
            for team_role, person in zip(("driver", "flyer"), people):
                current = NeoSubZeroUccAssignment.query.filter_by(
                    sort_date_operation_id=self.operation.id, team_role=team_role, **slot).first()
                values = dict(team_role=team_role, **slot)
                result = self.post(UCC, **values, person_id=person.id, expected_version=entity_version(current))
                self.assertEqual(result.status_code, 200, result.json)
                assignment = NeoSubZeroUccAssignment.query.filter_by(
                    sort_date_operation_id=self.operation.id, **values).one()
                self.assertEqual(assignment.person_id, person.id)
                self.assertEqual(self.post(UCC, **values, person_id="", expected_version="stale").status_code, 409)
                self.assertEqual(self.post(UCC, **values, person_id="",
                                           expected_version=entity_version(assignment)).status_code, 200)
            current = NeoSubZeroUccTruckAssignment.query.filter_by(sort_date_operation_id=self.operation.id, **slot).first()
            result = self.post("/neosubzero/truck", board="ucc", **slot, truck_number=truck.number,
                               expected_version=entity_version(current))
            self.assertEqual(result.status_code, 200, result.json)
            current = NeoSubZeroUccTruckAssignment.query.filter_by(sort_date_operation_id=self.operation.id, **slot).one()
            self.assertEqual(self.post("/neosubzero/truck", board="ucc", **slot, truck_number="",
                                       expected_version=entity_version(current)).status_code, 200)

        for action, source, destination in (("move", "Alpha", "Bravo"), ("swap", "Bravo", "Charlie")):
            occupied = "Alpha" if action == "move" else "Charlie"
            slot = dict(ramp=occupied, position_number=2, team_role="driver")
            current = NeoSubZeroUccAssignment.query.filter_by(sort_date_operation_id=self.operation.id, **slot).first()
            result = self.post(UCC, **slot, expected_version=entity_version(current),
                               person_id=people[0 if action == "move" else 1].id)
            self.assertEqual(result.status_code, 200, result.json)
            values = dict(source_slot=source + "|2", destination_ramp=destination,
                          destination_position=2, resolution=action, expected_version=ucc_staffing_version(self.operation))
            result = self.post(UCC + "/unit", **values)
            self.assertEqual(result.status_code, 200, result.json)
            self.assertEqual(self.post(UCC + "/unit", **values).status_code, 409)
        for assignment in NeoSubZeroUccAssignment.query.filter_by(sort_date_operation_id=self.operation.id, position_number=2).all():
            self.assertEqual(self.post(UCC, ramp=assignment.ramp, position_number=2, team_role=assignment.team_role,
                                       person_id="", expected_version=entity_version(assignment)).status_code, 200)

        self.post(FLEET, action="delete_truck", truck_id=truck.id, expected_version=entity_version(truck), confirm="yes")
        self.assertIsNotNone(truck.archived_at_utc)
        self.assertEqual([row["actor_id"] for row in truck.history], [actor.id] * 5)
        settings = self.post("/neosubzero/settings", action="save_fluids", type_i_fluid_name="Test Type I",
                             type_i_concentration_percent="50", type_iv_fluid_name="Test Type IV")
        self.assertEqual(settings.status_code, 403 if role == "simulator" else 302)

    def test_queue_and_permanent_authority_use_independent_canonical_checks(self):
        lead = self.f._user("scope-lead", "watcher")
        set_subzero_role(lead, "simulator")
        people, outsider = canonical_roster(self.f, lead)
        worker = people[0]
        other = self.f._user("unscoped-management", "watcher")
        set_subzero_role(other, "grandmaster")
        self.f._login(self.client, other)
        self.assertEqual(self.post(MANAGEMENT + "?tab=permanent").status_code, 403)
        self.f._login(self.client, lead)
        context = neosubzero_callout_context(self.operation)
        original = deice_attendance_originals(self.operation, context["permanent"])[worker.id]["original"]
        attendance = dict(sort_date_operation_id=self.operation.id, **{
            f"status_{worker.id}": "here", f"original_{worker.id}": original,
        })
        self.assertEqual(self.post(MANAGEMENT + "?tab=permanent", **attendance).status_code, 200)
        self.assertEqual(self.post(MANAGEMENT + "?tab=permanent", **attendance).status_code, 409)
        self.assertEqual(self.post(MANAGEMENT + "?tab=permanent", sort_date_operation_id=self.operation.id,
                                   **{f"status_{outsider.id}": "here"}).status_code, 409)
        self.assertEqual(StaffingDailyAttendance.query.one().person_id, worker.id)

        second = self.f._departure("5X300", "N300", "DEN", 3)
        db.session.add(SortDateParkingAssignment(sort_date_operation_id=self.operation.id,
                                               tail_number="N300", ramp_code="A", position_code="A04"))
        for minute, mission in enumerate((self.f.alpha, second)):
            mission.actual_block_out_datetime_utc = datetime(2026, 9, 1, 1, minute)
            self.f._deice_event(mission, "deice_planned")
        db.session.commit()
        for role in ROLES[2:]:
            actor = self.f._user("queue-" + role, "watcher")
            set_subzero_role(actor, role)
            self.f._login(self.client, actor)
            ramp = neosubzero_ucc_context(self.f.gateway, self.operation)["ramps"][0]
            values = dict(ramp="Alpha", mission_id=ramp["throat"]["mission_id"], direction="forward",
                          expected_version=ramp["queue_version"])
            result = self.post(UCC + "/queue", **values)
            self.assertEqual(result.status_code, 200, result.json)
            self.assertEqual(self.post(UCC + "/queue", **values).status_code, 409)
