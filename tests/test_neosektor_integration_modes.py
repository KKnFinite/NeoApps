import os
import unittest
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.orm import Session

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
from app.services.neosektor_live_counts import (
    apply_standalone_compat_values,
    driver_routing_state_payload,
    update_tunnel_driver_offset,
)
from app.services.neosektor_live_refresh import (
    COUNT_STATE_SCOPE,
    ROUTING_STATE_SCOPE,
    neosektor_state_revision,
)
from app.services.neosektor_sheets_compat import (
    GOOGLE_PRIMARY,
    NEO_ONLY,
    NEO_PRIMARY_GOOGLE_MIRROR,
    SHEET_CELL_ORDER,
    clear_neosektor_google_cache,
    google_primary_operational_values,
    google_primary_wave_timer_starts,
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
        self.assertEqual(NeoSektorOperationalSetting.query.count(), 0)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)
        self.assertEqual(NeoSektorOpenBayState.query.count(), 0)
        self.assertEqual(NeoSektorBayStatus.query.count(), 0)

    def test_google_primary_all_up_observation_is_process_local(self):
        worksheet = _FakeWorksheet(
            _complete_sheet_values(B2=0, C2=0, D2=0, B3=1, C3=0, D3=0)
        )
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            google_primary_operational_values(self.gateway)
            started = google_primary_wave_timer_starts(self.gateway)
            worksheet.values["B2"] = 1
            google_primary_operational_values(self.gateway, force=True)
            cleared = google_primary_wave_timer_starts(self.gateway)

        self.assertIn("1ST WAVE", started)
        self.assertNotIn("2ND WAVE", started)
        self.assertNotIn("1ST WAVE", cleared)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)

    def test_google_primary_operational_change_invalidates_revision(self):
        worksheet = _FakeWorksheet()
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            first_revision = neosektor_state_revision(
                self.gateway,
                COUNT_STATE_SCOPE,
            )
            worksheet.values["B2"] = 8
            clear_neosektor_google_cache(self.gateway)
            second_revision = neosektor_state_revision(
                self.gateway,
                COUNT_STATE_SCOPE,
            )

        self.assertNotEqual(first_revision, second_revision)
        self.assertEqual(worksheet.batch_reads, [SHEET_CELL_ORDER, SHEET_CELL_ORDER])
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)
        self.assertEqual(NeoSektorOpenBayState.query.count(), 0)
        self.assertEqual(NeoSektorBayStatus.query.count(), 0)

    def test_google_primary_neo_owned_driver_offset_invalidates_routing_revision(self):
        worksheet = _FakeWorksheet()
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            first_revision = neosektor_state_revision(
                self.gateway,
                ROUTING_STATE_SCOPE,
            )
            update_tunnel_driver_offset(self.gateway, {"west_offset": 3})
            db.session.commit()
            second_revision = neosektor_state_revision(
                self.gateway,
                ROUTING_STATE_SCOPE,
            )

        self.assertNotEqual(first_revision, second_revision)
        self.assertEqual(worksheet.batch_reads, [SHEET_CELL_ORDER])

    def test_state_gets_are_read_only_in_all_integration_modes(self):
        self._login("simulator")
        worksheet = _FakeWorksheet()
        statements = {"writes": 0, "commits": 0}

        def track_statement(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().split(None, 1)[0].upper() in {
                "INSERT",
                "UPDATE",
                "DELETE",
            }:
                statements["writes"] += 1

        def track_commit(_session):
            statements["commits"] += 1

        engine = db.engine
        event.listen(engine, "before_cursor_execute", track_statement)
        event.listen(Session, "after_commit", track_commit)
        try:
            with (
                patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
                patch(
                    "app.services.neosektor_sheets_compat._get_worksheet",
                    return_value=worksheet,
                ),
            ):
                for mode in (
                    GOOGLE_PRIMARY,
                    NEO_PRIMARY_GOOGLE_MIRROR,
                    NEO_ONLY,
                ):
                    with self.subTest(mode=mode):
                        self._set_mode(mode)
                        if mode != GOOGLE_PRIMARY:
                            apply_standalone_compat_values(
                                self.gateway,
                                _complete_sheet_values(),
                            )
                            db.session.commit()
                        clear_neosektor_google_cache(self.gateway)
                        statements.update(writes=0, commits=0)

                        response = self.client.get("/neosektor/live-counts/state")

                        self.assertEqual(response.status_code, 200)
                        self.assertTrue(response.get_json()["ok"])
                        self.assertEqual(statements["writes"], 0)
                        self.assertEqual(statements["commits"], 0)
        finally:
            event.remove(engine, "before_cursor_execute", track_statement)
            event.remove(Session, "after_commit", track_commit)

        self.assertEqual(worksheet.batch_reads, [SHEET_CELL_ORDER])

    def test_initialized_page_get_is_read_only_in_all_integration_modes(self):
        self._login("simulator")
        worksheet = _FakeWorksheet()
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            for mode in (
                GOOGLE_PRIMARY,
                NEO_PRIMARY_GOOGLE_MIRROR,
                NEO_ONLY,
            ):
                with self.subTest(mode=mode):
                    self._set_mode(mode)
                    if mode != GOOGLE_PRIMARY:
                        apply_standalone_compat_values(
                            self.gateway,
                            _complete_sheet_values(),
                        )
                        db.session.commit()
                    clear_neosektor_google_cache(self.gateway)
                    self.assertEqual(
                        self.client.get("/neosektor/live-counts").status_code,
                        200,
                    )

                    statements = {"writes": 0, "commits": 0}

                    def track_statement(
                        _conn,
                        _cursor,
                        statement,
                        _params,
                        _context,
                        _many,
                    ):
                        if statement.lstrip().split(None, 1)[0].upper() in {
                            "INSERT",
                            "UPDATE",
                            "DELETE",
                        }:
                            statements["writes"] += 1

                    def track_commit(_session):
                        statements["commits"] += 1

                    event.listen(db.engine, "before_cursor_execute", track_statement)
                    event.listen(Session, "after_commit", track_commit)
                    try:
                        response = self.client.get("/neosektor/live-counts")
                    finally:
                        event.remove(
                            db.engine,
                            "before_cursor_execute",
                            track_statement,
                        )
                        event.remove(Session, "after_commit", track_commit)

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(statements["writes"], 0)
                    self.assertEqual(statements["commits"], 0)

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

    def test_neo_only_ballmat_write_reuses_one_operational_bundle(self):
        self._set_mode(NEO_ONLY)
        apply_standalone_compat_values(self.gateway, _complete_sheet_values())
        db.session.commit()
        self._login("simulator")

        with patch(
            "app.services.neosektor_sheets_compat._get_worksheet"
        ) as worksheet:
            response, metrics = self._capture_post_metrics(
                "/neosektor/ballmat/update?side=east",
                {
                    "side": "east",
                    "waves": {
                        "first": {"count": 11, "status": "Full"},
                        "second": {"count": 6, "status": "Moderate"},
                    },
                    "open_bays": 3,
                    "bay_statuses": {
                        "Bay 1": "Full",
                        "Bay 2": "Moderate",
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(metrics["selects"], 16)
        self.assertEqual(metrics["inserts"], 0)
        self.assertEqual(metrics["commits"], 1)
        self.assertEqual(
            metrics["table_selects"],
            {
                "neosektor_operational_settings": 1,
                "neosektor_sort_states": 1,
                "neosektor_wave_states": 1,
                "neosektor_ballmat_wave_counts": 1,
                "neosektor_ballmat_counts": 1,
                "neosektor_open_bay_states": 1,
                "neosektor_bay_statuses": 1,
                "neosektor_driver_route_settings": 0,
            },
        )
        worksheet.assert_not_called()

    def test_mirror_write_commits_neo_before_changed_cells_only(self):
        self._set_mode(NEO_PRIMARY_GOOGLE_MIRROR)
        apply_standalone_compat_values(self.gateway, _complete_sheet_values())
        db.session.commit()
        self._login("simulator")
        worksheet = _FakeWorksheet()
        order = []
        original_update = worksheet.update_acell

        def ordered_update(cell, value):
            order.append(("google", cell))
            original_update(cell, value)

        worksheet.update_acell = ordered_update
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            response, metrics = self._capture_post_metrics(
                "/neosektor/ballmat/update?side=east",
                {
                    "side": "east",
                    "waves": {"first": {"count": 11}},
                    "open_bays": 3,
                    "bay_statuses": {"Bay 1": "Moderate"},
                },
                commit_order=order,
            )

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(metrics["selects"], 17)
        self.assertEqual(metrics["commits"], 1)
        self.assertEqual(
            worksheet.updates,
            [("B2", 11), ("B4", 3), ("B6", "Moderate")],
        )
        self.assertEqual(order[0], ("commit", None))
        self.assertTrue(all(kind == "google" for kind, _cell in order[1:]))

    def test_google_primary_write_skips_neo_mirror_and_duplicate_rows(self):
        self._set_mode(GOOGLE_PRIMARY)
        self._login("simulator")
        worksheet = _FakeWorksheet()
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
            patch(
                "app.neonodes.neosektor.routes.mirror_neosektor_operational_values"
            ) as mirror,
        ):
            google_primary_operational_values(self.gateway)
            worksheet.batch_reads.clear()
            response, metrics = self._capture_post_metrics(
                "/neosektor/ballmat/update?side=east",
                {
                    "side": "east",
                    "waves": {"first": {"count": 9}},
                    "open_bays": 3,
                    "bay_statuses": {"Bay 1": "Moderate"},
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(metrics["selects"], 12)
        self.assertEqual(metrics["commits"], 1)
        self.assertEqual(worksheet.batch_reads, [])
        self.assertEqual(
            worksheet.updates,
            [("B2", 9), ("B4", 3), ("B6", "Moderate")],
        )
        mirror.assert_not_called()
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)
        self.assertEqual(NeoSektorOpenBayState.query.count(), 0)
        self.assertEqual(NeoSektorBayStatus.query.count(), 0)

    def test_tunnel_writes_reuse_one_operational_bundle(self):
        self._set_mode(NEO_ONLY)
        apply_standalone_compat_values(self.gateway, _complete_sheet_values())
        driver_routing_state_payload(self.gateway)
        db.session.commit()
        self._login("simulator")

        actions = (
            (
                "/neosektor/tunnel-conductor/ballmat",
                {
                    "side": "east",
                    "waves": {"first": {"count": 8}},
                    "open_bays": 2,
                    "bay_statuses": {"Bay 1": "Moderate"},
                },
            ),
            (
                "/neosektor/tunnel-conductor/wave",
                {"wave": "first", "delta": 1},
            ),
            (
                "/neosektor/tunnel-conductor/offset",
                {"west_offset": 4},
            ),
            (
                "/neosektor/tunnel-conductor/settings",
                {
                    "first_modifier": 52,
                    "second_modifier": 31,
                    "down_timer_minutes": 22,
                },
            ),
        )
        for url, payload in actions:
            with self.subTest(url=url):
                response, metrics = self._capture_post_metrics(url, payload)
                self.assertEqual(response.status_code, 200)
                self.assertLessEqual(metrics["selects"], 17)
                self.assertEqual(metrics["commits"], 1)
                self.assertTrue(
                    all(count == 1 for count in metrics["table_selects"].values())
                )

    def _capture_post_metrics(self, url, payload, *, commit_order=None):
        statements = []
        commits = []

        def capture_statement(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        def capture_commit(_session):
            commits.append(True)
            if commit_order is not None:
                commit_order.append(("commit", None))

        engine = db.engine
        event.listen(engine, "before_cursor_execute", capture_statement)
        event.listen(Session, "after_commit", capture_commit)
        try:
            response = self.client.post(url, json=payload)
        finally:
            event.remove(engine, "before_cursor_execute", capture_statement)
            event.remove(Session, "after_commit", capture_commit)

        kinds = {
            kind: sum(
                statement.lstrip().split(None, 1)[0].upper() == kind
                for statement in statements
            )
            for kind in ("SELECT", "INSERT", "UPDATE", "DELETE")
        }
        select_sql = [
            statement.lower()
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
        ]
        tables = (
            "neosektor_operational_settings",
            "neosektor_sort_states",
            "neosektor_wave_states",
            "neosektor_ballmat_wave_counts",
            "neosektor_ballmat_counts",
            "neosektor_open_bay_states",
            "neosektor_bay_statuses",
            "neosektor_driver_route_settings",
        )
        return response, {
            "selects": kinds["SELECT"],
            "inserts": kinds["INSERT"],
            "updates": kinds["UPDATE"],
            "deletes": kinds["DELETE"],
            "commits": len(commits),
            "table_selects": {
                table: sum(table in statement for statement in select_sql)
                for table in tables
            },
        }

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
