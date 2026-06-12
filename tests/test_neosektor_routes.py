import unittest

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    NeoSektorBallmatCount,
    NeoSektorBallmatWaveCount,
    NeoSektorBayStatus,
    NeoSektorDriverRouteSetting,
    NeoSektorOpenBayState,
    NeoSektorSortState,
    NeoSektorWaveState,
    PermissionRule,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules


class NeoSektorRoutesTest(unittest.TestCase):
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
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_neosektor_dashboard_requires_login(self):
        response = self.client.get("/neosektor", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

    def test_operator_can_open_neosektor_dashboard(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoSektor", response.data)
        for label in (
            b"TUNNEL CONDUCTOR",
            b"EBM",
            b"WBM",
            b"DISCHARGE",
            b"VIEW LIVE COUNTS",
            b"DRIVER ROUTING",
        ):
            self.assertIn(label, response.data)

    def test_placeholder_routes_load_for_view_authorized_user(self):
        self._login_approved_user(role="operator")

        paths = {
            "/neosektor/discharge": b"DISCHARGE",
        }

        for path, title in paths.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(title, response.data)
                self.assertIn(b"SCREEN LOGIC WILL BE COPIED", response.data)

    def test_driver_routing_loads_for_view_authorized_user(self):
        self._login_approved_user(role="watcher")

        response = self.client.get("/neosektor/driver-routing")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DRIVER ROUTING", response.data)
        self.assertIn(b"DRIVER COUNTS", response.data)
        self.assertIn(b"data-driver-routing", response.data)
        self.assertIn(b"data-can-edit=\"false\"", response.data)
        self.assertIn(b"VIEW ONLY", response.data)
        self.assertNotIn(b"SCREEN LOGIC WILL BE COPIED", response.data)

    def test_driver_routing_blocks_user_without_view_permission(self):
        view_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.driver_routing.view"
        ).one()
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.driver_routing.edit"
        ).one()
        view_rule.minimum_role = "simulator"
        edit_rule.minimum_role = "simulator"
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor/driver-routing", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/neosektor")

    def test_view_only_driver_routing_user_cannot_update_settings(self):
        self._login_approved_user(role="watcher")

        response = self.client.post(
            "/neosektor/driver-routing/update",
            json={"west_offset": 4},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(NeoSektorDriverRouteSetting.query.count(), 0)

    def test_edit_authorized_user_can_update_driver_route_offset(self):
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/driver-routing/update",
            json={"west_offset": 4},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"]["routing"]["west_offset"], 4)
        self.assertEqual(
            NeoSektorDriverRouteSetting.query.filter_by(
                route_name="WEST OFFSET",
            ).one().route_value,
            "4",
        )

    def test_driver_routing_reflects_shared_neosektor_state(self):
        self._login_approved_user(role="operator")
        self.client.get("/neosektor/ebm")
        self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "east",
                "waves": {
                    "first": {"count": 7, "status": "Light"},
                    "second": {"count": 2, "status": "Light"},
                },
                "open_bays": 3,
                "bay_statuses": {"EAST 1": "Moderate"},
            },
        )
        self.client.post(
            "/neosektor/driver-routing/update",
            json={"west_offset": 3},
        )

        page = self.client.get("/neosektor/driver-routing")
        state_response = self.client.get("/neosektor/driver-routing/state")

        payload = state_response.get_json()
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"West Ballmat Stay Left", page.data)
        self.assertIn(b"East Ballmat Stay Right", page.data)
        self.assertEqual(payload["state"]["routing"]["west_offset"], 3)
        self.assertEqual(
            payload["state"]["routing"]["routes"]["first"]["target"],
            "West Ballmat Stay Left",
        )
        self.assertEqual(
            payload["state"]["routing"]["routes"]["second"]["target"],
            "East Ballmat Stay Right",
        )
        self.assertEqual(payload["state"]["sides"]["east"]["open_bays"], 3)

    def test_neosektor_dashboard_and_header_link_to_real_driver_routing(self):
        self._login_approved_user(role="operator")

        dashboard = self.client.get("/neosektor")
        driver_routing = self.client.get("/neosektor/driver-routing")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b'href="/neosektor/driver-routing"', dashboard.data)
        self.assertEqual(driver_routing.status_code, 200)
        self.assertIn(b'href="/neosektor/driver-routing"', driver_routing.data)
        self.assertIn(b'aria-current="page"', driver_routing.data)

    def test_tunnel_conductor_loads_for_view_authorized_user(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor/tunnel-conductor")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"TUNNEL CONDUCTOR", response.data)
        self.assertIn(b"BALLMAT COUNT CONTROL", response.data)
        self.assertIn(b"data-tunnel-conductor", response.data)
        self.assertIn(b"data-can-edit=\"true\"", response.data)
        self.assertNotIn(b"SCREEN LOGIC WILL BE COPIED", response.data)

    def test_tunnel_conductor_blocks_user_without_view_permission(self):
        view_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.tunnel_conductor.view"
        ).one()
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.tunnel_conductor.edit"
        ).one()
        view_rule.minimum_role = "simulator"
        edit_rule.minimum_role = "simulator"
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor/tunnel-conductor", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/neosektor")

    def test_view_only_tunnel_conductor_user_cannot_update_counts(self):
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.tunnel_conductor.edit"
        ).one()
        edit_rule.minimum_role = "simulator"
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "east", "wave": "first", "delta": 1},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)

    def test_tunnel_conductor_delta_updates_east_first_wave(self):
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "east", "wave": "first", "delta": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="EAST",
                wave_name="1ST WAVE",
            ).one().count,
            1,
        )

    def test_tunnel_conductor_delta_updates_east_second_wave(self):
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "east", "wave": "second", "delta": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="EAST",
                wave_name="2ND WAVE",
            ).one().count,
            1,
        )

    def test_tunnel_conductor_delta_updates_west_first_wave(self):
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "west", "wave": "first", "delta": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="WEST",
                wave_name="1ST WAVE",
            ).one().count,
            1,
        )

    def test_tunnel_conductor_delta_updates_west_second_wave(self):
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "west", "wave": "second", "delta": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="WEST",
                wave_name="2ND WAVE",
            ).one().count,
            1,
        )

    def test_tunnel_conductor_delta_counts_cannot_go_below_zero(self):
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "east", "wave": "first", "delta": -1},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["state"]["sides"]["east"]["waves"][0]["count"], 0)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="EAST",
                wave_name="1ST WAVE",
            ).one().count,
            0,
        )

    def test_tunnel_conductor_delta_updates_shared_live_state(self):
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "west", "wave": "second", "delta": 4},
        )

        self.assertEqual(response.status_code, 200)
        state_response = self.client.get("/neosektor/ballmat/state")
        live_counts = self.client.get("/neosektor/live-counts")

        payload = state_response.get_json()
        self.assertEqual(payload["state"]["sides"]["west"]["total_count"], 4)
        self.assertEqual(payload["state"]["waves"][1]["unloaded"], 4)
        self.assertEqual(NeoSektorSortState.query.one().unloaded_total, 4)
        self.assertEqual(live_counts.status_code, 200)
        self.assertIn(b"<span>UNLOADED</span><strong>4</strong>", live_counts.data)

    def test_neosektor_dashboard_and_header_link_to_real_tunnel_conductor(self):
        self._login_approved_user(role="operator")

        dashboard = self.client.get("/neosektor")
        tunnel = self.client.get("/neosektor/tunnel-conductor")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b'href="/neosektor/tunnel-conductor"', dashboard.data)
        self.assertEqual(tunnel.status_code, 200)
        self.assertIn(b'href="/neosektor/tunnel-conductor"', tunnel.data)
        self.assertIn(b'aria-current="page"', tunnel.data)

    def test_ebm_and_wbm_open_shared_ballmat_operations_screen(self):
        self._login_approved_user(role="operator")

        ebm = self.client.get("/neosektor/ebm")
        wbm = self.client.get("/neosektor/wbm")
        east_compat = self.client.get("/neosektor/ballmat?side=east", follow_redirects=False)
        west_compat = self.client.get("/neosektor/ballmat?side=west", follow_redirects=False)

        self.assertEqual(ebm.status_code, 200)
        self.assertIn(b"BALLMAT OPERATIONS", ebm.data)
        self.assertIn(b"EAST BALLMAT SELECTED", ebm.data)
        self.assertIn(b"data-selected-side=\"east\"", ebm.data)
        self.assertIn(b"EAST BALLMAT", ebm.data)
        self.assertIn(b"WEST BALLMAT", ebm.data)
        self.assertIn(b"data-can-edit=\"true\"", ebm.data)
        self.assertIn(b'href="/neosektor/ebm"', ebm.data)
        self.assertIn(b'href="/neosektor/wbm"', ebm.data)
        self.assertEqual(wbm.status_code, 200)
        self.assertIn(b"WEST BALLMAT SELECTED", wbm.data)
        self.assertIn(b"data-selected-side=\"west\"", wbm.data)
        self.assertEqual(east_compat.status_code, 302)
        self.assertEqual(east_compat.location, "/neosektor/ebm")
        self.assertEqual(west_compat.status_code, 302)
        self.assertEqual(west_compat.location, "/neosektor/wbm")

    def test_view_only_ballmat_user_cannot_update_counts(self):
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.ebm.edit"
        ).one()
        edit_rule.minimum_role = "simulator"
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "east",
                "waves": {"first": {"count": 12, "status": "Light"}},
                "open_bays": 2,
                "bay_statuses": {},
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)

    def test_ebm_view_permission_controls_screen_access(self):
        view_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.ebm.view"
        ).one()
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.ebm.edit"
        ).one()
        view_rule.minimum_role = "simulator"
        edit_rule.minimum_role = "simulator"
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor/ebm", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/neosektor")

    def test_wbm_view_only_user_sees_disabled_controls_and_cannot_update(self):
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.wbm.edit"
        ).one()
        edit_rule.minimum_role = "simulator"
        db.session.commit()
        self._login_approved_user(role="operator")

        page = self.client.get("/neosektor/wbm")
        update = self.client.post(
            "/neosektor/ballmat/update?side=west",
            json={
                "side": "west",
                "waves": {"first": {"count": 5, "status": "Light"}},
                "open_bays": 1,
                "bay_statuses": {},
            },
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"data-can-edit=\"false\"", page.data)
        self.assertIn(b"VIEW ONLY", page.data)
        self.assertEqual(update.status_code, 403)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 4)
        self.assertEqual(
            sum(row.count for row in NeoSektorBallmatWaveCount.query.all()),
            0,
        )

    def test_edit_authorized_user_updates_selected_side_only(self):
        self._login_approved_user(role="operator")
        self.client.get("/neosektor/ebm")

        response = self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "east",
                "waves": {
                    "first": {"count": 12, "status": "Light"},
                    "second": {"count": 4, "status": "Moderate"},
                },
                "open_bays": 3,
                "bay_statuses": {"EAST 1": "Full", "EAST 2": "Light"},
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        east_state = payload["state"]["sides"]["east"]
        west_state = payload["state"]["sides"]["west"]
        self.assertEqual(east_state["total_count"], 16)
        self.assertEqual(east_state["open_bays"], 3)
        self.assertEqual(west_state["total_count"], 0)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="EAST",
                wave_name="1ST WAVE",
            ).one().count,
            12,
        )
        self.assertEqual(
            NeoSektorOpenBayState.query.filter_by(side="EAST").one().open_count,
            3,
        )
        self.assertEqual(
            NeoSektorBayStatus.query.filter_by(bay_name="EAST 1").one().status,
            "Full",
        )
        self.assertEqual(
            NeoSektorWaveState.query.filter_by(wave_name="1ST WAVE").one().unloaded_count,
            12,
        )
        self.assertEqual(NeoSektorSortState.query.one().unloaded_total, 16)

    def test_edit_authorized_user_cannot_update_unselected_side(self):
        self._login_approved_user(role="operator")
        self.client.get("/neosektor/ebm")

        response = self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "west",
                "waves": {"first": {"count": 99, "status": "Full"}},
                "open_bays": 1,
                "bay_statuses": {},
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            sum(row.count for row in NeoSektorBallmatWaveCount.query.all()),
            0,
        )

    def test_ballmat_update_counts_clamp_at_zero(self):
        self._login_approved_user(role="operator")
        self.client.get("/neosektor/ebm")

        response = self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "east",
                "waves": {
                    "first": {"count": -4, "status": "Light"},
                    "second": {"count": 0, "status": "Empty"},
                },
                "open_bays": -1,
                "bay_statuses": {},
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        east_state = payload["state"]["sides"]["east"]
        self.assertEqual(east_state["waves"][0]["count"], 0)
        self.assertEqual(east_state["open_bays"], 0)

    def test_live_json_endpoint_returns_updated_ballmat_state(self):
        self._login_approved_user(role="operator")
        self.client.post(
            "/neosektor/ballmat/update?side=west",
            json={
                "side": "west",
                "waves": {
                    "first": {"count": 7, "status": "Light"},
                    "second": {"count": 8, "status": "Moderate"},
                },
                "open_bays": 2,
                "bay_statuses": {"WEST 1": "Full"},
            },
        )

        response = self.client.get("/neosektor/ballmat/state")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"]["sides"]["west"]["total_count"], 15)
        self.assertEqual(payload["state"]["sides"]["west"]["open_bays"], 2)
        self.assertEqual(payload["state"]["waves"][0]["unloaded"], 7)

    def test_live_counts_loads_default_database_backed_state(self):
        self._login_approved_user(role="watcher")

        response = self.client.get("/neosektor/live-counts")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"VIEW LIVE COUNTS", response.data)
        self.assertIn(b"LIVE COUNTS", response.data)
        self.assertIn(b"LEFT", response.data)
        self.assertIn(b"1ST WAVE", response.data)
        self.assertIn(b"2ND WAVE", response.data)
        self.assertIn(b"EAST BALLMAT", response.data)
        self.assertIn(b"WEST BALLMAT", response.data)
        self.assertIn(b"Empty", response.data)
        self.assertNotIn(b"SCREEN LOGIC WILL BE COPIED", response.data)
        self.assertEqual(NeoSektorSortState.query.count(), 1)
        self.assertEqual(NeoSektorWaveState.query.count(), 2)
        self.assertEqual(NeoSektorBallmatCount.query.count(), 2)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 4)
        self.assertEqual(NeoSektorOpenBayState.query.count(), 2)
        self.assertEqual(NeoSektorBayStatus.query.count(), 6)
        self.assertEqual(NeoSektorDriverRouteSetting.query.count(), 3)

    def test_neosektor_dashboard_and_header_link_to_real_live_counts(self):
        self._login_approved_user(role="operator")

        dashboard = self.client.get("/neosektor")
        live_counts = self.client.get("/neosektor/live-counts")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b'href="/neosektor/live-counts"', dashboard.data)
        self.assertEqual(live_counts.status_code, 200)
        self.assertIn(b'href="/neosektor/live-counts"', live_counts.data)
        self.assertIn(b'aria-current="page"', live_counts.data)

    def test_watcher_can_open_dashboard_and_live_counts_but_not_operator_pages(self):
        self._login_approved_user(role="watcher")

        dashboard = self.client.get("/neosektor", follow_redirects=False)
        ebm = self.client.get("/neosektor/ebm", follow_redirects=False)
        wbm = self.client.get("/neosektor/wbm", follow_redirects=False)
        live_counts = self.client.get("/neosektor/live-counts", follow_redirects=False)

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"NeoSektor", dashboard.data)
        self.assertEqual(ebm.status_code, 302)
        self.assertEqual(ebm.location, "/neosektor")
        self.assertEqual(wbm.status_code, 302)
        self.assertEqual(wbm.location, "/neosektor")
        self.assertEqual(live_counts.status_code, 200)
        self.assertIn(b"VIEW LIVE COUNTS", live_counts.data)

    def test_rfd_sektor_still_points_to_standalone_service(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/rfd/sektor", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "https://neosektor.onrender.com/")

    def test_rfd_hub_neosektor_tile_points_to_internal_dashboard(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/rfd")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoSektor", response.data)
        self.assertIn(b'href="/neosektor"', response.data)
        self.assertNotIn(b'href="/rfd/sektor"', response.data)

    def _login_approved_user(self, role):
        user = User(
            username=f"sektor_{role}_user",
            email=f"sektor_{role}@example.test",
            role="watcher",
            is_active=True,
        )
        user.set_password("TestPassword123!")
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

        sektor = NeoNode.query.filter_by(code="sektor").one()
        if role != "watcher":
            db.session.add(
                GatewayNodeRole(
                    gateway_membership_id=membership.id,
                    node_id=sektor.id,
                    role=role,
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
