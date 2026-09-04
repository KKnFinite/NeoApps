from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from app.extensions import db
from app.models import (
    NeoSektorBallmatCount,
    NeoSektorBallmatWaveCount,
    NeoSektorBayStatus,
    NeoSektorDriverRouteSetting,
    NeoSektorOpenBayState,
    NeoSektorOperationalSetting,
    NeoSektorSortState,
    NeoSektorWaveState,
    SortDateMission,
)
from app.services.operation_scope import current_operational_sort_operation
from app.services.node_refresh import node_auto_refresh_status
from app.services.live_screen_refresh import live_screen_refresh_value


STATUS_LABELS = ("Empty", "Light", "Moderate", "Full", "Overflowing")
STATUS_RANKS = {label: index for index, label in enumerate(STATUS_LABELS)}
DEFAULT_SORT_NAME = "night"
DEFAULT_ACTIVE_WAVE = "1ST WAVE"
DEFAULT_WAVES = (
    ("first", "1ST WAVE"),
    ("second", "2ND WAVE"),
)
DEFAULT_BALLMAT_SIDES = (
    ("east", "EAST", "EBM"),
    ("west", "WEST", "WBM"),
)
DEFAULT_BAYS = (
    ("EAST", "Bay 1"),
    ("EAST", "Bay 2"),
    ("EAST", "Bay 3"),
    ("WEST", "Bay 4"),
    ("WEST", "Bay 5"),
)
DRIVER_ROUTE_FIRST_WAVE_NAME = "1ST WAVE ROUTE"
DRIVER_ROUTE_SECOND_WAVE_NAME = "2ND WAVE ROUTE"
DRIVER_ROUTE_WEST_OFFSET_NAME = "WEST OFFSET"
DRIVER_ROUTE_FIRST_WAVE_OVERRIDE_NAME = "1ST WAVE OVERRIDE"
DRIVER_ROUTE_SECOND_WAVE_OVERRIDE_NAME = "2ND WAVE OVERRIDE"
DRIVER_ROUTE_BAY_PRIORITY_ENABLED_NAMES = tuple(
    f"{bay_name.upper()} PRIORITY ENABLED" for _side, bay_name in DEFAULT_BAYS
)
DRIVER_ROUTE_OVERRIDE_VALUES = ("auto", "east", "west")
DEFAULT_DRIVER_ROUTES = (
    DRIVER_ROUTE_FIRST_WAVE_NAME,
    DRIVER_ROUTE_SECOND_WAVE_NAME,
    DRIVER_ROUTE_WEST_OFFSET_NAME,
    DRIVER_ROUTE_FIRST_WAVE_OVERRIDE_NAME,
    DRIVER_ROUTE_SECOND_WAVE_OVERRIDE_NAME,
    *DRIVER_ROUTE_BAY_PRIORITY_ENABLED_NAMES,
)
DEFAULT_FIRST_WAVE_UNLOAD_MODIFIER = 45
DEFAULT_SECOND_WAVE_UNLOAD_MODIFIER = 37
DEFAULT_ALL_UP_TO_DOWN_MINUTES = 15
UNLOAD_MODIFIER_MAX = 999
ALL_UP_TO_DOWN_MINUTES_MAX = 120
MAIN_BALLMAT_COUNT_MAX = 99
LEFT_TO_ARRIVE_MAX = 999
DRIVER_OFFSET_MAX = 20
TUNNEL_CONDUCTOR_VIEW_PERMISSION = "neosektor.conductor.view"
TUNNEL_CONDUCTOR_EDIT_PERMISSION = "neosektor.tunnel_conductor.edit"


@dataclass
class _PersistentStateChangeTracker:
    changed: bool = False

    def mark_changed(self):
        self.changed = True


@dataclass
class NeoSektorOperationalStateBundle:
    """One coherent set of NeoSektor operational rows for a request."""

    gateway: object
    sort_date: date
    sort_name: str
    initialize: bool
    refresh_status: dict | None
    operational_settings: object
    integration_mode: str
    sort_state: object
    ballmat_wave_counts: list
    waves: list
    ballmats: list
    open_bays: list
    bay_statuses: list
    timer_rows: list
    _change_tracker: _PersistentStateChangeTracker = field(repr=False)
    google_cells: dict | None = None
    driver_routes: list | None = None
    routing_sort_state: object | None = None

    @classmethod
    def load(
        cls,
        gateway,
        sort_date=None,
        sort_name=None,
        *,
        initialize=True,
        refresh_status=None,
        include_routing=False,
    ):
        sort_date = sort_date or date.today()
        sort_name = normalize_sort_name(sort_name)
        change_tracker = _PersistentStateChangeTracker()
        settings = _operational_settings_for_state(
            gateway,
            initialize=initialize,
            change_tracker=change_tracker,
        )
        integration_mode = _neosektor_integration_mode(
            gateway,
            settings=settings,
        )

        if integration_mode == "google_primary":
            from app.services.neosektor_sheets_compat import (
                google_primary_operational_values,
                google_primary_wave_timer_starts,
            )

            cells = google_primary_operational_values(gateway)
            if initialize:
                sort_state = get_or_create_sort_state(
                    gateway,
                    sort_date,
                    sort_name,
                    change_tracker=change_tracker,
                )
                timer_rows = _get_or_create_waves(
                    sort_state,
                    change_tracker=change_tracker,
                )
            else:
                sort_state = _copy_sort_state(None, gateway, sort_date, sort_name)
                timer_starts = google_primary_wave_timer_starts(
                    gateway,
                    operational_settings=settings,
                )
                timer_rows = [
                    SimpleNamespace(
                        wave_name=wave_name,
                        all_up_started_at=timer_starts.get(wave_name),
                    )
                    for _wave_key, wave_name in DEFAULT_WAVES
                ]
            bundle = cls(
                gateway=gateway,
                sort_date=sort_date,
                sort_name=sort_name,
                initialize=initialize,
                refresh_status=refresh_status,
                operational_settings=settings,
                integration_mode=integration_mode,
                sort_state=sort_state,
                ballmat_wave_counts=[],
                waves=[],
                ballmats=[],
                open_bays=[],
                bay_statuses=[],
                timer_rows=timer_rows,
                _change_tracker=change_tracker,
                google_cells=dict(cells),
            )
        else:
            if initialize:
                sort_state = get_or_create_sort_state(
                    gateway,
                    sort_date,
                    sort_name,
                    change_tracker=change_tracker,
                )
                ballmat_wave_counts = _get_or_create_ballmat_wave_counts(
                    sort_state,
                    change_tracker=change_tracker,
                )
                waves = _get_or_create_waves(
                    sort_state,
                    change_tracker=change_tracker,
                )
                ballmats = _get_or_create_ballmats(
                    sort_state,
                    change_tracker=change_tracker,
                )
                open_bays = _get_or_create_open_bays(
                    sort_state,
                    change_tracker=change_tracker,
                )
                bay_statuses = _get_or_create_bay_statuses(
                    sort_state,
                    change_tracker=change_tracker,
                )
            else:
                (
                    sort_state,
                    ballmat_wave_counts,
                    waves,
                    ballmats,
                    open_bays,
                    bay_statuses,
                ) = _read_only_ballmat_components(gateway, sort_date, sort_name)
            bundle = cls(
                gateway=gateway,
                sort_date=sort_date,
                sort_name=sort_name,
                initialize=initialize,
                refresh_status=refresh_status,
                operational_settings=settings,
                integration_mode=integration_mode,
                sort_state=sort_state,
                ballmat_wave_counts=ballmat_wave_counts,
                waves=waves,
                ballmats=ballmats,
                open_bays=open_bays,
                bay_statuses=bay_statuses,
                timer_rows=waves,
                _change_tracker=change_tracker,
            )

        if include_routing:
            bundle.ensure_driver_routes()
        return bundle

    def ensure_driver_routes(self):
        if self.driver_routes is not None:
            return self.driver_routes
        if self.initialize:
            self.routing_sort_state = self.sort_state
            self.driver_routes = _get_or_create_driver_routes(
                self.sort_state,
                change_tracker=self._change_tracker,
            )
        else:
            self.routing_sort_state, self.driver_routes = (
                _read_only_sort_and_driver_routes(
                    self.gateway,
                    self.sort_date,
                    self.sort_name,
                )
            )
        return self.driver_routes

    @property
    def persistent_state_changed(self):
        return self._change_tracker.changed

    def resolved_refresh_status(self):
        if self.refresh_status is None:
            self.refresh_status = neosektor_refresh_status(self.gateway)
        return self.refresh_status

    def apply_google_updates(self, updates):
        if self.google_cells is not None:
            self.google_cells.update(updates)

    def operational_cell_values(self):
        if self.integration_mode == "google_primary":
            return dict(self.google_cells or {})

        wave_counts = {
            (row.side, row.wave_name): max(row.count or 0, 0)
            for row in self.ballmat_wave_counts
        }
        waves = {
            row.wave_name: max(row.planned_count or 0, 0)
            for row in self.waves
        }
        open_bays = {
            row.side: max(row.open_count or 0, 0)
            for row in self.open_bays
        }
        bays = {row.bay_name: _status(row.status) for row in self.bay_statuses}
        return {
            "B2": wave_counts[("EAST", "1ST WAVE")],
            "C2": wave_counts[("WEST", "1ST WAVE")],
            "D2": waves["1ST WAVE"],
            "B3": wave_counts[("EAST", "2ND WAVE")],
            "C3": wave_counts[("WEST", "2ND WAVE")],
            "D3": waves["2ND WAVE"],
            "B4": open_bays["EAST"],
            "C4": open_bays["WEST"],
            "B6": bays["Bay 1"],
            "B8": bays["Bay 2"],
            "B10": bays["Bay 3"],
            "C6": bays["Bay 4"],
            "C8": bays["Bay 5"],
        }

    def mirror_operational_cell_values(self):
        """Return Google mirror values, including canonical LTU display output."""
        values = self.operational_cell_values()
        waves = self.ballmat_state_payload()["waves"]
        values.update(_google_mirror_left_to_unload_values(waves))
        return values

    def ballmat_state_payload(self):
        if self.integration_mode == "google_primary":
            (
                wave_counts,
                waves,
                ballmats,
                open_bays,
                bay_statuses,
            ) = self._google_ballmat_components()
            planned_total = max(self.google_cells["D2"], 0) + max(
                self.google_cells["D3"], 0
            )
            unloaded_total = sum(max(row.count or 0, 0) for row in wave_counts)
        else:
            wave_counts = self.ballmat_wave_counts
            waves = self.waves
            ballmats = self.ballmats
            open_bays = self.open_bays
            bay_statuses = self.bay_statuses
            _sync_ballmat_rollups(
                self.sort_state,
                wave_counts,
                waves,
                ballmats,
                change_tracker=self._change_tracker,
            )
            planned_total = max(0, self.sort_state.planned_total or 0)
            unloaded_total = max(0, self.sort_state.unloaded_total or 0)

        sides = _side_state_views(
            wave_counts,
            ballmats,
            open_bays,
            bay_statuses,
        )
        wave_views = _wave_views(
            waves,
            sides,
            self.operational_settings,
            timer_rows=self.timer_rows,
            persist_timer=self.initialize,
            change_tracker=self._change_tracker,
        )
        if self.initialize:
            db.session.flush()

        return {
            "summary": {
                "sort_date": self.sort_date.isoformat(),
                "sort_name": self.sort_name.upper(),
                "active_wave": self.sort_state.active_wave,
                "planned_total": planned_total,
                "unloaded_total": unloaded_total,
                "left_to_unload": max(planned_total - unloaded_total, 0),
                "completion_percent": _completion_percent(
                    planned_total,
                    unloaded_total,
                ),
            },
            "sides": sides,
            "waves": wave_views,
            "operational_settings": _operational_settings_view(
                self.operational_settings
            ),
            "integration": _neosektor_integration_status(
                self.gateway,
                settings=self.operational_settings,
            ),
            "refresh": self.resolved_refresh_status(),
        }

    def driver_routing_state_payload(self):
        state = self.ballmat_state_payload()
        driver_routes = self.ensure_driver_routes()
        routing = _driver_routing_calculation(
            self.gateway,
            self.routing_sort_state or self.sort_state,
            state["sides"],
            state["waves"],
            driver_routes,
        )
        _sync_driver_route_values(
            driver_routes,
            routing,
            change_tracker=self._change_tracker,
        )
        if self.initialize:
            db.session.flush()
        state["routing"] = routing
        state["driver_routes"] = [
            _driver_route_view(row) for row in driver_routes
        ]
        return state

    def _google_ballmat_components(self):
        cells = self.google_cells
        wave_cell_map = {
            ("EAST", "1ST WAVE"): "B2",
            ("WEST", "1ST WAVE"): "C2",
            ("EAST", "2ND WAVE"): "B3",
            ("WEST", "2ND WAVE"): "C3",
        }
        wave_counts = []
        display_order = 0
        for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES:
            for _wave_key, wave_name in DEFAULT_WAVES:
                display_order += 1
                count = cells[wave_cell_map[(side_label, wave_name)]]
                wave_counts.append(
                    SimpleNamespace(
                        side=side_label,
                        wave_name=wave_name,
                        count=count,
                        status="Light" if count else "Empty",
                        display_order=display_order,
                    )
                )

        ballmats = []
        for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES:
            matching = [row for row in wave_counts if row.side == side_label]
            total = sum(row.count for row in matching)
            ballmats.append(
                SimpleNamespace(
                    side=side_label,
                    count=total,
                    status=_aggregate_status(matching, total),
                )
            )
        open_bays = [
            SimpleNamespace(side="EAST", open_count=cells["B4"]),
            SimpleNamespace(side="WEST", open_count=cells["C4"]),
        ]
        bay_statuses = [
            SimpleNamespace(
                side="EAST",
                bay_name="Bay 1",
                status=cells["B6"],
                display_order=1,
            ),
            SimpleNamespace(
                side="EAST",
                bay_name="Bay 2",
                status=cells["B8"],
                display_order=2,
            ),
            SimpleNamespace(
                side="EAST",
                bay_name="Bay 3",
                status=cells["B10"],
                display_order=3,
            ),
            SimpleNamespace(
                side="WEST",
                bay_name="Bay 4",
                status=cells["C6"],
                display_order=4,
            ),
            SimpleNamespace(
                side="WEST",
                bay_name="Bay 5",
                status=cells["C8"],
                display_order=5,
            ),
        ]
        waves = []
        for index, (_wave_key, wave_name) in enumerate(DEFAULT_WAVES, start=1):
            matching = [row for row in wave_counts if row.wave_name == wave_name]
            unloaded = sum(row.count for row in matching)
            waves.append(
                SimpleNamespace(
                    wave_name=wave_name,
                    planned_count=cells[
                        "D2" if wave_name == "1ST WAVE" else "D3"
                    ],
                    unloaded_count=unloaded,
                    status=_aggregate_status(matching, unloaded),
                    display_order=index,
                )
            )
        return wave_counts, waves, ballmats, open_bays, bay_statuses


def live_counts_context(gateway, sort_date=None, sort_name=None, *, bundle=None):
    state = ballmat_state_payload(
        gateway,
        sort_date,
        sort_name,
        bundle=bundle,
    )
    return {
        "status_labels": STATUS_LABELS,
        "summary": state["summary"],
        "waves": state["waves"],
        "sides": state["sides"],
        "operational_settings": state["operational_settings"],
        "integration": state["integration"],
        "refresh_status": state["refresh"],
    }


def ballmat_operations_context(
    gateway,
    selected_side,
    sort_date=None,
    sort_name=None,
    *,
    bundle=None,
):
    selected_side = normalize_ballmat_side(selected_side) or "east"
    state = ballmat_state_payload(
        gateway,
        sort_date,
        sort_name,
        bundle=bundle,
    )

    return {
        "selected_side": selected_side,
        "selected_side_label": side_display_label(selected_side),
        "selected_manager_label": side_manager_label(selected_side),
        "state": state,
        "status_labels": STATUS_LABELS,
    }


def ballmat_state_payload(
    gateway,
    sort_date=None,
    sort_name=None,
    *,
    initialize=True,
    refresh_status=None,
    bundle=None,
):
    bundle = bundle or NeoSektorOperationalStateBundle.load(
        gateway,
        sort_date,
        sort_name,
        initialize=initialize,
        refresh_status=refresh_status,
    )
    return bundle.ballmat_state_payload()


def _google_ballmat_updates(selected_side, payload):
    payload = payload or {}
    cell_map = {
        "east": {"first": "B2", "second": "B3", "open": "B4"},
        "west": {"first": "C2", "second": "C3", "open": "C4"},
    }[selected_side]
    updates = {}
    wave_payload = payload.get("waves") or {}
    for wave_key in ("first", "second"):
        values = wave_payload.get(wave_key) or {}
        if "count" in values:
            updates[cell_map[wave_key]] = _clean_count(
                values.get("count"),
                maximum=MAIN_BALLMAT_COUNT_MAX,
            )
    if "open_bays" in payload:
        updates[cell_map["open"]] = _clean_count(
            payload.get("open_bays"),
            maximum=MAIN_BALLMAT_COUNT_MAX,
        )

    bay_cells = {
        "Bay 1": "B6",
        "Bay 2": "B8",
        "Bay 3": "B10",
        "Bay 4": "C6",
        "Bay 5": "C8",
    }
    for bay_name, value in (payload.get("bay_statuses") or {}).items():
        if bay_name in bay_cells:
            updates[bay_cells[bay_name]] = _status(value)
    return updates


def _neosektor_integration_mode(gateway, settings=None):
    from app.services.neosektor_sheets_compat import neosektor_integration_mode

    return neosektor_integration_mode(gateway, settings=settings)


def _neosektor_integration_status(gateway, settings=None):
    from app.services.neosektor_sheets_compat import neosektor_integration_status

    return neosektor_integration_status(gateway, settings=settings)


NEOSEKTOR_LIVE_COUNTS_REFRESH_KEY = "neosektor.live_counts"
NEOSEKTOR_TUNNEL_CONDUCTOR_REFRESH_KEY = "neosektor.tunnel_conductor"
NEOSEKTOR_EBM_REFRESH_KEY = "neosektor.ebm"
NEOSEKTOR_WBM_REFRESH_KEY = "neosektor.wbm"
NEOSEKTOR_DISCHARGE_REFRESH_KEY = "neosektor.discharge"
NEOSEKTOR_DRIVER_ROUTING_REFRESH_KEY = "neosektor.driver_routing"
NEOSEKTOR_REFRESH_KEYS = (
    NEOSEKTOR_LIVE_COUNTS_REFRESH_KEY,
    NEOSEKTOR_TUNNEL_CONDUCTOR_REFRESH_KEY,
    NEOSEKTOR_EBM_REFRESH_KEY,
    NEOSEKTOR_WBM_REFRESH_KEY,
    NEOSEKTOR_DISCHARGE_REFRESH_KEY,
    NEOSEKTOR_DRIVER_ROUTING_REFRESH_KEY,
)


def neosektor_refresh_status(
    gateway,
    now=None,
    *,
    screen_key=NEOSEKTOR_LIVE_COUNTS_REFRESH_KEY,
):
    return _neosektor_screen_refresh_status(
        gateway,
        screen_key,
        now=now,
    )


def driver_routing_refresh_status(gateway, now=None, *, screen_key=None):
    """Passive wall-board refresh status with one canonical next-window wake."""
    return _neosektor_screen_refresh_status(
        gateway,
        NEOSEKTOR_DRIVER_ROUTING_REFRESH_KEY,
        now=now,
        schedule_next_window=True,
    )


def _neosektor_screen_refresh_status(
    gateway,
    screen_key,
    *,
    now=None,
    schedule_next_window=False,
):
    """Combine canonical Ops-window eligibility with one screen's DB override."""
    status = node_auto_refresh_status(
        gateway,
        now=now,
        schedule_next_window=schedule_next_window,
    )
    setting = live_screen_refresh_value(gateway, screen_key)
    status = {
        **status,
        "screen_key": screen_key,
        "live_screen_refresh_interval_ms": setting.effective_interval_ms,
        "refresh_configured_label": setting.configured_label,
        "refresh_effective_label": setting.effective_label,
        "refresh_source": setting.source,
    }
    if status["auto_refresh_enabled"] and not setting.enabled:
        status.update(
            auto_refresh_enabled=False,
            reason="screen_refresh_off",
            message="Live updates off in NeoSektor Settings.",
            live_status_label="Live updates off in NeoSektor Settings.",
        )
    return status


def update_ballmat_side(
    gateway,
    selected_side,
    payload,
    sort_date=None,
    sort_name=None,
    *,
    bundle=None,
    include_routing_state=False,
):
    selected_side = normalize_ballmat_side(selected_side)
    target_side = normalize_ballmat_side((payload or {}).get("side"))
    if not selected_side or not target_side or selected_side != target_side:
        raise ValueError("Selected side does not match update side.")

    bundle = bundle or NeoSektorOperationalStateBundle.load(
        gateway,
        sort_date,
        sort_name,
    )
    if bundle.integration_mode == "google_primary":
        updates = _google_ballmat_updates(selected_side, payload)
        if updates:
            from app.services.neosektor_sheets_compat import (
                write_google_primary_operational_values,
            )

            write_google_primary_operational_values(
                gateway,
                updates,
                integration_mode=bundle.integration_mode,
            )
            bundle.apply_google_updates(updates)
        if include_routing_state:
            return bundle.driver_routing_state_payload()
        return bundle.ballmat_state_payload()

    side_label = side_display_label(selected_side)
    wave_payload = (payload or {}).get("waves") or {}
    rows_by_wave = {
        row.wave_name: row
        for row in bundle.ballmat_wave_counts
        if row.side == side_label
    }
    for wave_key, wave_name in DEFAULT_WAVES:
        row = rows_by_wave[wave_name]
        row.count = _clean_count(
            (wave_payload.get(wave_key) or {}).get("count"),
            default=row.count,
            maximum=MAIN_BALLMAT_COUNT_MAX,
        )
        row.status = _status((wave_payload.get(wave_key) or {}).get("status") or row.status)

    open_bay_row = next(
        row for row in bundle.open_bays if row.side == side_label
    )
    open_bay_row.open_count = _clean_count(
        (payload or {}).get("open_bays"),
        default=open_bay_row.open_count,
        maximum=MAIN_BALLMAT_COUNT_MAX,
    )

    bay_payload = (payload or {}).get("bay_statuses") or {}
    for bay in bundle.bay_statuses:
        if bay.side == side_label and bay.bay_name in bay_payload:
            bay.status = _status(bay_payload[bay.bay_name])

    if include_routing_state:
        return bundle.driver_routing_state_payload()
    return bundle.ballmat_state_payload()


def tunnel_conductor_context(
    gateway,
    sort_date=None,
    sort_name=None,
    *,
    bundle=None,
):
    return {
        "state": driver_routing_state_payload(
            gateway,
            sort_date,
            sort_name,
            bundle=bundle,
        ),
        "status_labels": STATUS_LABELS,
    }


def driver_routing_context(
    gateway,
    sort_date=None,
    sort_name=None,
    *,
    bundle=None,
):
    return {
        "state": driver_routing_state_payload(
            gateway,
            sort_date,
            sort_name,
            bundle=bundle,
        ),
    }


def driver_routing_state_payload(
    gateway,
    sort_date=None,
    sort_name=None,
    *,
    initialize=True,
    refresh_status=None,
    bundle=None,
):
    bundle = bundle or NeoSektorOperationalStateBundle.load(
        gateway,
        sort_date,
        sort_name,
        initialize=initialize,
        refresh_status=refresh_status,
        include_routing=True,
    )
    return bundle.driver_routing_state_payload()


def update_driver_routing_settings(
    gateway,
    payload,
    sort_date=None,
    sort_name=None,
    *,
    bundle=None,
):
    bundle = bundle or NeoSektorOperationalStateBundle.load(
        gateway,
        sort_date,
        sort_name,
        include_routing=True,
    )
    driver_routes = bundle.ensure_driver_routes()
    offset_row = _driver_route_by_name(driver_routes, DRIVER_ROUTE_WEST_OFFSET_NAME)
    offset_row.route_value = str(_clean_offset((payload or {}).get("west_offset")))
    return bundle.driver_routing_state_payload()


def update_tunnel_driver_offset(
    gateway,
    payload,
    sort_date=None,
    sort_name=None,
    *,
    bundle=None,
):
    return update_driver_routing_settings(
        gateway,
        payload,
        sort_date,
        sort_name,
        bundle=bundle,
    )


def update_neosektor_operational_settings(
    gateway,
    payload,
    sort_date=None,
    sort_name=None,
    *,
    bundle=None,
):
    bundle = bundle or NeoSektorOperationalStateBundle.load(
        gateway,
        sort_date,
        sort_name,
        include_routing=True,
    )
    driver_routes = bundle.ensure_driver_routes()
    settings = bundle.operational_settings
    settings.first_wave_unload_modifier = _clean_count(
        (payload or {}).get("first_modifier"),
        default=settings.first_wave_unload_modifier,
        maximum=UNLOAD_MODIFIER_MAX,
    )
    settings.second_wave_unload_modifier = _clean_count(
        (payload or {}).get("second_modifier"),
        default=settings.second_wave_unload_modifier,
        maximum=UNLOAD_MODIFIER_MAX,
    )
    settings.all_up_to_down_minutes = _clean_count(
        (payload or {}).get("down_timer_minutes"),
        default=settings.all_up_to_down_minutes,
        minimum=1,
        maximum=ALL_UP_TO_DOWN_MINUTES_MAX,
    )
    first_override = _driver_route_by_name(
        driver_routes,
        DRIVER_ROUTE_FIRST_WAVE_OVERRIDE_NAME,
    )
    second_override = _driver_route_by_name(
        driver_routes,
        DRIVER_ROUTE_SECOND_WAVE_OVERRIDE_NAME,
    )
    first_override.route_value = _clean_driver_route_override(
        (payload or {}).get("first_override"),
        default=first_override.route_value,
    )
    second_override.route_value = _clean_driver_route_override(
        (payload or {}).get("second_override"),
        default=second_override.route_value,
    )
    priority_enabled = (payload or {}).get("bay_priority_enabled") or {}
    if isinstance(priority_enabled, dict):
        for bay_name in (bay_name for _side, bay_name in DEFAULT_BAYS):
            if bay_name not in priority_enabled:
                continue
            _driver_route_by_name(
                driver_routes,
                _driver_route_bay_priority_enabled_name(bay_name),
            ).route_value = "true" if bool(priority_enabled[bay_name]) else "false"
    return bundle.driver_routing_state_payload()


def apply_standalone_compat_values(
    gateway,
    cell_values,
    sort_date=None,
    sort_name=None,
):
    """Apply only the established standalone Sheet cells to NeoSektor state."""
    sort_date = sort_date or date.today()
    sort_name = normalize_sort_name(sort_name)
    sort_state = get_or_create_sort_state(gateway, sort_date, sort_name)
    ballmat_wave_counts = _get_or_create_ballmat_wave_counts(sort_state)
    waves = _get_or_create_waves(sort_state)
    ballmats = _get_or_create_ballmats(sort_state)
    open_bays = _get_or_create_open_bays(sort_state)
    bay_statuses = _get_or_create_bay_statuses(sort_state)
    changed = 0

    wave_count_rows = {
        (row.side, row.wave_name): row
        for row in ballmat_wave_counts
    }
    wave_rows = {row.wave_name: row for row in waves}
    open_bay_rows = {row.side: row for row in open_bays}
    bay_rows = {row.bay_name: row for row in bay_statuses}

    count_targets = {
        "B2": (wave_count_rows[("EAST", "1ST WAVE")], "count", MAIN_BALLMAT_COUNT_MAX),
        "C2": (wave_count_rows[("WEST", "1ST WAVE")], "count", MAIN_BALLMAT_COUNT_MAX),
        "D2": (wave_rows["1ST WAVE"], "planned_count", LEFT_TO_ARRIVE_MAX),
        "B3": (wave_count_rows[("EAST", "2ND WAVE")], "count", MAIN_BALLMAT_COUNT_MAX),
        "C3": (wave_count_rows[("WEST", "2ND WAVE")], "count", MAIN_BALLMAT_COUNT_MAX),
        "D3": (wave_rows["2ND WAVE"], "planned_count", LEFT_TO_ARRIVE_MAX),
        "B4": (open_bay_rows["EAST"], "open_count", MAIN_BALLMAT_COUNT_MAX),
        "C4": (open_bay_rows["WEST"], "open_count", MAIN_BALLMAT_COUNT_MAX),
    }
    for cell, (row, attribute, maximum) in count_targets.items():
        parsed = _standalone_compat_count(cell_values.get(cell), maximum)
        if parsed is not None:
            changed += _assign_if_changed(row, attribute, parsed)

    status_targets = {
        "B6": bay_rows["Bay 1"],
        "B8": bay_rows["Bay 2"],
        "B10": bay_rows["Bay 3"],
        "C6": bay_rows["Bay 4"],
        "C8": bay_rows["Bay 5"],
    }
    for cell, row in status_targets.items():
        parsed = _standalone_compat_status(cell_values.get(cell))
        if parsed is not None:
            changed += _assign_if_changed(row, "status", parsed)

    _sync_ballmat_rollups(sort_state, ballmat_wave_counts, waves, ballmats)
    db.session.flush()
    return changed


def canonical_neosektor_compat_values(
    gateway,
    sort_date=None,
    sort_name=None,
):
    """Read the persisted Neon side of the standalone Sheet contract only.

    This deliberately bypasses the active integration mode.  Recovery needs to
    compare Google with the actual Neon rows, not with a GOOGLE PRIMARY display
    bundle that would read those same Google values back.
    """
    sort_date = sort_date or date.today()
    sort_name = normalize_sort_name(sort_name)
    (
        _sort_state,
        wave_counts,
        waves,
        ballmats,
        open_bays,
        bay_statuses,
    ) = _read_only_ballmat_components(gateway, sort_date, sort_name)
    wave_count_rows = {
        (row.side, row.wave_name): max(row.count or 0, 0)
        for row in wave_counts
    }
    wave_rows = {
        row.wave_name: max(row.planned_count or 0, 0)
        for row in waves
    }
    open_bay_rows = {row.side: max(row.open_count or 0, 0) for row in open_bays}
    bay_rows = {row.bay_name: _status(row.status) for row in bay_statuses}
    values = {
        "B2": wave_count_rows[("EAST", "1ST WAVE")],
        "C2": wave_count_rows[("WEST", "1ST WAVE")],
        "D2": wave_rows["1ST WAVE"],
        "B3": wave_count_rows[("EAST", "2ND WAVE")],
        "C3": wave_count_rows[("WEST", "2ND WAVE")],
        "D3": wave_rows["2ND WAVE"],
        "B4": open_bay_rows["EAST"],
        "C4": open_bay_rows["WEST"],
        "B6": bay_rows["Bay 1"],
        "B8": bay_rows["Bay 2"],
        "B10": bay_rows["Bay 3"],
        "C6": bay_rows["Bay 4"],
        "C8": bay_rows["Bay 5"],
    }
    sides = _side_state_views(wave_counts, ballmats, open_bays, bay_statuses)
    wave_views = _wave_views(
        waves,
        sides,
        _operational_settings_for_state(gateway, initialize=False),
        timer_rows=waves,
        persist_timer=False,
    )
    values.update(_google_mirror_left_to_unload_values(wave_views))
    return values


def adjust_tunnel_wave_arrivals(
    gateway,
    wave,
    delta=None,
    value=None,
    sort_date=None,
    sort_name=None,
    *,
    bundle=None,
):
    _wave_key, wave_name = normalize_wave_key(wave)
    if not wave_name:
        raise ValueError("Invalid wave.")

    bundle = bundle or NeoSektorOperationalStateBundle.load(
        gateway,
        sort_date,
        sort_name,
        include_routing=True,
    )
    if bundle.integration_mode == "google_primary":
        target_cell = "D2" if wave_name == "1ST WAVE" else "D3"
        current_value = bundle.google_cells[target_cell]
        if value is not None:
            next_value = _clean_count(
                value,
                default=current_value,
                maximum=LEFT_TO_ARRIVE_MAX,
            )
        else:
            next_value = min(
                max(current_value + _clean_delta(delta), 0),
                LEFT_TO_ARRIVE_MAX,
            )
        from app.services.neosektor_sheets_compat import (
            write_google_primary_operational_values,
        )

        write_google_primary_operational_values(
            gateway,
            {target_cell: next_value},
            integration_mode=bundle.integration_mode,
        )
        bundle.apply_google_updates({target_cell: next_value})
        return bundle.driver_routing_state_payload()

    target_row = next(row for row in bundle.waves if row.wave_name == wave_name)
    if value is not None:
        target_row.planned_count = _clean_count(
            value,
            default=target_row.planned_count,
            maximum=LEFT_TO_ARRIVE_MAX,
        )
    else:
        target_row.planned_count = min(
            max((target_row.planned_count or 0) + _clean_delta(delta), 0),
            LEFT_TO_ARRIVE_MAX,
        )
    return bundle.driver_routing_state_payload()


def get_or_create_sort_state(
    gateway,
    sort_date,
    sort_name,
    *,
    change_tracker=None,
):
    sort_state = NeoSektorSortState.query.filter_by(
        gateway_id=gateway.id,
        sort_date=sort_date,
        sort_name=sort_name,
    ).first()
    if sort_state:
        return sort_state

    sort_state = NeoSektorSortState(
        gateway_id=gateway.id,
        gateway_code=gateway.code,
        sort_date=sort_date,
        sort_name=sort_name,
        active_wave=DEFAULT_ACTIVE_WAVE,
    )
    db.session.add(sort_state)
    _mark_persistent_state_changed(change_tracker)
    db.session.flush()
    return sort_state


def get_or_create_operational_settings(gateway, *, change_tracker=None):
    settings = NeoSektorOperationalSetting.query.filter_by(
        gateway_id=gateway.id,
    ).first()
    if settings:
        return settings

    settings = NeoSektorOperationalSetting(
        gateway_id=gateway.id,
        gateway_code=gateway.code,
        first_wave_unload_modifier=DEFAULT_FIRST_WAVE_UNLOAD_MODIFIER,
        second_wave_unload_modifier=DEFAULT_SECOND_WAVE_UNLOAD_MODIFIER,
        all_up_to_down_minutes=DEFAULT_ALL_UP_TO_DOWN_MINUTES,
    )
    db.session.add(settings)
    _mark_persistent_state_changed(change_tracker)
    db.session.flush()
    return settings


def _operational_settings_for_state(
    gateway,
    *,
    initialize,
    change_tracker=None,
):
    if initialize:
        return get_or_create_operational_settings(
            gateway,
            change_tracker=change_tracker,
        )
    settings = NeoSektorOperationalSetting.query.filter_by(
        gateway_id=gateway.id
    ).first()
    if settings:
        return settings
    return SimpleNamespace(
        gateway_id=gateway.id,
        gateway_code=gateway.code,
        first_wave_unload_modifier=DEFAULT_FIRST_WAVE_UNLOAD_MODIFIER,
        second_wave_unload_modifier=DEFAULT_SECOND_WAVE_UNLOAD_MODIFIER,
        all_up_to_down_minutes=DEFAULT_ALL_UP_TO_DOWN_MINUTES,
        integration_mode="google_primary",
        google_mirror_writes_enabled=False,
        google_mirror_sync_needed=False,
        google_mirror_last_error=None,
        google_mirror_failed_at_utc=None,
        updated_at=None,
    )


def _read_only_ballmat_components(gateway, sort_date, sort_name):
    persisted = _existing_sort_state(gateway, sort_date, sort_name)
    sort_state = _copy_sort_state(persisted, gateway, sort_date, sort_name)
    sort_state_id = getattr(persisted, "id", None)

    wave_counts = _read_only_wave_counts(sort_state_id)
    waves = _read_only_waves(sort_state_id)
    ballmats = _read_only_ballmats(sort_state_id)
    open_bays = _read_only_open_bays(sort_state_id)
    bay_statuses = _read_only_bay_statuses(sort_state_id)
    return sort_state, wave_counts, waves, ballmats, open_bays, bay_statuses


def _read_only_sort_and_driver_routes(gateway, sort_date, sort_name):
    persisted = _existing_sort_state(gateway, sort_date, sort_name)
    return (
        _copy_sort_state(persisted, gateway, sort_date, sort_name),
        _read_only_driver_routes(getattr(persisted, "id", None)),
    )


def _existing_sort_state(gateway, sort_date, sort_name):
    return NeoSektorSortState.query.filter_by(
        gateway_id=gateway.id,
        sort_date=sort_date,
        sort_name=sort_name,
    ).first()


def _copy_sort_state(row, gateway, sort_date, sort_name):
    return SimpleNamespace(
        id=getattr(row, "id", None),
        gateway_id=gateway.id,
        gateway_code=gateway.code,
        sort_date=sort_date,
        sort_name=sort_name,
        active_wave=getattr(row, "active_wave", DEFAULT_ACTIVE_WAVE),
        planned_total=getattr(row, "planned_total", 0),
        unloaded_total=getattr(row, "unloaded_total", 0),
        updated_at=getattr(row, "updated_at", None),
    )


def _read_only_waves(sort_state_id):
    existing = {
        row.wave_name: row
        for row in _rows_for_sort_state(NeoSektorWaveState, sort_state_id)
    }
    return [
        SimpleNamespace(
            wave_name=wave_name,
            planned_count=getattr(existing.get(wave_name), "planned_count", 0),
            unloaded_count=getattr(existing.get(wave_name), "unloaded_count", 0),
            all_up_started_at=getattr(
                existing.get(wave_name), "all_up_started_at", None
            ),
            status=getattr(existing.get(wave_name), "status", "Empty"),
            display_order=index,
        )
        for index, (_wave_key, wave_name) in enumerate(DEFAULT_WAVES, start=1)
    ]


def _read_only_wave_counts(sort_state_id):
    existing = {
        (row.side, row.wave_name): row
        for row in _rows_for_sort_state(NeoSektorBallmatWaveCount, sort_state_id)
    }
    rows = []
    display_order = 0
    for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES:
        for _wave_key, wave_name in DEFAULT_WAVES:
            display_order += 1
            source = existing.get((side_label, wave_name))
            rows.append(
                SimpleNamespace(
                    side=side_label,
                    wave_name=wave_name,
                    count=getattr(source, "count", 0),
                    status=getattr(source, "status", "Empty"),
                    display_order=display_order,
                )
            )
    return rows


def _read_only_ballmats(sort_state_id):
    existing = {
        row.side: row
        for row in _rows_for_sort_state(NeoSektorBallmatCount, sort_state_id)
    }
    return [
        SimpleNamespace(
            side=side_label,
            count=getattr(existing.get(side_label), "count", 0),
            status=getattr(existing.get(side_label), "status", "Empty"),
        )
        for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES
    ]


def _read_only_open_bays(sort_state_id):
    existing = {
        row.side: row
        for row in _rows_for_sort_state(NeoSektorOpenBayState, sort_state_id)
    }
    return [
        SimpleNamespace(
            side=side_label,
            open_count=getattr(existing.get(side_label), "open_count", 0),
        )
        for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES
    ]


def _read_only_bay_statuses(sort_state_id):
    existing = {
        row.bay_name: row
        for row in _rows_for_sort_state(NeoSektorBayStatus, sort_state_id)
    }
    return [
        SimpleNamespace(
            side=side,
            bay_name=bay_name,
            status=getattr(existing.get(bay_name), "status", "Empty"),
            display_order=index,
        )
        for index, (side, bay_name) in enumerate(DEFAULT_BAYS, start=1)
    ]


def _read_only_driver_routes(sort_state_id):
    existing = {
        row.route_name: row
        for row in _rows_for_sort_state(NeoSektorDriverRouteSetting, sort_state_id)
    }
    return [
        SimpleNamespace(
            route_name=route_name,
            route_value=getattr(
                existing.get(route_name),
                "route_value",
                _driver_route_default_value(route_name),
            ),
            display_order=index,
        )
        for index, route_name in enumerate(DEFAULT_DRIVER_ROUTES, start=1)
    ]


def _rows_for_sort_state(model, sort_state_id):
    if sort_state_id is None:
        return []
    return model.query.filter_by(sort_state_id=sort_state_id).all()


def normalize_sort_name(sort_name):
    value = str(sort_name or "").strip().lower()
    return value or DEFAULT_SORT_NAME


def normalize_ballmat_side(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"e", "east", "ebm"}:
        return "east"
    if normalized in {"w", "west", "wbm"}:
        return "west"
    return None


def side_display_label(side):
    normalized = normalize_ballmat_side(side) or "east"
    return "EAST" if normalized == "east" else "WEST"


def side_manager_label(side):
    normalized = normalize_ballmat_side(side) or "east"
    return "EBM" if normalized == "east" else "WBM"


def normalize_wave_key(value):
    normalized = str(value or "").strip().lower()
    for wave_key, wave_name in DEFAULT_WAVES:
        wave_aliases = {
            wave_key,
            wave_name.lower(),
            wave_name.lower().replace(" ", "_"),
            wave_name.lower().replace(" ", "-"),
        }
        if normalized in wave_aliases:
            return wave_key, wave_name
    if normalized in {"1", "first", "1st", "1st_wave", "1st-wave"}:
        return "first", "1ST WAVE"
    if normalized in {"2", "second", "2nd", "2nd_wave", "2nd-wave"}:
        return "second", "2ND WAVE"
    return None, None


def _get_or_create_waves(sort_state, *, change_tracker=None):
    existing = {
        row.wave_name: row
        for row in NeoSektorWaveState.query.filter_by(sort_state_id=sort_state.id).all()
    }
    rows = []
    for index, (_wave_key, wave_name) in enumerate(DEFAULT_WAVES, start=1):
        row = existing.get(wave_name)
        if row is None:
            row = NeoSektorWaveState(
                sort_state_id=sort_state.id,
                wave_name=wave_name,
                display_order=index,
            )
            db.session.add(row)
            _mark_persistent_state_changed(change_tracker)
        rows.append(row)
    return sorted(rows, key=lambda row: row.display_order)


def _get_or_create_ballmats(sort_state, *, change_tracker=None):
    existing = {
        row.side: row
        for row in NeoSektorBallmatCount.query.filter_by(sort_state_id=sort_state.id).all()
    }
    rows = []
    for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES:
        row = existing.get(side_label)
        if row is None:
            row = NeoSektorBallmatCount(sort_state_id=sort_state.id, side=side_label)
            db.session.add(row)
            _mark_persistent_state_changed(change_tracker)
        rows.append(row)
    return rows


def _get_or_create_ballmat_wave_counts(sort_state, *, change_tracker=None):
    existing = {
        (row.side, row.wave_name): row
        for row in NeoSektorBallmatWaveCount.query.filter_by(
            sort_state_id=sort_state.id
        ).all()
    }
    rows = []
    display_order = 0
    for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES:
        for _wave_key, wave_name in DEFAULT_WAVES:
            display_order += 1
            row = existing.get((side_label, wave_name))
            if row is None:
                row = NeoSektorBallmatWaveCount(
                    sort_state_id=sort_state.id,
                    side=side_label,
                    wave_name=wave_name,
                    display_order=display_order,
                )
                db.session.add(row)
                _mark_persistent_state_changed(change_tracker)
            rows.append(row)
    return sorted(rows, key=lambda row: row.display_order)


def _get_or_create_open_bays(sort_state, *, change_tracker=None):
    existing = {
        row.side: row
        for row in NeoSektorOpenBayState.query.filter_by(sort_state_id=sort_state.id).all()
    }
    rows = []
    for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES:
        row = existing.get(side_label)
        if row is None:
            row = NeoSektorOpenBayState(sort_state_id=sort_state.id, side=side_label)
            db.session.add(row)
            _mark_persistent_state_changed(change_tracker)
        rows.append(row)
    return rows


def _get_or_create_bay_statuses(sort_state, *, change_tracker=None):
    existing = {
        row.bay_name: row
        for row in NeoSektorBayStatus.query.filter_by(sort_state_id=sort_state.id).all()
    }
    rows = []
    for index, (side, bay_name) in enumerate(DEFAULT_BAYS, start=1):
        row = existing.get(bay_name)
        if row is None:
            row = NeoSektorBayStatus(
                sort_state_id=sort_state.id,
                side=side,
                bay_name=bay_name,
                display_order=index,
            )
            db.session.add(row)
            _mark_persistent_state_changed(change_tracker)
        rows.append(row)
    return sorted(rows, key=lambda row: row.display_order)


def _get_or_create_driver_routes(sort_state, *, change_tracker=None):
    existing = {
        row.route_name: row
        for row in NeoSektorDriverRouteSetting.query.filter_by(
            sort_state_id=sort_state.id
        ).all()
    }
    rows = []
    for index, route_name in enumerate(DEFAULT_DRIVER_ROUTES, start=1):
        row = existing.get(route_name)
        if row is None:
            row = NeoSektorDriverRouteSetting(
                sort_state_id=sort_state.id,
                route_name=route_name,
                route_value=_driver_route_default_value(route_name),
                display_order=index,
            )
            db.session.add(row)
            _mark_persistent_state_changed(change_tracker)
        rows.append(row)
    return sorted(rows, key=lambda row: row.display_order)


def _completion_percent(planned_total, unloaded_total):
    if planned_total <= 0:
        return 0
    return min(round((unloaded_total / planned_total) * 100), 100)


def _wave_view(row, left_to_unload=None):
    planned = max(row.planned_count or 0, 0)
    unloaded = max(row.unloaded_count or 0, 0)
    left = max(planned - unloaded, 0) if left_to_unload is None else left_to_unload
    return {
        "name": row.wave_name,
        "planned": planned,
        "left_to_arrive": _wave_left_to_arrive_display(planned),
        "unloaded": unloaded,
        "left": left,
        "left_to_unload": left,
        "status": _status(row.status),
    }


def _wave_left_to_unload(waves, wave_name):
    for wave in waves:
        if wave.get("name") == wave_name:
            return wave.get("left_to_unload", wave.get("left", 0))
    return 0


def _google_mirror_left_to_unload_value(value):
    """Mirror the already-calculated LTU display value; DOWN is numeric zero."""
    return 0 if str(value or "").strip().upper() == "DOWN" else value


def _google_mirror_left_to_unload_values(waves):
    return {
        "E2": _google_mirror_left_to_unload_value(
            _wave_left_to_unload(waves, "1ST WAVE")
        ),
        "E3": _google_mirror_left_to_unload_value(
            _wave_left_to_unload(waves, "2ND WAVE")
        ),
    }


def _wave_views(
    waves,
    sides,
    operational_settings,
    now=None,
    timer_rows=None,
    persist_timer=True,
    change_tracker=None,
):
    rows_by_name = {row.wave_name: row for row in waves}
    first_row = rows_by_name["1ST WAVE"]
    second_row = rows_by_name["2ND WAVE"]
    timer_rows_by_name = {
        row.wave_name: row for row in (timer_rows or waves)
    }
    east = sides["east"]
    west = sides["west"]

    east_open_bays = max(east["open_bays"], 0)
    west_open_bays = max(west["open_bays"], 0)

    first_left_to_arrive = max(first_row.planned_count or 0, 0)
    second_left_to_arrive = max(second_row.planned_count or 0, 0)

    first_east_wave_count = _side_wave_count(east, "first")
    first_west_wave_count = _side_wave_count(west, "first")
    second_east_wave_count = _side_wave_count(east, "second")
    second_west_wave_count = _side_wave_count(west, "second")

    first_east_waiting = _side_wave_waiting(first_east_wave_count, east_open_bays)
    first_west_waiting = _side_wave_waiting(first_west_wave_count, west_open_bays)
    first_back_row_waiting = first_east_waiting + first_west_waiting
    first_is_all_up = _wave_is_all_up(
        first_left_to_arrive,
        first_back_row_waiting,
    )
    first_timer_done = _sync_wave_all_up_timer(
        timer_rows_by_name["1ST WAVE"],
        first_is_all_up,
        operational_settings,
        now,
        persist=persist_timer,
        change_tracker=change_tracker,
    )
    if first_is_all_up and first_timer_done:
        first_left_to_unload = "DOWN"
    elif first_is_all_up:
        first_left_to_unload = "ALL UP"
    else:
        first_left_to_unload = _active_wave_left_to_unload(
            first_left_to_arrive,
            first_east_waiting,
            first_west_waiting,
            east_open_bays,
            west_open_bays,
            _settings_first_modifier(operational_settings),
        )

    second_waiting_on_first_wave = first_is_all_up and not first_timer_done
    second_is_active = first_is_all_up and first_timer_done
    second_east_waiting = _side_wave_waiting(second_east_wave_count, east_open_bays)
    second_west_waiting = _side_wave_waiting(second_west_wave_count, west_open_bays)
    second_back_row_waiting = second_east_waiting + second_west_waiting
    second_inactive_remaining = _inactive_wave_left_to_unload(
        second_left_to_arrive,
        second_east_wave_count,
        second_west_wave_count,
    )
    second_is_all_up = _wave_is_all_up(
        second_left_to_arrive,
        second_back_row_waiting,
    )
    second_timer_done = _sync_wave_all_up_timer(
        timer_rows_by_name["2ND WAVE"],
        second_is_active and second_is_all_up,
        operational_settings,
        now,
        persist=persist_timer,
        change_tracker=change_tracker,
    )

    if second_is_active and second_is_all_up and second_timer_done:
        second_left_to_unload = "DOWN"
    elif second_is_active and second_is_all_up:
        second_left_to_unload = "ALL UP"
    elif second_waiting_on_first_wave:
        second_left_to_unload = "PENDING"
    elif not second_is_active:
        second_left_to_unload = second_inactive_remaining
    else:
        second_left_to_unload = _active_wave_left_to_unload(
            second_left_to_arrive,
            second_east_waiting,
            second_west_waiting,
            east_open_bays,
            west_open_bays,
            _settings_second_modifier(operational_settings),
        )

    return [
        _wave_view(first_row, first_left_to_unload),
        _wave_view(second_row, second_left_to_unload),
    ]


def _wave_left_to_arrive_display(value):
    return "ALL IN" if max(value or 0, 0) == 0 else max(value or 0, 0)


def _wave_back_row_waiting(east_wave, west_wave, east_open_bays, west_open_bays):
    """Count back-row work after each ballmat uses only its own open bays."""
    east_waiting = _side_wave_waiting(east_wave, east_open_bays)
    west_waiting = _side_wave_waiting(west_wave, west_open_bays)
    return east_waiting + west_waiting


def _wave_is_all_up(left_to_arrive, back_row_waiting):
    return max(left_to_arrive or 0, 0) == 0 and back_row_waiting == 0


def _side_wave_waiting(back_row_count, open_bays):
    return max(max(back_row_count or 0, 0) - max(open_bays or 0, 0), 0)


def _active_wave_left_to_unload(
    left_to_arrive,
    east_waiting,
    west_waiting,
    east_open_bays,
    west_open_bays,
    modifier,
):
    """Calculate active-wave LTU with the opposite-ballmat opening adjustment.

    A side with no waiting back-row work has enough of its own openings to
    absorb its queue. Its available openings reduce the active wave's modifier
    only when the other side is still waiting; manual back-row counts remain
    untouched throughout.
    """
    base_modifier = max(modifier or 0, 0)
    if east_waiting > 0 and west_waiting > 0:
        adjusted_modifier = base_modifier
    elif east_waiting > 0:
        adjusted_modifier = max(base_modifier - max(west_open_bays or 0, 0), 0)
    elif west_waiting > 0:
        adjusted_modifier = max(base_modifier - max(east_open_bays or 0, 0), 0)
    else:
        adjusted_modifier = 0

    return (
        max(left_to_arrive or 0, 0)
        + max(east_waiting or 0, 0)
        + max(west_waiting or 0, 0)
        + adjusted_modifier
    )


def _inactive_wave_left_to_unload(left_to_arrive, east_wave, west_wave):
    """Show the raw workload for a wave that cannot yet use its modifier."""
    return (
        max(left_to_arrive or 0, 0)
        + max(east_wave or 0, 0)
        + max(west_wave or 0, 0)
    )


def _sync_wave_all_up_timer(
    row,
    is_timer_active,
    operational_settings=None,
    now=None,
    *,
    persist=True,
    change_tracker=None,
):
    if now is None:
        now = datetime.utcnow()
    elif now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)

    if is_timer_active:
        if row.all_up_started_at is None:
            if persist:
                row.all_up_started_at = now
                _mark_persistent_state_changed(change_tracker)
            return False
        started_at = row.all_up_started_at
        if started_at.tzinfo is not None:
            started_at = started_at.astimezone(timezone.utc).replace(tzinfo=None)
        delay = timedelta(minutes=_settings_down_timer_minutes(operational_settings))
        return now - started_at >= delay

    if persist and row.all_up_started_at is not None:
        row.all_up_started_at = None
        _mark_persistent_state_changed(change_tracker)
    return False


def _ballmat_view(row):
    return {
        "side": row.side,
        "count": max(row.count or 0, 0),
        "status": _status(row.status),
    }


def _open_bay_view(row):
    return {
        "side": row.side,
        "open_count": max(row.open_count or 0, 0),
    }


def _bay_status_view(row):
    return {
        "side": row.side,
        "bay_name": row.bay_name,
        "status": _status(row.status),
    }


def _driver_route_view(row):
    return {
        "route_name": row.route_name,
        "route_value": row.route_value or "-",
    }


def _operational_settings_view(settings):
    return {
        "first_modifier": _settings_first_modifier(settings),
        "second_modifier": _settings_second_modifier(settings),
        "down_timer_minutes": _settings_down_timer_minutes(settings),
    }


def _settings_first_modifier(settings):
    return _clean_count(
        getattr(settings, "first_wave_unload_modifier", None),
        default=DEFAULT_FIRST_WAVE_UNLOAD_MODIFIER,
        maximum=UNLOAD_MODIFIER_MAX,
    )


def _settings_second_modifier(settings):
    return _clean_count(
        getattr(settings, "second_wave_unload_modifier", None),
        default=DEFAULT_SECOND_WAVE_UNLOAD_MODIFIER,
        maximum=UNLOAD_MODIFIER_MAX,
    )


def _settings_down_timer_minutes(settings):
    return _clean_count(
        getattr(settings, "all_up_to_down_minutes", None),
        default=DEFAULT_ALL_UP_TO_DOWN_MINUTES,
        minimum=1,
        maximum=ALL_UP_TO_DOWN_MINUTES_MAX,
    )


def _driver_route_default_value(route_name):
    if route_name == DRIVER_ROUTE_WEST_OFFSET_NAME:
        return "0"
    if route_name in (
        DRIVER_ROUTE_FIRST_WAVE_OVERRIDE_NAME,
        DRIVER_ROUTE_SECOND_WAVE_OVERRIDE_NAME,
    ):
        return "auto"
    if route_name in DRIVER_ROUTE_BAY_PRIORITY_ENABLED_NAMES:
        return "true"
    return ""


def _driver_route_by_name(driver_routes, route_name):
    return next(row for row in driver_routes if row.route_name == route_name)


def _driver_routing_calculation(gateway, sort_state, sides, waves, driver_routes):
    east = sides["east"]
    west = sides["west"]
    west_offset = _driver_route_offset(driver_routes)
    east_open_bays = max(east["open_bays"], 0)
    west_open_bays = max(west["open_bays"], 0)
    first_route = _driver_wave_route_with_override(
        _side_wave_count(east, "first"),
        _side_wave_count(west, "first"),
        east_open_bays,
        west_open_bays,
        west_offset,
        _driver_route_override(driver_routes, DRIVER_ROUTE_FIRST_WAVE_OVERRIDE_NAME),
    )
    second_route = _driver_wave_route_with_override(
        _side_wave_count(east, "second"),
        _side_wave_count(west, "second"),
        east_open_bays,
        west_open_bays,
        west_offset,
        _driver_route_override(driver_routes, DRIVER_ROUTE_SECOND_WAVE_OVERRIDE_NAME),
    )

    wave_by_name = {wave["name"]: wave for wave in waves}
    first_left_to_arrive = max(
        wave_by_name["1ST WAVE"].get("planned") or 0,
        0,
    )
    second_left_to_arrive = max(
        wave_by_name["2ND WAVE"].get("planned") or 0,
        0,
    )
    first_display_state = "all_in" if first_left_to_arrive == 0 else "route"
    second_display_state = _second_wave_driver_display_state(
        gateway,
        second_left_to_arrive,
    )
    first_route = _with_driver_display_state(first_route, first_display_state, "first")
    second_route = _with_driver_display_state(second_route, second_display_state, "second")

    return {
        "sort_name": sort_state.sort_name.upper(),
        "active_wave": sort_state.active_wave,
        "west_offset": west_offset,
        "routes": {
            "first": {
                "wave_key": "first",
                "wave_label": "1ST WAVE",
                "east_count": _side_wave_count(east, "first"),
                "west_count": _side_wave_count(west, "first"),
                **first_route,
            },
            "second": {
                "wave_key": "second",
                "wave_label": "2ND WAVE",
                "east_count": _side_wave_count(east, "second"),
                "west_count": _side_wave_count(west, "second"),
                **second_route,
            },
        },
        "bay_priority": _driver_bay_priority(sides, driver_routes),
        "bay_priority_enabled_bays": {
            bay_name: _driver_route_bay_priority_enabled(driver_routes, bay_name)
            for _side, bay_name in DEFAULT_BAYS
        },
    }


def _driver_wave_route(east_value, west_value, east_open_bays, west_open_bays, west_offset):
    # Open bays are available capacity, so route to the lower net pressure.
    # Keep the West offset as a routing-only threshold bias; it never changes
    # the persisted/count displays used elsewhere in Sektor.
    east_pressure = max(east_value or 0, 0) - max(east_open_bays or 0, 0)
    west_pressure = max(west_value or 0, 0) - max(west_open_bays or 0, 0)
    effective_west_pressure = west_pressure + max(west_offset or 0, 0)

    if east_pressure <= effective_west_pressure:
        return {
            "target": "East Ballmat Stay Right",
            "direction": "east",
            "arrow": "right",
        }

    return {
        "target": "West Ballmat Stay Left",
        "direction": "west",
        "arrow": "left",
    }


def _driver_wave_route_with_override(
    east_value,
    west_value,
    east_open_bays,
    west_open_bays,
    west_offset,
    override,
):
    calculated = _driver_wave_route(
        east_value,
        west_value,
        east_open_bays,
        west_open_bays,
        west_offset,
    )
    if override == "east":
        route = {
            "target": "East Ballmat Stay Right",
            "direction": "east",
            "arrow": "right",
        }
    elif override == "west":
        route = {
            "target": "West Ballmat Stay Left",
            "direction": "west",
            "arrow": "left",
        }
    else:
        route = calculated
    return {
        **route,
        "calculated_target": calculated["target"],
        "override": override,
        "route_source": "MANUAL OVERRIDE" if override != "auto" else "AUTO",
    }


def _side_wave_count(side, wave_key):
    wave = next((row for row in side["waves"] if row["key"] == wave_key), None)
    return max((wave or {}).get("count") or 0, 0)


def _driver_bay_priority(sides, driver_routes):
    priority = [
        {
            **bay,
            "side": side["label"],
            "rank_label": "",
            "status_rank": STATUS_RANKS[_status(bay["status"])],
        }
        for side in sides.values()
        for bay in side["bays"]
        if _driver_route_bay_priority_enabled(driver_routes, bay["bay_name"])
    ]
    priority.sort(
        key=lambda bay: (bay["status_rank"], _bay_number(bay["bay_name"])),
        reverse=True,
    )
    for index, bay in enumerate(priority, start=1):
        bay["rank"] = index
        bay["rank_label"] = _ordinal(index)
    return priority[:3]


def _driver_route_bay_priority_enabled_name(bay_name):
    return f"{str(bay_name or '').strip().upper()} PRIORITY ENABLED"


def _driver_route_bay_priority_enabled(driver_routes, bay_name):
    row = _driver_route_by_name(
        driver_routes,
        _driver_route_bay_priority_enabled_name(bay_name),
    )
    return str(row.route_value or "true").strip().lower() != "false"


def _second_wave_driver_display_state(gateway, left_to_arrive):
    operation = current_operational_sort_operation(gateway)
    if operation is None or not _current_sort_second_wave_has_arrived(operation):
        return "not_arrived"
    return "all_in" if left_to_arrive == 0 else "route"


def _current_sort_second_wave_has_arrived(operation):
    return (
        SortDateMission.query.filter(
            SortDateMission.sort_date_operation_id == operation.id,
            SortDateMission.mission_type == "arrival",
            SortDateMission.wave.in_(("2", "2nd Wave")),
            SortDateMission.actual_block_in_datetime_utc.isnot(None),
        )
        .limit(1)
        .first()
        is not None
    )


def _with_driver_display_state(route, display_state, wave_key):
    if display_state == "not_arrived":
        message = "2ND WAVE NOT ARRIVED"
    elif display_state == "all_in":
        message = "1ST WAVE ALL IN" if wave_key == "first" else "2ND WAVE ALL IN"
    else:
        message = ""
    return {**route, "display_state": display_state, "display_message": message}


def _bay_number(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return int(digits or 0)


def _ordinal(number):
    if number == 1:
        suffix = "st"
    elif number == 2:
        suffix = "nd"
    elif number == 3:
        suffix = "rd"
    else:
        suffix = "th"
    return f"{number}{suffix}"


def _driver_route_offset(driver_routes):
    offset_row = _driver_route_by_name(driver_routes, DRIVER_ROUTE_WEST_OFFSET_NAME)
    return _clean_offset(offset_row.route_value)


def _driver_route_override(driver_routes, route_name):
    return _clean_driver_route_override(
        _driver_route_by_name(driver_routes, route_name).route_value,
    )


def _clean_driver_route_override(value, *, default="auto"):
    normalized = str(value or default).strip().lower()
    if normalized not in DRIVER_ROUTE_OVERRIDE_VALUES:
        return _clean_driver_route_override(default, default="auto")
    return normalized


def _sync_driver_route_values(
    driver_routes,
    routing,
    *,
    change_tracker=None,
):
    _assign_if_changed(
        _driver_route_by_name(driver_routes, DRIVER_ROUTE_FIRST_WAVE_NAME),
        "route_value",
        routing["routes"]["first"]["calculated_target"],
        change_tracker=change_tracker,
    )
    _assign_if_changed(
        _driver_route_by_name(driver_routes, DRIVER_ROUTE_SECOND_WAVE_NAME),
        "route_value",
        routing["routes"]["second"]["calculated_target"],
        change_tracker=change_tracker,
    )
    _assign_if_changed(
        _driver_route_by_name(driver_routes, DRIVER_ROUTE_WEST_OFFSET_NAME),
        "route_value",
        str(routing["west_offset"]),
        change_tracker=change_tracker,
    )


def _side_state_views(ballmat_wave_counts, ballmats, open_bays, bay_statuses):
    ballmat_by_side = {row.side: row for row in ballmats}
    open_bay_by_side = {row.side: row for row in open_bays}
    wave_counts_by_side = {
        side_label: [
            row
            for row in ballmat_wave_counts
            if row.side == side_label
        ]
        for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES
    }
    bay_statuses_by_side = {
        side_label: [
            row
            for row in bay_statuses
            if row.side == side_label
        ]
        for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES
    }

    sides = {}
    for side_key, side_label, manager_label in DEFAULT_BALLMAT_SIDES:
        sides[side_key] = {
            "key": side_key,
            "label": side_label,
            "manager_label": manager_label,
            "total_count": max(ballmat_by_side[side_label].count or 0, 0),
            "status": _status(ballmat_by_side[side_label].status),
            "open_bays": max(open_bay_by_side[side_label].open_count or 0, 0),
            "waves": [
                _ballmat_wave_view(row)
                for row in sorted(
                    wave_counts_by_side[side_label],
                    key=lambda row: row.display_order,
                )
            ],
            "bays": [
                _bay_status_view(row)
                for row in sorted(
                    bay_statuses_by_side[side_label],
                    key=lambda row: row.display_order,
                )
            ],
        }
    return sides


def _ballmat_wave_view(row):
    return {
        "key": _wave_key(row.wave_name),
        "name": row.wave_name,
        "count": max(row.count or 0, 0),
        "status": _status(row.status),
    }


def _sync_ballmat_rollups(
    sort_state,
    ballmat_wave_counts,
    waves,
    ballmats,
    *,
    change_tracker=None,
):
    wave_rows = {row.wave_name: row for row in waves}
    side_rows = {row.side: row for row in ballmats}
    total_unloaded = 0

    for _wave_key, wave_name in DEFAULT_WAVES:
        matching_rows = [
            row
            for row in ballmat_wave_counts
            if row.wave_name == wave_name
        ]
        wave_total = sum(max(row.count or 0, 0) for row in matching_rows)
        wave_row = wave_rows[wave_name]
        _assign_if_changed(
            wave_row,
            "unloaded_count",
            wave_total,
            change_tracker=change_tracker,
        )
        _assign_if_changed(
            wave_row,
            "status",
            _aggregate_status(matching_rows, wave_total),
            change_tracker=change_tracker,
        )

    for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES:
        matching_rows = [
            row
            for row in ballmat_wave_counts
            if row.side == side_label
        ]
        side_total = sum(max(row.count or 0, 0) for row in matching_rows)
        side_row = side_rows[side_label]
        _assign_if_changed(
            side_row,
            "count",
            side_total,
            change_tracker=change_tracker,
        )
        _assign_if_changed(
            side_row,
            "status",
            _aggregate_status(matching_rows, side_total),
            change_tracker=change_tracker,
        )
        total_unloaded += side_total

    _assign_if_changed(
        sort_state,
        "unloaded_total",
        total_unloaded,
        change_tracker=change_tracker,
    )


def _aggregate_status(rows, total_count):
    statuses = [_status(row.status) for row in rows]
    if not statuses:
        return "Empty"

    strongest = max(statuses, key=lambda status: STATUS_RANKS[status])
    if total_count > 0 and strongest == "Empty":
        return "Light"
    return strongest


def _wave_key(wave_name):
    normalized = str(wave_name or "").strip().upper()
    for wave_key, configured_name in DEFAULT_WAVES:
        if normalized == configured_name:
            return wave_key
    return normalized.lower().replace(" ", "_")


def _clean_count(value, default=0, minimum=0, maximum=9999):
    try:
        cleaned = int(value)
    except (TypeError, ValueError):
        cleaned = default or 0
    return min(max(cleaned, minimum), maximum)


def _clean_delta(value):
    try:
        cleaned = int(value)
    except (TypeError, ValueError):
        cleaned = 0
    return min(max(cleaned, -1000), 1000)


def _clean_offset(value):
    try:
        cleaned = int(value)
    except (TypeError, ValueError):
        cleaned = 0
    return min(max(cleaned, 0), DRIVER_OFFSET_MAX)


def _standalone_compat_count(value, maximum):
    if value is None or isinstance(value, bool):
        return None

    raw_value = str(value).strip()
    if not raw_value:
        return None

    try:
        numeric = Decimal(raw_value)
    except (InvalidOperation, ValueError):
        return None
    if not numeric.is_finite() or numeric != numeric.to_integral_value():
        return None

    parsed = int(numeric)
    if parsed < 0 or parsed > maximum:
        return None
    return parsed


def _standalone_compat_status(value):
    normalized = str(value or "").strip().title()
    return normalized if normalized in STATUS_LABELS else None


def _assign_if_changed(
    row,
    attribute,
    value,
    *,
    change_tracker=None,
):
    if getattr(row, attribute) == value:
        return 0
    setattr(row, attribute, value)
    _mark_persistent_state_changed(change_tracker)
    return 1


def _mark_persistent_state_changed(change_tracker):
    if change_tracker is not None:
        change_tracker.mark_changed()


def _status(value):
    value = str(value or "").strip().title()
    return value if value in STATUS_LABELS else "Empty"
