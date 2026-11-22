import re
import unittest
from hashlib import sha256
from pathlib import Path
from datetime import date, datetime, time
from unittest.mock import patch
from sqlalchemy import event

from app.extensions import db
from app.models import Gateway, GatewayMembership, GatewayNodeRole, NeoErmacDoorPull, NeoNode, SortDateMission, SortDateOperation, SortDateParkingAssignment
from app.models.account_preference import AccountOperationalScreen
from app.neonodes.neoraiden.services import snapshot
from tests import test_gateway_shell as fixtures


def seed_sort(gateway):
    op = SortDateOperation(gateway_id=gateway.id, gateway_code=gateway.code, sort_date=date(2026, 4, 12), sort_name="night")
    db.session.add(op)
    db.session.flush()
    missions = []
    for number, tail, hour, eta in (("FX101","N101",4,6), ("FX202","N202",5,5)):
        m = SortDateMission(sort_date_operation_id=op.id, sort_date=op.sort_date, sort_name=op.sort_name,
            gateway_code=gateway.code, flight_number=number, assigned_tail_number=tail, origin=gateway.code,
            destination="OAK", mission_type="departure", planned_datetime_utc=datetime(2026,4,13,hour),
            eta_datetime_utc=datetime(2026,4,13,eta))
        db.session.add(m)
        missions.append(m)
    db.session.flush()
    db.session.add(SortDateParkingAssignment(sort_date_operation_id=op.id,tail_number="N202",ramp_code="B",position_code="B02"))
    db.session.add(NeoErmacDoorPull(gateway_id=gateway.id,sort_date_operation_id=op.id,sort_date_mission_id=missions[0].id,
        destination="OAK",door="D13", actual_pure_pull_time_local=time(1,25)))
    db.session.commit()
    return op


class RaidenTest(unittest.TestCase):
    setUp = fixtures.GatewayShellTest.setUp
    tearDown = fixtures.GatewayShellTest.tearDown

    def test_native_staffing_distinguishes_missing_zero_and_here(self):
        from app.models import StaffingDailyAttendance
        from tests.neoraiden_fixtures import record_attendance

        op = seed_sort(self.gateway)
        with self.app.test_request_context(), patch("requests.sessions.Session.request",
                side_effect=AssertionError("native staffing attempted external HTTP")):
            def here(operation):
                return snapshot(self.user, self.gateway, operation)["marked_here"]

            self.assertIsNone(here(op))
            record_attendance(op)
            self.assertEqual(here(op), 1)
            attendance = StaffingDailyAttendance.query.filter_by(sort_date_operation_id=op.id)
            attendance.filter_by(status="here").one().status = "vacation"
            db.session.commit()
            self.assertEqual(here(op), 0)
            other = SortDateOperation(gateway_id=self.gateway.id, gateway_code=self.gateway.code,
                                      sort_date=date(2026, 4, 13), sort_name="night")
            db.session.add(other)
            db.session.commit()
            self.assertIsNone(here(other))

    def test_automatic_state_is_bounded_by_physical_sort_hours(self):
        from datetime import timedelta
        from tests.test_neorain_sort_refresh import configure_sort_window
        op = seed_sort(self.gateway)
        _, start = configure_sort_window(self.gateway, op.sort_date)
        path = f"/neoraiden/state?operation_id={op.id}"
        for minutes, status in ((-1,204),(0,200),(479,200),(480,204),(1440,204)):
            self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = start + timedelta(minutes=minutes)
            self.assertEqual(self.client.get(path, headers={"X-Raiden-Automatic":"1"}).status_code, status)
        self.assertEqual(self.client.get(path).status_code, 200)
        op.archived_at_utc = start
        db.session.commit()
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = start
        self.assertEqual(self.client.get(path, headers={"X-Raiden-Automatic":"1"}).status_code, 204)

    def test_recorded_uld_projection_and_wave_lifecycle(self):
        from datetime import timedelta
        from app.models import NeoSektorOperationalSetting, NeoSektorBallmatWaveCount, NeoSektorWaveState
        from app.services.neosektor_live_counts import native_uld_summary
        from tests.neoraiden_fixtures import record_counts

        op = seed_sort(self.gateway)
        db.session.add(NeoSektorOperationalSetting(gateway_id=self.gateway.id, gateway_code=self.gateway.code,
                       integration_mode="google_primary", first_wave_unload_modifier=10, all_up_to_down_minutes=15))
        db.session.commit()
        def read():
            return native_uld_summary(self.gateway, op.sort_date, op.sort_name)
        with patch("app.services.neosektor_live_counts._neosektor_integration_mode",side_effect=AssertionError("integration routing")), \
             patch("requests.sessions.Session.request",side_effect=AssertionError("external HTTP")):
            self.assertIsNone(read())
            state = record_counts(self.gateway,op)
            first, second = read()["waves"]
            self.assertEqual((first["east"],first["west"],first["arrive"],first["unload"]),(6,0,15,26))
            self.assertEqual((second["east"],second["west"],second["arrive"],second["unload"]),(4,7,32,38))
            self.assertEqual((read()["east_open"],read()["west_open"],read()["unloaded"],read()["percent"]),(2,3,17,17))
            self.assertIsNone(native_uld_summary(self.gateway,op.sort_date,"day"))
            other=Gateway(code="SEA",name="Seattle")
            db.session.add(other)
            db.session.commit()
            self.assertIsNone(native_uld_summary(other,op.sort_date,op.sort_name))
            with self.app.test_request_context():
                self.assertEqual(snapshot(self.user,self.gateway,op)["uld"],read())
            sector = NeoNode.query.filter_by(code="sektor").one()
            membership = GatewayMembership.query.filter_by(user_id=self.user.id,gateway_id=self.gateway.id).one()
            GatewayNodeRole.query.filter_by(node_id=sector.id,gateway_membership_id=membership.id).update({"is_active":False})
            db.session.commit()
            with self.app.test_request_context():
                self.assertIsNone(snapshot(self.user,self.gateway,op)["uld"])
            waves = NeoSektorWaveState.query.filter_by(sort_state_id=state.id)
            counts = NeoSektorBallmatWaveCount.query.filter_by(sort_state_id=state.id)
            waves.update({"planned_count":0})
            counts.update({"count":0})
            db.session.commit()
            self.assertEqual([(w["arrive"],w["unload"]) for w in read()["waves"]],
                             [("ALL IN","ALL UP"),("ALL IN","PENDING")])
            self.assertIsNone(waves.first().all_up_started_at)
            waves.update({"all_up_started_at":datetime.utcnow()-timedelta(hours=2)})
            db.session.commit()
            self.assertEqual([w["unload"] for w in read()["waves"]],["DOWN","DOWN"])
            counts.filter_by(side="WEST",wave_name="2ND WAVE").delete()
            db.session.commit()
            incomplete = read()
            self.assertIsNone(incomplete["waves"][1]["west"])
            self.assertIsNone(incomplete["waves"][1]["unload"])
            self.assertIsNone(incomplete["unloaded"])
            self.assertEqual(incomplete["waves"][0]["unload"],"DOWN")

    def test_command_center_reads_under_explicit_grants_without_dml(self):
        from app.models import AccountAuthorizationState, AccountSecurityEvent, PortalAppAccess
        from tests.neoraiden_fixtures import record_counts
        op = seed_sort(self.gateway)
        record_counts(self.gateway, op)
        approval = PortalAppAccess.query.filter_by(user_id=self.user.id, app_code="neogateway").first()
        if not approval:
            approval = PortalAppAccess(user_id=self.user.id, app_code="neogateway")
            db.session.add(approval)
        approval.status, approval.is_active, approval.role = "approved", True, "watcher"
        receipt = AccountSecurityEvent(actor_user_id=self.user.id,subject_user_id=self.user.id,
                                       action="fixture",details={})
        db.session.add(receipt)
        db.session.flush()
        db.session.add(AccountAuthorizationState(id=1, active=True, security_event_id=receipt.id,
                                                inventory_fingerprint="fixture"))
        db.session.commit()
        statements = []
        def trace(_conn, _cur, statement, _params, _ctx, _many):
            statements.append(statement.split()[0].upper())
        engine = db.engine
        event.listen(engine, "before_cursor_execute", trace)
        try:
            with patch("requests.sessions.Session.request", side_effect=AssertionError("external fetch")):
                for path in ("/neoraiden", "/neoraiden/state"):
                    result = self.client.get(path + "?operation_id=" + str(op.id))
                    self.assertEqual(result.status_code,200)
            self.assertFalse({"INSERT", "UPDATE", "DELETE", "MERGE"} & set(statements))
        finally:
            event.remove(engine,"before_cursor_execute",trace)
        approval.is_active = False
        db.session.commit()
        self.assertEqual(self.client.get("/neoraiden").status_code,403)
        self.assertEqual(self.client.get("/neoraiden/state").status_code,403)

    def test_native_projection_identity_and_revalidation(self):
        op = seed_sort(self.gateway)
        self.user.role = "watcher"
        db.session.commit()
        path = "/neoraiden/state?gateway=RFD&operation_id=" + str(op.id)
        with patch("requests.sessions.Session.request", side_effect=AssertionError("external traffic")):
            result = self.client.get(path)
            self.assertEqual(result.status_code, 200)
            with self.app.test_request_context():
                rows = snapshot(self.user, self.gateway, op)["departures"]
            self.assertEqual([(r["tail"], r["route_label"], r["ordinal"], r["parking"]) for r in rows],
                             [("N202", "OAK", None, "B02"), ("N101", "OAK", None, None)])
            self.assertEqual([len(r["pulls"]) for r in rows], [0,1])
            self.assertEqual(rows[1]["pulls"][0]["pure"],"01:25")
            self.assertIsNone(rows[0]["deice"])
            self.assertEqual(self.client.get(path, headers={"If-None-Match":result.headers["ETag"]}).status_code, 304)
            self.assertIn("no-store",result.headers["Cache-Control"])
            node = NeoNode.query.filter_by(code="ermac").one()
            GatewayNodeRole.query.filter_by(node_id=node.id).update({"is_active":False})
            db.session.commit()
            changed = self.client.get(path, headers={"If-None-Match":result.headers["ETag"]})
            self.assertEqual(changed.status_code,200)
            self.assertNotIn("D13",changed.json["html"])
            self.assertEqual(self.client.post(path).status_code,405)
            GatewayMembership.query.filter_by(user_id=self.user.id).update({"is_active": False})
            db.session.commit()
            self.assertEqual(self.client.get(path,headers={"If-None-Match":changed.headers["ETag"]}).status_code,403)

    def test_gateway_and_sort_isolation_and_read_only_current(self):
        foreign = Gateway(code="DEN",name="Denver")
        db.session.add(foreign)
        db.session.commit()
        op = seed_sort(foreign)
        self.assertEqual(self.client.get("/neoraiden?gateway=DEN").status_code,403)
        access = GatewayMembership(user_id=self.user.id, gateway_id=foreign.id, status="approved", is_active=True)
        db.session.add(access)
        db.session.commit()
        result = self.client.get("/neoraiden?gateway=DEN&operation_id="+str(op.id))
        self.assertEqual(result.status_code, 200)
        self.assertIn(f'href="/neoraiden?gateway=DEN&amp;operation_id={op.id}" aria-label="NeoRaiden home"', result.text)
        self.assertEqual(self.client.get("/neoraiden/state?gateway=RFD&operation_id="+str(op.id)).status_code,404)
        self.assertEqual(self.client.get("/neoraiden?operation_id=garbage").status_code,404)
        response = self.client.get("/neoraiden")
        self.assertEqual(response.status_code,200)
        self.assertNotIn(b"FX101",response.data)
        self.assertIn(b"No active Sort",response.data)
        self.assertEqual(SortDateOperation.query.count(),1)

    def test_returning_browsers_fetch_current_shell_assets(self):
        for page, files in (("/rfd", ["css/gateway_launcher.css"]),
                            ("/neoraiden", ["css/operational_node_shell.css", "js/operational_shell.js"])):
            html = self.client.get(page).text
            for asset in files:
                with self.subTest(asset=asset):
                    content = (Path(self.app.static_folder) / asset).read_bytes().replace(b"\r\n", b"\n")
                    url = re.search(r'/static/' + re.escape(asset) + r'\?[^"]+', html).group(0)
                    self.assertIn("rev=" + sha256(content).hexdigest()[:12], url)

    def test_last_screen_on_staffing_and_standalone_routing(self):
        from app.models import NeoSektorOperationalSetting
        db.session.add(NeoSektorOperationalSetting(gateway_id=self.gateway.id, gateway_code=self.gateway.code,
                                                   integration_mode="neo_only"))
        db.session.commit()
        for path in ("/neostaffing/attendance", "/neosektor/driver-routing"):
            with self.subTest(path=path):
                page = self.client.get(path)
                self.assertEqual(page.status_code, 200)
                token = re.search(rb'data-visit="([^"]+)"', page.data).group(1).decode()
                self.assertEqual(self.client.post("/account/operational-screen", data={"visit":token}).status_code, 204)
                self.assertEqual(self.client.get("/login").location, path)
        self.assertNotIn(b'data-visit="', self.client.get("/neosektor/driver-routing?tv=1").data)

    def test_store_restore_and_fallback_on_revocation(self):
        op = seed_sort(self.gateway)
        screen = "/neoraiden?gateway=RFD&operation_id=" + str(op.id)
        page = self.client.get(screen)
        token = re.search(rb'data-visit="([^"]+)"',page.data).group(1).decode()
        endpoint = "/account/operational-screen"
        self.assertEqual(self.client.post(endpoint,data={"visit":"https://elsewhere.test/"}).status_code,403)
        self.assertEqual(self.client.post(endpoint,data={"visit":token}).status_code,204)
        self.assertEqual(self.client.get("/login").location, screen)
        self.client.post("/logout")
        response = self.client.post("/login",data={"username":"gateway-shell","password":"TestPassword123!"})
        self.assertEqual(response.location,screen)
        saved = db.session.get(AccountOperationalScreen,self.user.id)
        self.assertEqual(saved.operation_id,op.id)
        newer = re.search(rb'data-visit="([^"]+)"',self.client.get("/neoermac/door-view").data).group(1).decode()
        self.assertEqual(self.client.post(endpoint,data={"visit":newer}).status_code,204)
        self.assertEqual(self.client.post(endpoint,data={"visit":token}).status_code,204)
        self.assertEqual(self.client.get("/login").location,"/neoermac/door-view")
        node=NeoNode.query.filter_by(code="ermac").one()
        GatewayNodeRole.query.filter_by(node_id=node.id).update({"is_active":False})
        db.session.commit()
        self.assertNotEqual(self.client.get("/login").location,"/neoermac/door-view")
