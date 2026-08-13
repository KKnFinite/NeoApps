import os
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    NeoSektorBallmatWaveCount,
    NeoSektorBayStatus,
    NeoSektorOpenBayState,
    NeoSektorOperationalSetting,
    User,
)
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
)
from app.services.neosektor_live_counts import apply_standalone_compat_values
from app.services.neosektor_sheets_compat import (
    GOOGLE_PRIMARY,
    NEO_ONLY,
    NEO_PRIMARY_GOOGLE_MIRROR,
    SHEET_CELL_ORDER,
    clear_neosektor_google_cache,
    neosektor_integration_mode,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


FAKE_SHEETS_ENV = {
    "GOOGLE_SHEETS_ID": "test-sheet-id",
    "GOOGLE_SHEETS_TAB": "Live Counts",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "{}",
}


def _complete_sheet_values(**overrides):
    values = {
        "B2": 7,
        "C2": 4,
        "D2": 18,
        "B3": 5,
        "C3": 3,
        "D3": 14,
        "B4": 2,
        "C4": 1,
        "B6": "Full",
        "B8": "Moderate",
        "B10": "Light",
        "C6": "Overflowing",
        "C8": "Empty",
    }
    values.update(overrides)
    return values


class _FakeWorksheet:
    def __init__(self, values=None, read_error=None, write_error=None):
        self.values = values or _complete_sheet_values()
        self.read_error = read_error
        self.write_error = write_error
        self.batch_reads = []
        self.updates = []

    def batch_get(self, ranges):
        self.batch_reads.append(tuple(ranges))
        if self.read_error:
            raise self.read_error
        return [
            [[self.values[cell]]] if cell in self.values else []
            for cell in ranges
        ]

    def update_acell(self, cell, value):
        if self.write_error:
            raise self.write_error
        self.updates.append((cell, value))
        self.values[cell] = value


class NeoSektorIntegrationModesTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoSektorIntegrationModesConfig",
            (),
            {
                "SECRET_KEY": "neosektor-integration-modes",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        clear_neosektor_google_cache()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_default_mode_is_google_primary_and_contract_excludes_neo_settings(self):
        self.assertEqual(neosektor_integration_mode(self.gateway), GOOGLE_PRIMARY)
        self.assertEqual(
            SHEET_CELL_ORDER,
            (
                "B2", "C2", "D2", "B3", "C3", "D3", "B4", "C4",
                "B6", "B8", "B10", "C6", "C8",
            ),
        )
        self.assertNotIn("B13", SHEET_CELL_ORDER)
        self.assertNotIn("B14", SHEET_CELL_ORDER)
        self.assertNotIn("B15", SHEET_CELL_ORDER)

    def test_system_settings_mode_is_grandmaster_writable_only(self):
        self._login("operator")
        page = self.client.get("/motherbrain/system-settings")
        denied = self.client.post(
            "/motherbrain/system-settings",
            data={"action": "set_neosektor_mode", "integration_mode": NEO_ONLY},
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"SYSTEM SETTINGS", page.data.upper())
        self.assertIn(b"VIEW ONLY", page.data)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(neosektor_integration_mode(self.gateway), GOOGLE_PRIMARY)

        self._login("grandmaster")
        worksheet = _FakeWorksheet()
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            changed = self.client.post(
                "/motherbrain/system-settings",
                data={"action": "set_neosektor_mode", "integration_mode": NEO_ONLY},
                follow_redirects=True,
            )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(neosektor_integration_mode(self.gateway), NEO_ONLY)

    def test_google_primary_reads_directly_without_duplicate_operational_rows(self):
        self._login("simulator")
        worksheet = _FakeWorksheet()
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            response = self.client.get("/neosektor/live-counts/state")

        self.assertEqual(response.status_code, 200)
        state = response.get_json()["state"]
        self.assertEqual(state["sides"]["east"]["waves"][0]["count"], 7)
        self.assertEqual(state["waves"][0]["planned"], 18)
        self.assertEqual(state["sides"]["east"]["bays"][0]["status"], "Full")
        self.assertEqual(worksheet.batch_reads, [SHEET_CELL_ORDER])
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)
        self.assertEqual(NeoSektorOpenBayState.query.count(), 0)
        self.assertEqual(NeoSektorBayStatus.query.count(), 0)

    def test_google_primary_uses_transient_cache_without_database_throttle_write(self):
        self._login("simulator")
        worksheet = _FakeWorksheet()
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            first = self.client.get("/neosektor/live-counts/state")
            second = self.client.get("/neosektor/driver-routing/state")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(worksheet.batch_reads, [SHEET_CELL_ORDER])
        settings = NeoSektorOperationalSetting.query.one()
        self.assertIsNone(settings.last_google_read_at_utc)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)
        self.assertEqual(NeoSektorOpenBayState.query.count(), 0)
        self.assertEqual(NeoSektorBayStatus.query.count(), 0)

    def test_google_primary_writes_google_and_keeps_neo_settings_in_neon(self):
        self._login("simulator")
        worksheet = _FakeWorksheet()
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            edit = self.client.post(
                "/neosektor/ballmat/update?side=east",
                json={
                    "side": "east",
                    "waves": {"first": {"count": 9}},
                    "open_bays": 3,
                    "bay_statuses": {"Bay 1": "Moderate"},
                },
            )
            settings = self.client.post(
                "/neosektor/tunnel-conductor/settings",
                json={
                    "first_modifier": 52,
                    "second_modifier": 31,
                    "down_timer_minutes": 22,
                },
            )
            offset = self.client.post(
                "/neosektor/tunnel-conductor/offset",
                json={"west_offset": 4},
            )

        self.assertEqual(edit.status_code, 200)
        self.assertEqual(settings.status_code, 200)
        self.assertEqual(offset.status_code, 200)
        self.assertEqual(
            worksheet.updates,
            [("B2", 9), ("B4", 3), ("B6", "Moderate")],
        )
        self.assertEqual(worksheet.batch_reads, [SHEET_CELL_ORDER])
        self.assertEqual(
            edit.get_json()["state"]["sides"]["east"]["waves"][0]["count"],
            9,
        )
        self.assertEqual(offset.get_json()["state"]["routing"]["west_offset"], 4)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)
        saved = NeoSektorOperationalSetting.query.one()
        self.assertEqual(saved.first_wave_unload_modifier, 52)
        self.assertEqual(saved.second_wave_unload_modifier, 31)
        self.assertEqual(saved.all_up_to_down_minutes, 22)

    def test_google_primary_write_failure_is_visible_and_does_not_persist_counts(self):
        self._login("simulator")
        worksheet = _FakeWorksheet(write_error=RuntimeError("unavailable"))
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            response = self.client.post(
                "/neosektor/ballmat/update?side=east",
                json={
                    "side": "east",
                    "waves": {"first": {"count": 8}},
                    "bay_statuses": {},
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn(b"could not save", response.data)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)

    def test_google_primary_to_neo_primary_handoff_imports_before_switch(self):
        self._login("grandmaster")
        worksheet = _FakeWorksheet()
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            response = self.client.post(
                "/motherbrain/system-settings",
                data={
                    "action": "set_neosektor_mode",
                    "integration_mode": NEO_PRIMARY_GOOGLE_MIRROR,
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(neosektor_integration_mode(self.gateway), NEO_PRIMARY_GOOGLE_MIRROR)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="EAST", wave_name="1ST WAVE"
            ).one().count,
            7,
        )
        self.assertEqual(worksheet.batch_reads, [SHEET_CELL_ORDER])

    def test_neo_only_to_mirror_mode_keeps_neo_authoritative_on_mirror_failure(self):
        self._set_mode(NEO_ONLY)
        apply_standalone_compat_values(self.gateway, _complete_sheet_values())
        db.session.commit()
        self._login("grandmaster")
        worksheet = _FakeWorksheet(write_error=RuntimeError("unavailable"))
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            response = self.client.post(
                "/motherbrain/system-settings",
                data={
                    "action": "set_neosektor_mode",
                    "integration_mode": NEO_PRIMARY_GOOGLE_MIRROR,
                },
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(neosektor_integration_mode(self.gateway), NEO_PRIMARY_GOOGLE_MIRROR)
        settings = NeoSektorOperationalSetting.query.one()
        self.assertTrue(settings.google_mirror_sync_needed)
        self.assertIn(b"GOOGLE MIRROR NEEDS ATTENTION", response.data)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="EAST", wave_name="1ST WAVE"
            ).one().count,
            7,
        )

    def test_failed_google_handoff_leaves_google_primary_active(self):
        self._login("grandmaster")
        worksheet = _FakeWorksheet(values=_complete_sheet_values(B2="invalid"))
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            response = self.client.post(
                "/motherbrain/system-settings",
                data={
                    "action": "set_neosektor_mode",
                    "integration_mode": NEO_PRIMARY_GOOGLE_MIRROR,
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(neosektor_integration_mode(self.gateway), GOOGLE_PRIMARY)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)

    def test_neo_primary_reads_neon_and_mirrors_edits(self):
        self._set_mode(NEO_PRIMARY_GOOGLE_MIRROR)
        apply_standalone_compat_values(self.gateway, _complete_sheet_values())
        db.session.commit()
        self._login("simulator")
        worksheet = _FakeWorksheet()
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            state = self.client.get("/neosektor/live-counts/state")
            edit = self.client.post(
                "/neosektor/ballmat/update?side=east",
                json={
                    "side": "east",
                    "waves": {"first": {"count": 11}},
                    "open_bays": 2,
                    "bay_statuses": {"Bay 1": "Full"},
                },
            )

        self.assertEqual(state.status_code, 200)
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(worksheet.batch_reads, [])
        self.assertIn(("B2", 11), worksheet.updates)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="EAST", wave_name="1ST WAVE"
            ).one().count,
            11,
        )

    def test_mirror_failure_preserves_neo_success_and_retry_clears_warning(self):
        self._set_mode(NEO_PRIMARY_GOOGLE_MIRROR)
        apply_standalone_compat_values(self.gateway, _complete_sheet_values())
        db.session.commit()
        self._login("simulator")
        failing = _FakeWorksheet(write_error=RuntimeError("unavailable"))
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=failing,
            ),
        ):
            response = self.client.post(
                "/neosektor/ballmat/update?side=east",
                json={
                    "side": "east",
                    "waves": {"first": {"count": 12}},
                    "open_bays": 2,
                    "bay_statuses": {"Bay 1": "Full"},
                },
            )

        self.assertEqual(response.status_code, 200)
        saved = NeoSektorOperationalSetting.query.one()
        self.assertTrue(saved.google_mirror_sync_needed)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="EAST", wave_name="1ST WAVE"
            ).one().count,
            12,
        )

        self._login("grandmaster")
        healthy = _FakeWorksheet()
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=healthy,
            ),
        ):
            retry = self.client.post(
                "/motherbrain/system-settings",
                data={"action": "retry_neosektor_google_mirror"},
            )

        self.assertEqual(retry.status_code, 302)
        self.assertFalse(NeoSektorOperationalSetting.query.one().google_mirror_sync_needed)
        self.assertEqual(set(cell for cell, _value in healthy.updates), set(SHEET_CELL_ORDER))

    def test_neo_only_performs_no_operational_google_reads_or_writes(self):
        self._set_mode(NEO_ONLY)
        apply_standalone_compat_values(self.gateway, _complete_sheet_values())
        db.session.commit()
        self._login("simulator")
        with patch(
            "app.services.neosektor_sheets_compat._get_worksheet"
        ) as worksheet:
            state = self.client.get("/neosektor/live-counts/state")
            edit = self.client.post(
                "/neosektor/ballmat/update?side=east",
                json={
                    "side": "east",
                    "waves": {"first": {"count": 13}},
                    "open_bays": 2,
                    "bay_statuses": {"Bay 1": "Full"},
                },
            )

        self.assertEqual(state.status_code, 200)
        self.assertEqual(edit.status_code, 200)
        worksheet.assert_not_called()

    def _set_mode(self, mode):
        settings = NeoSektorOperationalSetting.query.filter_by(
            gateway_id=self.gateway.id
        ).first()
        if not settings:
            settings = NeoSektorOperationalSetting(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
            )
            db.session.add(settings)
        settings.integration_mode = mode
        db.session.commit()

    def _login(self, role):
        self.client.post("/logout")
        username = f"integration_{role}"
        user = User.query.filter_by(username=username).first()
        if not user:
            user = User(username=username, role=role, is_active=True)
            set_user_password(user, "TestPassword123!")
            db.session.add(user)
            db.session.flush()
            backfill_default_gateway_node_roles(user, role=role)
            db.session.commit()
        return self.client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
        )


if __name__ == "__main__":
    unittest.main()
