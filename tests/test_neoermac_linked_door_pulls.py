import unittest
from datetime import date, datetime, time
from pathlib import Path

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

    def test_linked_pure_defaults_to_this_door_only(self):
        self._supervise("D1", "D4")

        response = self._save("D1", "pure", "01:45")

        self.assertEqual(response.status_code, 200)
        pulls = self._pulls_by_door()
        self.assertEqual(set(pulls), {"D1"})
        self.assertEqual(pulls["D1"].actual_pure_pull_time_local, time(1, 45))

    def test_explicit_both_sides_mix_writes_linked_supervised_doors(self):
        self._supervise("D1", "D4")

        response = self._save("D4", "mix", "02:12", apply_to_both=True)

        self.assertEqual(response.status_code, 200)
        pulls = self._pulls_by_door()
        self.assertEqual(pulls["D1"].actual_mix_pull_time_local, time(2, 12))
        self.assertEqual(pulls["D4"].actual_mix_pull_time_local, time(2, 12))

    def test_editing_respects_the_current_scope_without_retroactive_sync(self):
        self._supervise("D1", "D4")
        self._save("D1", "pure", "01:40", apply_to_both=True)

        self._save("D4", "pure", "01:52")

        pulls = self._pulls_by_door()
        self.assertEqual(pulls["D1"].actual_pure_pull_time_local, time(1, 40))
        self.assertEqual(pulls["D4"].actual_pure_pull_time_local, time(1, 52))

    def test_clearing_respects_the_current_scope(self):
        self._supervise("D1", "D4")
        self._save("D1", "pure", "01:45", apply_to_both=True)

        self._save("D1", "pure", "")

        pulls = self._pulls_by_door()
        self.assertIsNone(pulls["D1"].actual_pure_pull_time_local)
        self.assertEqual(pulls["D4"].actual_pure_pull_time_local, time(1, 45))

    def test_no_pure_and_no_mix_respect_explicit_scope(self):
        self._supervise("D1", "D4")
        self._save("D1", "pure", no_pull=True)
        self._save("D1", "mix", no_pull=True, apply_to_both=True)

        pulls = self._pulls_by_door()
        self.assertTrue(pulls["D1"].no_pure_pull)
        self.assertTrue(pulls["D1"].no_mix_pull)
        self.assertFalse(pulls["D4"].no_pure_pull)
        self.assertTrue(pulls["D4"].no_mix_pull)

    def test_explicit_both_sides_clear_clears_both_records(self):
        self._supervise("D1", "D4")
        self._save("D1", "pure", "01:45", apply_to_both=True)

        self._save("D1", "pure", "", apply_to_both=True)

        for pull in self._pulls_by_door().values():
            self.assertIsNone(pull.actual_pure_pull_time_local)
            self.assertFalse(pull.no_pure_pull)

    def test_unselected_counterpart_remains_independent(self):
        self._supervise("D1")

        self._save("D1", "pure", "01:45", apply_to_both=True)

        pulls = self._pulls_by_door()
        self.assertEqual(set(pulls), {"D1"})
        self.assertEqual(pulls["D1"].actual_pure_pull_time_local, time(1, 45))

    def test_same_destination_at_an_unrelated_belt_is_not_changed(self):
        self._assign("runout_4", "east_destination_1", "SDF")
        self._assign("runout_4", "west_destination_1", "SDF")
        db.session.commit()
        self._supervise("D1", "D4", "D13", "D17")

        self._save("D1", "pure", "01:45", apply_to_both=True)

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

    def test_malformed_scope_safely_defaults_to_this_door(self):
        self._supervise("D1", "D4")

        response = self._save("D1", "pure", "01:45", apply_to_both="yes")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(self._pulls_by_door()), {"D1"})

    def test_normal_bulk_save_respects_explicit_both_sides_scope(self):
        self._supervise("D1", "D4")

        response = self.client.post(
            "/neoermac/door-view",
            data={
                "door": "D1",
                "action": "save_pulls",
                "destination_count": "1",
                "destination_0": "SDF",
                "actual_pure_0": "01:45",
                "actual_mix_0": "",
                "apply_to_both": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        pulls = self._pulls_by_door()
        self.assertEqual(set(pulls), {"D1", "D4"})
        self.assertEqual(pulls["D1"].actual_pure_pull_time_local, time(1, 45))
        self.assertEqual(pulls["D4"].actual_pure_pull_time_local, time(1, 45))

    def test_scope_control_only_renders_for_a_linked_supervised_door(self):
        self._supervise("D1", "D4")

        linked_page = self.client.get("/neoermac/door-view?door=D1")

        self.assertIn(b"data-pull-scope aria", linked_page.data)
        self.assertIn(b"THIS DOOR", linked_page.data)
        self.assertIn(b"BOTH SIDES", linked_page.data)
        self.assertIn(b'data-operation-id="%d"' % self.operation.id, linked_page.data)
        self.assertIn(b'data-pull-scope-option="0" aria-pressed="true"', linked_page.data)

        self._supervise("D1", "D13")
        unrelated_page = self.client.get("/neoermac/door-view?door=D1")
        self.assertNotIn(b"data-pull-scope aria", unrelated_page.data)

    def test_scope_client_contract_is_operation_scoped_and_safe(self):
        template = Path("app/templates/neonodes/neoermac/door_view.html").read_text()

        self.assertIn(
            'class="neo-segmented-control neoermac-pull-scope-options"',
            template,
        )
        self.assertIn("neoermac.pull-scope.${userId}.${operationId}", template)
        self.assertIn('body.set("apply_to_both"', template)
        self.assertIn("window.localStorage.getItem(scopeStorageKey)", template)
        self.assertIn("Browser storage is optional; THIS DOOR remains the safe fallback.", template)

    def test_scope_control_uses_visible_ermac_selected_and_unselected_states(self):
        css = Path("app/static/css/base.css").read_text()
        scope_css = css.split(".neoermac-pull-scope {", 1)[1].split(
            ".neoermac-door-selector > label", 1
        )[0]

        self.assertIn(".neo-segmented-control", scope_css)
        self.assertIn(
            "--segmented-control-active-background: var(--node-ermac-primary);",
            scope_css,
        )
        self.assertIn(
            "--segmented-control-color: var(--node-ermac-highlight);",
            scope_css,
        )
        self.assertIn('button[aria-pressed="true"]', scope_css)
        self.assertIn("--segmented-control-hover-color: #fff;", scope_css)
        self.assertIn(
            "--segmented-control-focus: var(--node-ermac-highlight);",
            scope_css,
        )
        self.assertIn(":is(a, button):focus-visible", scope_css)
        self.assertNotIn("--node-ermac-accent", scope_css)
        self.assertNotIn("--node-ermac-panel", scope_css)

    def test_inactive_tab_is_green_during_pull_now_period(self):
        self._supervise("D1", "D4")
        self._set_planned_pulls(time(23, 0), time(23, 30))

        payload = self._state("D1")
        page = self.client.get("/neoermac/door-view?door=D1")

        self.assertEqual(payload["door_tab_alerts"]["D1"]["state"], "")
        self.assertEqual(payload["door_tab_alerts"]["D4"]["state"], "due_now")
        self.assertGreaterEqual(len(payload["door_tab_alerts"]["D4"]["pulls"]), 1)
        self.assertTrue(
            all(
                pull["due_now_epoch_ms"] is not None
                for pull in payload["door_tab_alerts"]["D4"]["pulls"]
            )
        )
        self.assertIn(
            b'class="neoermac-door-tab is-pull-due-now"',
            page.data,
        )
        self.assertIn(b'data-door-tab-alert-state="due_now"', page.data)
        self.assertNotIn(
            b'class="neoermac-door-tab is-active is-pull-due-now"',
            page.data,
        )

    def test_inactive_tab_is_red_and_red_overrides_green(self):
        self._supervise("D1", "D4")
        self._set_planned_pulls(time(23, 0), time(22, 50))

        payload = self._state("D1")
        page = self.client.get("/neoermac/door-view?door=D1")

        self.assertEqual(payload["door_tab_alerts"]["D4"]["state"], "late")
        self.assertIn(b'class="neoermac-door-tab is-pull-late"', page.data)
        self.assertNotIn(
            b'class="neoermac-door-tab is-pull-due-now is-pull-late"',
            page.data,
        )

    def test_due_soon_pull_does_not_animate_an_inactive_tab(self):
        self._supervise("D1", "D4")
        self._set_planned_pulls(time(23, 4), time(23, 30))

        payload = self._state("D1")

        self.assertEqual(payload["door_tab_alerts"]["D4"]["state"], "")
        self.assertEqual(
            payload["destinations"][0]["pull_alerts"]["pure"]["state"],
            "due_soon",
        )

    def test_unsupervised_doors_are_absent_from_tab_alert_state(self):
        self._supervise("D1")
        self._set_planned_pulls(time(22, 50), time(23, 30))

        payload = self._state("D1")

        self.assertEqual(set(payload["door_tab_alerts"]), {"D1"})

    def test_actual_or_no_pull_clears_inactive_tab_alert(self):
        self._supervise("D1", "D4")
        self._set_planned_pulls(time(22, 50), time(23, 30))
        pull = NeoErmacDoorPull(
            gateway_id=self.gateway.id,
            sort_date_operation_id=self.operation.id,
            door="D4",
            destination="SDF",
            actual_pure_pull_time_local=time(22, 55),
        )
        db.session.add(pull)
        db.session.commit()

        self.assertEqual(self._state("D1")["door_tab_alerts"]["D4"]["state"], "")

        pull.actual_pure_pull_time_local = None
        pull.no_pure_pull = True
        db.session.commit()

        self.assertEqual(self._state("D1")["door_tab_alerts"]["D4"]["state"], "")

    def test_live_state_recomputes_inactive_tab_without_page_reload(self):
        self._supervise("D1", "D4")
        self._set_planned_pulls(time(23, 30), time(23, 40))
        self.assertEqual(self._state("D1")["door_tab_alerts"]["D4"]["state"], "")

        self._set_planned_pulls(time(23, 0), time(23, 40))
        payload = self._state("D1")
        page = self.client.get("/neoermac/door-view?door=D1")

        self.assertEqual(payload["door_tab_alerts"]["D4"]["state"], "due_now")
        self.assertIn(b"applyDoorTabAlerts(state.door_tab_alerts", page.data)
        self.assertIn(b"window.neoErmacApplyDoorTabAlerts", page.data)

    def test_linked_pull_save_immediately_clears_counterpart_tab_alert(self):
        self._supervise("D1", "D4")
        self._set_planned_pulls(time(22, 50), time(23, 30))
        self.assertEqual(
            self._state("D1")["door_tab_alerts"]["D4"]["state"],
            "late",
        )

        response = self._save("D1", "pure", "22:58", apply_to_both=True)
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["state"]["door_tab_alerts"]["D4"]["state"], "")
        self.assertEqual(
            self._pulls_by_door()["D4"].actual_pure_pull_time_local,
            time(22, 58),
        )

    def test_tab_alert_css_blinks_green_and_red_only_on_inactive_tabs(self):
        css = Path("app/static/css/base.css").read_text()

        self.assertIn(
            ".neoermac-door-tab:not(.is-active).is-pull-due-now",
            css,
        )
        self.assertIn(
            ".neoermac-door-tab:not(.is-active).is-pull-late",
            css,
        )
        self.assertIn("@keyframes neoermac-door-tab-pull-pulse", css)
        self.assertNotIn(
            ".neoermac-door-tab:not(.is-active).is-pull-due-soon",
            css,
        )

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

    def _save(self, door, pull_key, value="", no_pull=False, apply_to_both=None):
        data = {
            "door": door,
            "destination": "SDF",
            "pull_key": pull_key,
            "actual_pull": value,
            "no_pull": "1" if no_pull else "0",
        }
        if apply_to_both is not None:
            data["apply_to_both"] = "1" if apply_to_both is True else str(apply_to_both)
        return self.client.post(
            "/neoermac/door-view/pull-autosave",
            data=data,
        )

    def _state(self, door):
        response = self.client.get(f"/neoermac/door-view/state?door={door}")
        self.assertEqual(response.status_code, 200)
        return response.get_json()["state"]

    def _set_planned_pulls(self, pure, mix):
        mission = db.session.get(SortDateMission, self.mission.id)
        mission.pure_pull_time_local = pure
        mission.mix_pull_time_local = mix
        db.session.commit()

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
