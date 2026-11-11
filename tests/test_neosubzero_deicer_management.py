"""Isolated checks for Deicer Management and pre-sort DUCC staffing."""

from datetime import datetime
import unittest
from unittest.mock import patch

from app.extensions import db
from app.models import (
    GatewaySortMatrix, NeoSubZeroCalloutAssignment, NeoSubZeroUccAssignment,
    SortDateOperation, StaffingDailyAttendance, StaffingLeadershipAssignment,
    StaffingPersonQualification, StaffingUnit,
)
from app.services.live_collaboration import entity_version
from app.services.neostaffing import deice_attendance_originals, save_employee_editor
from app.services.neosubzero_staffing import neosubzero_callout_context
from app.services.neosubzero_trucks import manage_truck
from app.services.neosubzero_ucc import neosubzero_ucc_context
from tests import test_neosubzero_ucc as fixtures


def canonical_roster(fixture, manager):
    night = StaffingUnit.query.filter_by(unit_type="sort", name="Night").one()
    ramp = fixture._unit("operation", "Ramp", night)
    aviation = fixture._unit("department", "Aviation Services", ramp)
    deice = fixture._unit("work_area", "Deice", aviation)
    supervisor = fixture._person(manager.employee_id, "Deice", "Supervisor", deice)
    supervisor.classification = "full_time_supervisor"
    db.session.delete(supervisor.work_assignments[0])
    db.session.add(StaffingLeadershipAssignment(
        person_id=supervisor.id, unit_id=aviation.id, leadership_level="department",
    ))
    fixture.permanent.work_assignments[0].work_area_unit_id = deice.id
    for record in fixture.permanent.qualifications:
        db.session.delete(record)
    people = [fixture.permanent] + [
        fixture._person(f"DICE-{index}", f"Permanent {index}", "Deice", deice)
        for index in range(1, 5)
    ]
    shift = fixture._unit("department", "Shift", ramp)
    door = fixture._unit("work_area", "Door 1", shift)
    fixture.callout.work_assignments[0].work_area_unit_id = door.id
    candidate = fixture._person("DICE-CALL", "New", "Callout", door)
    db.session.commit()
    return people, candidate


class DeicerManagementTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.NeoSubZeroUccTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.manager = self.f._user("deice-manager", "grandmaster")
        self.people, self.candidate = canonical_roster(self.f, self.manager)
        self.client = self.f.app.test_client()
        self.f._login(self.client, self.manager)

    def test_pre_sort_attendance_callouts_and_early_out(self):
        f, client = self.f, self.client
        start = datetime(2026, 9, 1, 15)
        db.session.add(GatewaySortMatrix(
            gateway_id=f.gateway.id, gateway_code=f.gateway.code, day_of_week="tuesday",
            sort_name="night", is_active=True,
        ))
        db.session.commit()
        path = "/neosubzero/deicer-management"
        headers = {"X-UCC-Request": "1"}
        with (
            patch("app.services.operation_lifecycle.current_gateway_local_datetime", return_value=start),
            patch("app.neonodes.neosubzero.routes.current_neosubzero_operation", return_value=None),
            patch("app.neonodes.neosubzero.routes.neosubzero_weather_context", return_value=f._weather_context_fixture()),
        ):
            eligibility = client.get(path + "?tab=eligibility")
            self.assertEqual(eligibility.status_code, 200)
            self.assertEqual(SortDateOperation.query.count(), 1)
            self.assertIn(b"DICE-CALL", eligibility.data)
            client.post(path + "?tab=eligibility", data={
                "person_id": self.candidate.id, "action": "qualify", "expected_version": "",
            })
            authorization = StaffingPersonQualification.query.filter_by(person_id=self.candidate.id).one()
            self.assertTrue(authorization.active)
            self.assertEqual(authorization.change_history[0]["actor_user_id"], self.manager.id)
            with self.assertRaisesRegex(ValueError, "Deicer Management"):
                save_employee_editor(self.candidate, {"deice_trained": "no"})
            self.assertTrue(authorization.active)

            permanent = client.get(path)
            self.assertEqual(permanent.status_code, 200)
            operation = SortDateOperation.query.filter_by(sort_date=start.date(), sort_name="night").one()
            self.assertIsNone(operation.generated_by_user_id)
            context = neosubzero_callout_context(operation)
            self.assertEqual({row["person"].id for row in context["permanent"]}, {person.id for person in self.people})
            self.assertEqual(len(context["staffing_pool"]), 5)
            self.assertIn(b"SCHEDULED OFF", permanent.data)
            self.assertNotIn(b"ATTENDANCE COMPLETE", permanent.data)

            callout_form = {"operation_id": operation.id, "person_id": self.candidate.id,
                            "action": "add", "expected_version": ""}
            client.post(path + "?tab=callouts", data=callout_form)
            callout = NeoSubZeroCalloutAssignment.query.filter_by(
                sort_date_operation_id=operation.id, person_id=self.candidate.id,
            ).one()
            self.assertTrue(callout.active)
            self.assertIn(b"DICE-CALL", client.get(path + "?tab=callouts").data)

            page = client.get("/neosubzero/ucc")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'data-ucc-early-out', page.data)
            self.assertIn(b'data-ucc-whos-left', page.data)
            self.assertEqual(SortDateOperation.query.count(), 2)
            manage_truck(f.gateway, "add_truck", {"truck_number": "DICE-12"}, actor_id=self.manager.id)
            db.session.commit()
            truck_data = {"board": "ucc", "operation_id": operation.id, "truck_number": "DICE-12",
                          "position_number": 1, "ramp": "Early Out"}
            self.assertEqual(client.post("/neosubzero/truck", headers=headers, data=truck_data).status_code, 200)
            self.assertEqual(client.post("/neosubzero/truck", headers=headers,
                                         data={**truck_data, "ramp": "Alpha"}).status_code, 409)
            permanent_person = self.people[0]
            for ramp, person in (("Early Out", permanent_person), ("Alpha", permanent_person), ("Remote", self.candidate)):
                response = client.post("/neosubzero/ucc", headers=headers, data={
                    "operation_id": operation.id, "person_id": person.id, "position_number": 1,
                    "team_role": "driver", "ramp": ramp,
                })
                self.assertEqual(response.status_code, 200, response.get_json())
                if ramp == "Early Out":
                    remaining = neosubzero_ucc_context(f.gateway, operation)["whos_left"]
                    self.assertIn(permanent_person.id, {row["person"].id for row in remaining})
            self.assertEqual(NeoSubZeroUccAssignment.query.filter_by(person_id=permanent_person.id).count(), 2)
            for ramp in ("Bravo", "Early Out"):
                duplicate = client.post("/neosubzero/ucc", headers=headers, data={
                    "operation_id": operation.id, "person_id": permanent_person.id,
                    "position_number": 2, "team_role": "flyer", "ramp": ramp,
                })
                self.assertEqual(duplicate.status_code, 409)

            original = deice_attendance_originals(operation, context["permanent"])[permanent_person.id]
            values = {"sort_date_operation_id": operation.id, f"status_{permanent_person.id}": "call_in",
                      f"note_{permanent_person.id}": "Coverage arranged", f"original_{permanent_person.id}": original["original"]}
            self.assertEqual(client.post(path + "?tab=permanent", data=values).status_code, 200)
            saved = StaffingDailyAttendance.query.filter_by(person_id=permanent_person.id).one()
            self.assertEqual((saved.status, saved.note, saved.updated_by_user_id),
                             ("call_in", "Coverage arranged", self.manager.id))
            self.assertEqual(client.post(path + "?tab=permanent", data=values).status_code, 409)
            self.assertEqual(NeoSubZeroUccAssignment.query.filter_by(person_id=permanent_person.id).count(), 2)
            self.assertIn(b"ucc-person-unavailable", client.get("/neosubzero/ucc").data)

            client.post(path + "?tab=eligibility", data={
                "person_id": self.candidate.id, "action": "unqualify", "expected_version": entity_version(authorization),
            })
            self.assertFalse(authorization.active)
            self.assertEqual([item["action"] for item in authorization.change_history], ["grant", "revoke"])
            self.assertFalse(callout.active)
            self.assertEqual(NeoSubZeroUccAssignment.query.filter_by(person_id=self.candidate.id).count(), 1)

            viewer = f._user("deice-viewer", "watcher")
            f._login(client, viewer)
            self.assertEqual(client.post(path + "?tab=permanent", data=values).status_code, 403)
            self.assertEqual(client.post(path + "?tab=eligibility", data={"person_id": self.candidate.id}).status_code, 403)

    def test_ramp_rollout_uses_home_status_only_when_enabled(self):
        f = self.f
        night = StaffingUnit.query.filter_by(unit_type="sort", name="Night").one()
        ramp = StaffingUnit.query.filter_by(parent_id=night.id, name="Ramp").one()
        ramp_area = f._unit("work_area", "Ramp Crew", ramp)
        f.callout.work_assignments[0].work_area_unit_id = ramp_area.id
        db.session.add(StaffingDailyAttendance(
            person_id=f.callout.id, attendance_date=f.operation.sort_date, sort_unit_id=night.id,
            sort_date_operation_id=f.operation.id, work_area_unit_id=ramp_area.id, status="call_in",
        ))
        db.session.commit()
        selected = neosubzero_callout_context(f.operation)["callouts"][0]
        self.assertEqual((selected["attendance_source"], selected["available"]), ("pending_rollout", True))
        f.app.config["NEOSUBZERO_RAMP_ATTENDANCE_ENABLED"] = True
        selected = neosubzero_callout_context(f.operation)["callouts"][0]
        self.assertEqual((selected["attendance_source"], selected["available"]), ("home", False))
        self.assertTrue(f.callout_row.active)
        self.assertEqual(len(neosubzero_callout_context(f.operation)["staffing_pool"]), 5)
