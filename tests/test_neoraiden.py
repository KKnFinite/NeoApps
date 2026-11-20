import re
import unittest
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

    def test_command_center_reads_under_explicit_grants_without_dml(self):
        from app.models import AccountAuthorizationState, AccountSecurityEvent, PortalAppAccess
        op = seed_sort(self.gateway)
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
            self.assertEqual([(r["tail"],r["ordinal"],r["parking"]) for r in rows],
                             [("N202",1,"B02"),("N101",2,None)])
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
