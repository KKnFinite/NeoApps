import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, literal, or_, select, union_all

from app.extensions import db
from app.models import (
    MasterFlightSchedule,
    NeoErmacBuildingLineup,
    NeoErmacDoorPull,
    NeoErmacDoorSupervision,
    NeoErmacUldRequest,
    NeoSektorUldOnTheWayEvent,
    SortDateGoogleMissionLink,
    SortDateMission,
    SortDateParkingAssignment,
)
from app.services.neoermac_building_lineup import (
    get_building_lineup_assignments,
    get_building_lineup_destinations_for_door,
    get_building_lineup_doors_by_destination,
    get_linked_building_lineup_doors,
    get_outbound_door_options,
    load_building_lineup_rows,
    normalize_destination,
)
from app.services.gateway_matrix import (
    current_gateway_local_datetime,
    gateway_timezone,
    operation_is_active_at,
    sort_lookup_window_for_operation,
)
from app.services.neoermac_live_refresh import neoermac_live_refresh_status
from app.services.operation_scope import current_operational_sort_operation
from app.services.neoermac_tail_presence import (
    arrival_presence_by_tail,
    departure_tail_presence,
    normalize_tail_number,
    tail_presence_status_override,
)
from app.services.neoermac_pull_aggregation import (
    recompute_current_sort_door_pull_aggregates,
)
from app.services.sort_date_operations import mission_display_timing_data
from app.services.uld_requests import (
    ULD_TYPES,
    active_on_the_way_event_views,
    active_uld_requests_for_door,
    aggregate_uld_request_for_door,
    delete_uld_request,
    door_uld_state_payload,
    edit_uld_request,
    uld_workspace_state_payload,
    update_uld_request_from_form,
)


PULL_DUE_WARNING_MINUTES = 5
_OPERATION_UNSET = object()
_DOOR_PULL_LOOKUP_UNSET = object()
_BUNDLE_UNSET = object()

PULL_FIELDS = (
    {
        "key": "pure",
        "label": "Pure",
        "planned_attr": "pure_pull_time_local",
        "actual_attr": "actual_pure_pull_time_local",
        "no_attr": "no_pure_pull",
        "actual_field": "actual_pure",
        "no_field": "no_pure",
        "short_label": "PURE",
    },
    {
        "key": "mix",
        "label": "Mix Pull",
        "planned_attr": "mix_pull_time_local",
        "actual_attr": "actual_mix_pull_time_local",
        "no_attr": "no_mix_pull",
        "actual_field": "actual_mix",
        "no_field": "no_mix",
        "short_label": "MIX",
    },
)


@dataclass
class DoorViewOperationalStateBundle:
    """One coherent, request-local snapshot used by Door View builders."""

    gateway: object
    operation: object
    lineup_assignments: tuple
    missions: tuple
    door_pulls: list
    initialization_changed: bool = False
    destinations_by_door: dict = field(init=False)
    doors_by_destination: dict = field(init=False)
    departure_missions_by_destination: dict = field(init=False)
    arrival_missions: tuple = field(init=False)
    door_pulls_by_door_destination: dict = field(init=False)
    door_pulls_by_destination_and_door: dict = field(init=False)
    _masters_by_destination: object = field(default=_BUNDLE_UNSET, init=False)
    _parking_by_tail: object = field(default=_BUNDLE_UNSET, init=False)
    _arrivals_by_tail: object = field(default=_BUNDLE_UNSET, init=False)
    _uld_requests: object = field(default=_BUNDLE_UNSET, init=False)
    _uld_events: object = field(default=_BUNDLE_UNSET, init=False)

    def __post_init__(self):
        door_options = get_outbound_door_options()
        self.destinations_by_door = {
            door: dict(
                sorted(
                    get_building_lineup_destinations_for_door(
                        self.gateway,
                        door,
                        assignments=self.lineup_assignments,
                    ).items()
                )
            )
            for door in door_options
        }
        self.doors_by_destination = get_building_lineup_doors_by_destination(
            self.gateway,
            assignments=self.lineup_assignments,
        )
        self.departure_missions_by_destination = {}
        arrivals = []
        for mission in self.missions:
            if mission.mission_type == "arrival":
                arrivals.append(mission)
                continue
            if mission.mission_type != "departure":
                continue
            destination = normalize_destination(mission.destination)
            if (
                destination
                and destination not in self.departure_missions_by_destination
            ):
                self.departure_missions_by_destination[destination] = mission
        self.arrival_missions = tuple(arrivals)
        self.door_pulls_by_door_destination = {}
        self.door_pulls_by_destination_and_door = {}
        for record in self.door_pulls:
            self.register_door_pull(record)

    @classmethod
    def load(cls, gateway, operation, *, initialize_lineup):
        lineup_load = load_building_lineup_rows(
            gateway,
            initialize=initialize_lineup,
        )
        assignments = get_building_lineup_assignments(
            gateway,
            initialize=initialize_lineup,
            rows=lineup_load.rows,
        )
        missions = ()
        door_pulls = []
        if operation:
            missions = tuple(
                SortDateMission.query.filter_by(
                    sort_date_operation_id=operation.id,
                )
                .order_by(
                    SortDateMission.planned_datetime_utc.asc(),
                    SortDateMission.id.asc(),
                )
                .all()
            )
            door_pulls = (
                NeoErmacDoorPull.query.filter_by(
                    gateway_id=gateway.id,
                    sort_date_operation_id=operation.id,
                )
                .order_by(
                    NeoErmacDoorPull.updated_at.desc(),
                    NeoErmacDoorPull.id.desc(),
                )
                .all()
            )
        return cls(
            gateway=gateway,
            operation=operation,
            lineup_assignments=tuple(assignments),
            missions=tuple(missions),
            door_pulls=list(door_pulls),
            initialization_changed=lineup_load.persistent_state_changed,
        )

    def register_door_pull(self, record):
        door = normalize_door(record.door)
        destination = normalize_destination(record.destination)
        if not door or not destination:
            return
        self.door_pulls_by_door_destination.setdefault(
            (door, destination),
            record,
        )
        self.door_pulls_by_destination_and_door.setdefault(
            (destination, door),
            record,
        )
        if record not in self.door_pulls:
            self.door_pulls.append(record)

    @property
    def masters_by_destination(self):
        if self._masters_by_destination is _BUNDLE_UNSET:
            self._masters_by_destination = _master_departures_by_destination(
                self.gateway
            )
        return self._masters_by_destination

    @property
    def parking_by_tail(self):
        if self._parking_by_tail is _BUNDLE_UNSET:
            self._parking_by_tail = _parking_assignments_by_tail(self.operation)
        return self._parking_by_tail

    @property
    def arrivals_by_tail(self):
        if self._arrivals_by_tail is _BUNDLE_UNSET:
            google_links = []
            if self.operation:
                google_links = SortDateGoogleMissionLink.query.filter_by(
                    sort_date_operation_id=self.operation.id,
                    mission_type="arrival",
                ).all()
            self._arrivals_by_tail = arrival_presence_by_tail(
                self.operation,
                arrivals=self.arrival_missions,
                google_links=google_links,
            )
        return self._arrivals_by_tail

    @property
    def uld_requests(self):
        if self._uld_requests is _BUNDLE_UNSET:
            query = NeoErmacUldRequest.query.filter_by(gateway_id=self.gateway.id)
            if self.operation:
                query = query.filter_by(sort_date_operation_id=self.operation.id)
            else:
                query = query.filter(
                    NeoErmacUldRequest.sort_date_operation_id.is_(None)
                )
            self._uld_requests = query.all()
        return self._uld_requests

    @property
    def uld_events(self):
        if self._uld_events is _BUNDLE_UNSET:
            query = NeoSektorUldOnTheWayEvent.query.filter(
                NeoSektorUldOnTheWayEvent.gateway_id == self.gateway.id,
                NeoSektorUldOnTheWayEvent.expires_at_utc
                > datetime.now(timezone.utc).replace(tzinfo=None),
            )
            if self.operation:
                query = query.filter(
                    NeoSektorUldOnTheWayEvent.sort_date_operation_id
                    == self.operation.id
                )
            else:
                query = query.filter(
                    NeoSektorUldOnTheWayEvent.sort_date_operation_id.is_(None)
                )
            self._uld_events = query.order_by(
                NeoSektorUldOnTheWayEvent.sent_at_utc.asc(),
                NeoSektorUldOnTheWayEvent.id.asc(),
            ).all()
        return self._uld_events


def door_view_operational_state(
    gateway,
    *,
    operation=_OPERATION_UNSET,
    initialize_lineup=True,
):
    if operation is _OPERATION_UNSET:
        operation = _current_operation(gateway)
    return DoorViewOperationalStateBundle.load(
        gateway,
        operation,
        initialize_lineup=initialize_lineup,
    )


def door_view_context(gateway, selected_door=None, *, bundle=None):
    selected_door = normalize_door(selected_door)
    door_options = get_door_options(gateway)
    if selected_door not in door_options:
        selected_door = None

    operation = (
        bundle.operation if bundle is not None else _current_operation(gateway)
    )
    destinations = []
    uld_request = None
    uld_requests = []
    if selected_door:
        destinations = _destination_cards_for_door(
            gateway,
            selected_door,
            operation,
            bundle=bundle,
        )
        if bundle is None:
            uld_request = _uld_request_for_door(gateway, selected_door, operation)
            uld_requests = active_uld_requests_for_door(
                gateway,
                selected_door,
                operation,
            )

    return {
        "door_options": door_options,
        "selected_door": selected_door,
        "destinations": destinations,
        "pull_fields": PULL_FIELDS,
        "uld_types": ULD_TYPES,
        "uld_request": uld_request,
        "uld_requests": uld_requests,
        "operation": operation,
        "refresh_status": neoermac_refresh_status(gateway, operation=operation),
        "tugs": [],
        "on_the_way_events": (
            active_on_the_way_event_views(gateway, selected_door, operation=operation)
            if selected_door and bundle is None
            else []
        ),
    }


def save_door_pulls(
    gateway,
    selected_door,
    form_data,
    supervised_doors=(),
    *,
    apply_to_both=False,
):
    selected_door = normalize_door(selected_door)
    if not selected_door:
        raise ValueError("Select a door.")

    operation = _current_operation(gateway)
    bundle = door_view_operational_state(
        gateway,
        operation=operation,
        initialize_lineup=True,
    )
    allowed_destinations = set(bundle.destinations_by_door.get(selected_door, {}))

    row_count = _int_value(form_data.get("destination_count"), default=0)
    changed_destinations = set()
    for index in range(row_count):
        destination = normalize_destination(form_data.get(f"destination_{index}"))
        if not destination:
            continue
        if destination not in allowed_destinations:
            raise ValueError(f"{destination} is not assigned to {selected_door}.")

        for field in PULL_FIELDS:
            no_pull = form_data.get(f"{field['no_field']}_{index}") == "on"
            actual_value = (
                None
                if no_pull
                else _parse_optional_time(
                    form_data.get(f"{field['actual_field']}_{index}")
                )
            )
            _apply_pull_value(
                gateway,
                operation,
                selected_door,
                destination,
                field,
                actual_value,
                no_pull,
                supervised_doors,
                apply_to_both=apply_to_both,
                bundle=bundle,
            )
        changed_destinations.add(destination)

    db.session.flush()
    recompute_current_sort_door_pull_aggregates(
        gateway,
        operation=operation,
        destinations=changed_destinations,
        doors_by_destination=bundle.doors_by_destination,
        missions_by_destination=bundle.departure_missions_by_destination,
        pulls_by_destination_and_door=bundle.door_pulls_by_destination_and_door,
    )
    db.session.flush()


def save_single_door_pull(
    gateway,
    selected_door,
    destination,
    pull_key,
    actual_value,
    no_pull,
    supervised_doors=(),
    apply_to_both=False,
    *,
    operation=_OPERATION_UNSET,
    bundle=None,
):
    selected_door = normalize_door(selected_door)
    if not selected_door:
        raise ValueError("Select a door.")
    if selected_door not in get_door_options(gateway):
        raise ValueError(f"{selected_door} is not available.")

    destination = normalize_destination(destination)
    if not destination:
        raise ValueError("Select a destination.")

    field = _pull_field_by_key(pull_key)
    if not field:
        raise ValueError("Select a valid pull type.")

    if operation is _OPERATION_UNSET:
        operation = (
            bundle.operation if bundle is not None else _current_operation(gateway)
        )
    if bundle is None:
        bundle = door_view_operational_state(
            gateway,
            operation=operation,
            initialize_lineup=True,
        )
    allowed_destinations = set(bundle.destinations_by_door.get(selected_door, {}))
    if destination not in allowed_destinations:
        raise ValueError(f"{destination} is not assigned to {selected_door}.")

    no_pull = bool(no_pull)
    parsed_actual = None if no_pull else _parse_optional_time(actual_value)
    _apply_pull_value(
        gateway,
        operation,
        selected_door,
        destination,
        field,
        parsed_actual,
        no_pull,
        supervised_doors,
        apply_to_both=apply_to_both,
        bundle=bundle,
    )
    db.session.flush()
    recompute_current_sort_door_pull_aggregates(
        gateway,
        operation=operation,
        destinations=(destination,),
        doors_by_destination=bundle.doors_by_destination,
        missions_by_destination=bundle.departure_missions_by_destination,
        pulls_by_destination_and_door=bundle.door_pulls_by_destination_and_door,
    )
    db.session.flush()
    return _pull_card_payload(
        gateway,
        selected_door,
        destination,
        operation,
        bundle=bundle,
    )


def _apply_pull_value(
    gateway,
    operation,
    selected_door,
    destination,
    field,
    actual_value,
    no_pull,
    supervised_doors,
    *,
    apply_to_both=False,
    bundle=None,
):
    for target_door in _pull_write_doors(
        gateway,
        selected_door,
        destination,
        supervised_doors,
        apply_to_both=apply_to_both,
        bundle=bundle,
    ):
        record = _door_pull_record(
            gateway,
            target_door,
            destination,
            operation,
            create=True,
            bundle=bundle,
        )
        setattr(record, field["no_attr"], bool(no_pull))
        setattr(record, field["actual_attr"], None if no_pull else actual_value)


def _pull_write_doors(
    gateway,
    selected_door,
    destination,
    supervised_doors,
    *,
    apply_to_both=False,
    bundle=None,
):
    if not apply_to_both:
        return (selected_door,)

    return (selected_door,) + linked_supervised_pull_doors(
        gateway,
        selected_door,
        destination,
        supervised_doors,
        bundle=bundle,
    )


def linked_supervised_pull_doors(
    gateway,
    selected_door,
    destination,
    supervised_doors=(),
    *,
    bundle=None,
):
    """Return valid opposite supervised doors for one displayed destination."""
    selected_door = normalize_door(selected_door)
    supervised = {
        normalize_door(door)
        for door in (supervised_doors or ())
        if normalize_door(door)
    }
    if selected_door not in supervised:
        return ()

    linked = set(
        get_linked_building_lineup_doors(
            gateway,
            selected_door,
            destination,
            assignments=(
                bundle.lineup_assignments if bundle is not None else None
            ),
        )
    )
    return tuple(
        door
        for door in get_outbound_door_options()
        if door in linked and door in supervised
    )


def save_uld_request(
    gateway,
    selected_door,
    form_data,
    requested_by_user_id=None,
):
    selected_door = normalize_door(selected_door)
    if not selected_door:
        raise ValueError("Select a door.")
    if selected_door not in get_door_options(gateway):
        raise ValueError(f"{selected_door} is not available.")

    return update_uld_request_from_form(
        gateway,
        selected_door,
        form_data,
        operation=_current_operation(gateway),
        requested_by_user_id=requested_by_user_id,
    )


def edit_door_uld_request(gateway, selected_door, form_data):
    selected_door = normalize_door(selected_door)
    if not selected_door:
        raise ValueError("Select a door.")
    if selected_door not in get_door_options(gateway):
        raise ValueError(f"{selected_door} is not available.")

    counts = {
        "A2": form_data.get("uld_a2_count"),
        "A1": form_data.get("uld_a1_count"),
        "AMP": form_data.get("uld_amp_count"),
    }
    return edit_uld_request(
        gateway,
        selected_door,
        form_data.get("request_id"),
        counts,
        operation=_current_operation(gateway),
    )


def delete_door_uld_request(gateway, selected_door, form_data):
    selected_door = normalize_door(selected_door)
    if not selected_door:
        raise ValueError("Select a door.")
    if selected_door not in get_door_options(gateway):
        raise ValueError(f"{selected_door} is not available.")

    return delete_uld_request(
        gateway,
        selected_door,
        form_data.get("request_id"),
        operation=_current_operation(gateway),
    )


def door_view_uld_state(
    gateway,
    selected_door,
    supervised_doors=(),
    requested_by_user_id=None,
    operation=_OPERATION_UNSET,
    refresh_status=None,
    revision=None,
    initialize_lineup=True,
    bundle=None,
):
    selected_door = normalize_door(selected_door)
    if not selected_door:
        raise ValueError("Select a door.")
    if selected_door not in get_door_options(gateway):
        raise ValueError(f"{selected_door} is not available.")

    if operation is _OPERATION_UNSET:
        operation = (
            bundle.operation if bundle is not None else _current_operation(gateway)
        )
    if bundle is None:
        bundle = door_view_operational_state(
            gateway,
            operation=operation,
            initialize_lineup=initialize_lineup,
        )
    destinations = _destination_cards_for_door(
        gateway,
        selected_door,
        operation,
        door_pulls=bundle.door_pulls_by_door_destination,
        initialize_lineup=initialize_lineup,
        bundle=bundle,
    )
    state = door_uld_state_payload(
        gateway,
        selected_door,
        operation=operation,
        request_records=bundle.uld_requests,
        event_records=bundle.uld_events,
    )
    workspace = uld_workspace_state_payload(
        gateway,
        supervised_doors,
        requested_by_user_id,
        operation=operation,
        request_records=bundle.uld_requests,
        event_records=bundle.uld_events,
    )
    state["uld_workspace"] = workspace
    state["requests"] = workspace["requests"]
    state["on_the_way_events"] = workspace["on_the_way_events"]
    state["refresh"] = refresh_status or neoermac_refresh_status(gateway)
    if revision is not None:
        state["revision"] = revision
    state["destinations"] = [
        _door_card_state_payload(card, order_index=index)
        for index, card in enumerate(destinations)
    ]
    state["door_tab_alerts"] = door_tab_pull_alerts(
        gateway,
        selected_door,
        supervised_doors,
        operation=operation,
        door_pulls=bundle.door_pulls_by_door_destination,
        initialize_lineup=initialize_lineup,
        bundle=bundle,
    )
    return state


def door_view_uld_workspace(
    gateway,
    supervised_doors,
    requested_by_user_id,
    operation=None,
    *,
    bundle=None,
):
    preloaded = {}
    if bundle is not None:
        preloaded = {
            "request_records": bundle.uld_requests,
            "event_records": bundle.uld_events,
        }
    return uld_workspace_state_payload(
        gateway,
        supervised_doors,
        requested_by_user_id,
        operation=operation or _current_operation(gateway),
        **preloaded,
    )


def door_tab_pull_alerts(
    gateway,
    active_door,
    supervised_doors,
    operation=None,
    door_pulls=_DOOR_PULL_LOOKUP_UNSET,
    initialize_lineup=True,
    bundle=None,
):
    """Summarize unresolved pull urgency for supervised Door View tabs."""
    available_doors = set(get_door_options(gateway))
    active_door = normalize_door(active_door)
    doors = []
    for value in supervised_doors or ():
        door = normalize_door(value)
        if door in available_doors and door not in doors:
            doors.append(door)

    result = {door: _empty_door_tab_alert() for door in doors}
    operation = operation or (
        bundle.operation if bundle is not None else _current_operation(gateway)
    )
    if not operation or not doors:
        return result

    if bundle is None:
        bundle = door_view_operational_state(
            gateway,
            operation=operation,
            initialize_lineup=initialize_lineup,
        )
    destinations_by_door = {
        door: set(bundle.destinations_by_door.get(door, {})) for door in doors
    }
    missions = bundle.departure_missions_by_destination
    masters = bundle.masters_by_destination
    if door_pulls is _DOOR_PULL_LOOKUP_UNSET:
        door_pulls = bundle.door_pulls_by_door_destination

    for door in doors:
        if door == active_door:
            continue
        tab_state = ""
        tab_pulls = []
        for destination in destinations_by_door[door]:
            mission = missions.get(destination)
            master = masters.get(destination)
            timing_data = _mission_timing_data(mission, operation)
            planned_times = {
                "pure": _planned_pull_time(timing_data, master, "pure"),
                "mix": _planned_pull_time(timing_data, master, "mix"),
            }
            door_pull = door_pulls.get((door, destination))
            actual = {
                "pure": _time_value(
                    getattr(door_pull, "actual_pure_pull_time_local", None)
                ),
                "mix": _time_value(
                    getattr(door_pull, "actual_mix_pull_time_local", None)
                ),
            }
            no_pull = {
                "pure": bool(getattr(door_pull, "no_pure_pull", False)),
                "mix": bool(getattr(door_pull, "no_mix_pull", False)),
            }
            pull_alerts = _pull_alerts_for_card(
                gateway,
                door,
                destination,
                operation,
                planned_times,
                actual,
                no_pull,
            )
            tab_pulls.extend(
                alert
                for alert in pull_alerts.values()
                if alert.get("due_now_epoch_ms") is not None
            )
            states = {alert["state"] for alert in pull_alerts.values()}
            if "late" in states:
                tab_state = "late"
                break
            if "due_now" in states:
                tab_state = "due_now"

        result[door] = _door_tab_alert(tab_state, pulls=tab_pulls)

    return result


def neoermac_refresh_status(gateway, operation=None, now=None):
    return neoermac_live_refresh_status(gateway)


def current_door_view_operation(gateway):
    """Resolve the operation used by Door View without constructing page state."""
    return _current_operation(gateway)


def door_view_poll_revision(
    gateway,
    selected_door,
    requested_by_user_id,
    *,
    operation=_OPERATION_UNSET,
    now=None,
):
    """Return a database-light fingerprint for every visible Door View input."""
    selected_door = normalize_door(selected_door)
    if not selected_door or selected_door not in get_door_options(gateway):
        raise ValueError("Select a door.")
    if operation is _OPERATION_UNSET:
        operation = _current_operation(gateway)

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    operation_id = operation.id if operation else None
    operation_criteria = lambda model: (
        model.sort_date_operation_id == operation_id
        if operation_id is not None
        else model.sort_date_operation_id.is_(None)
    )
    aggregate_queries = (
        _door_revision_aggregate(
            "lineup",
            NeoErmacBuildingLineup,
            NeoErmacBuildingLineup.updated_at,
            NeoErmacBuildingLineup.gateway_id == gateway.id,
        ),
        _door_revision_aggregate(
            "missions",
            SortDateMission,
            SortDateMission.updated_at,
            operation_criteria(SortDateMission),
        ),
        _door_revision_aggregate(
            "google_mission_links",
            SortDateGoogleMissionLink,
            SortDateGoogleMissionLink.updated_at,
            operation_criteria(SortDateGoogleMissionLink),
        ),
        _door_revision_aggregate(
            "parking",
            SortDateParkingAssignment,
            SortDateParkingAssignment.updated_at,
            operation_criteria(SortDateParkingAssignment),
        ),
        _door_revision_aggregate(
            "door_pulls",
            NeoErmacDoorPull,
            NeoErmacDoorPull.updated_at,
            NeoErmacDoorPull.gateway_id == gateway.id,
            operation_criteria(NeoErmacDoorPull),
        ),
        _door_revision_aggregate(
            "supervision",
            NeoErmacDoorSupervision,
            NeoErmacDoorSupervision.updated_at,
            NeoErmacDoorSupervision.user_id == requested_by_user_id,
            operation_criteria(NeoErmacDoorSupervision),
        ),
        _door_revision_aggregate(
            "uld_requests",
            NeoErmacUldRequest,
            NeoErmacUldRequest.updated_at,
            NeoErmacUldRequest.gateway_id == gateway.id,
            operation_criteria(NeoErmacUldRequest),
        ),
        _door_revision_aggregate(
            "active_uld_events",
            NeoSektorUldOnTheWayEvent,
            NeoSektorUldOnTheWayEvent.created_at,
            NeoSektorUldOnTheWayEvent.gateway_id == gateway.id,
            operation_criteria(NeoSektorUldOnTheWayEvent),
            NeoSektorUldOnTheWayEvent.expires_at_utc > now_utc,
        ),
        _door_revision_aggregate(
            "master_departures",
            MasterFlightSchedule,
            MasterFlightSchedule.updated_at,
            MasterFlightSchedule.gateway_id == gateway.id,
            MasterFlightSchedule.mission_type == "departure",
            MasterFlightSchedule.active.is_(True),
        ),
    )
    rows = sorted(
        db.session.execute(union_all(*aggregate_queries)).all(),
        key=lambda row: row.source,
    )
    payload = {
        "gateway_id": gateway.id,
        "operation_id": operation_id,
        "operation_updated_at": _revision_value(
            getattr(operation, "updated_at", None)
        ),
        "selected_door": selected_door,
        "requested_by_user_id": requested_by_user_id,
        "inputs": [
            {
                "source": row.source,
                "row_count": int(row.row_count or 0),
                "max_id": int(row.max_id or 0),
                "id_sum": int(row.id_sum or 0),
                "latest_updated_at": _revision_value(row.latest_updated_at),
            }
            for row in rows
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _door_revision_aggregate(source, model, timestamp_column, *criteria):
    return select(
        literal(source).label("source"),
        func.count(model.id).label("row_count"),
        func.max(model.id).label("max_id"),
        func.coalesce(func.sum(model.id), 0).label("id_sum"),
        func.max(timestamp_column).label("latest_updated_at"),
    ).where(*criteria)


def _revision_value(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    return str(value or "")


def _pull_card_payload(
    gateway,
    selected_door,
    destination,
    operation,
    *,
    bundle=None,
):
    for index, card in enumerate(
        _destination_cards_for_door(
            gateway,
            selected_door,
            operation,
            bundle=bundle,
        )
    ):
        if card["destination"] == destination:
            return _door_card_state_payload(card, order_index=index)
    raise ValueError(f"{destination} is not assigned to {selected_door}.")


def _door_card_state_payload(card, order_index):
    return {
        "destination": card["destination"],
        "flight_number": card["flight_number"],
        "tail": card["tail"],
        "parking": card["parking"] or "-",
        "status": card["status"],
        "tail_presence": card["tail_presence"],
        "window_minutes": card["window_minutes"],
        "planned": card["planned"],
        "base_planned": card["base_planned"],
        "actual": card["actual"],
        "no_pull": card["no_pull"],
        "pull_alerts": card["pull_alerts"],
        "pulls_complete": card["pulls_complete"],
        "complete_title": card["complete_title"],
        "pull_summary": card["pull_summary"],
        "order_index": order_index,
    }


def normalize_door(value):
    value = str(value or "").strip().upper()
    if not value:
        return ""
    if value.startswith("D"):
        number = value[1:]
    else:
        number = value
    if not number.isdigit():
        return ""
    return f"D{int(number)}"


def get_door_options(gateway):
    return get_outbound_door_options()


def _destination_cards_for_door(
    gateway,
    selected_door,
    operation,
    door_pulls=_DOOR_PULL_LOOKUP_UNSET,
    initialize_lineup=True,
    bundle=None,
):
    if bundle is None:
        bundle = door_view_operational_state(
            gateway,
            operation=operation,
            initialize_lineup=initialize_lineup,
        )
    destination_slots = bundle.destinations_by_door.get(selected_door, {})
    missions = bundle.departure_missions_by_destination
    parking_by_tail = bundle.parking_by_tail
    arrivals_by_tail = bundle.arrivals_by_tail
    masters = bundle.masters_by_destination
    if door_pulls is _DOOR_PULL_LOOKUP_UNSET:
        door_pulls = bundle.door_pulls_by_door_destination
    cards = []

    for destination, slot_labels in destination_slots.items():
        mission = missions.get(destination)
        master = masters.get(destination)
        door_pull = door_pulls.get((selected_door, destination))
        timing_data = _mission_timing_data(mission, operation)
        planned_times = {
            "pure": _planned_pull_time(timing_data, master, "pure"),
            "mix": _planned_pull_time(timing_data, master, "mix"),
        }
        base_planned_times = {
            "pure": _base_pull_time(timing_data, master, "pure"),
            "mix": _base_pull_time(timing_data, master, "mix"),
        }
        actual = {
            "pure": _time_value(getattr(door_pull, "actual_pure_pull_time_local", None)),
            "mix": _time_value(getattr(door_pull, "actual_mix_pull_time_local", None)),
        }
        no_pull = {
            "pure": bool(getattr(door_pull, "no_pure_pull", False)),
            "mix": bool(getattr(door_pull, "no_mix_pull", False)),
        }
        tail_presence = departure_tail_presence(mission, arrivals_by_tail) if mission else None
        assigned_parking = _parking_for_mission(mission, parking_by_tail)
        visible_parking = (
            assigned_parking
            if tail_presence is None or tail_presence["show_door_parking"]
            else ""
        )

        pulls_complete = _pulls_complete(actual, no_pull, planned_times)
        cards.append(
            {
                "flight_number": _flight_number_for_card(mission, master),
                "destination": destination,
                "status": _status_for_card(mission, master, tail_presence),
                "slot_labels": slot_labels,
                "tail": mission.assigned_tail_number if mission else "",
                "parking": visible_parking,
                "tail_presence": tail_presence,
                "window_minutes": timing_data.get("effective_window_minutes"),
                "planned": {
                    "pure": _time_value(planned_times["pure"]),
                    "mix": _time_value(planned_times["mix"]),
                },
                "base_planned": {
                    "pure": _time_value(base_planned_times["pure"]),
                    "mix": _time_value(base_planned_times["mix"]),
                },
                "actual": actual,
                "no_pull": no_pull,
                "pull_alerts": _pull_alerts_for_card(
                    gateway,
                    selected_door,
                    destination,
                    operation,
                    planned_times,
                    actual,
                    no_pull,
                ),
                "pulls_complete": pulls_complete,
                "complete_title": _complete_title(
                    destination,
                    visible_parking,
                ),
                "pull_summary": _pull_summary(actual, no_pull),
                "_sort_key": _door_card_sort_key(
                    destination,
                    _flight_number_for_card(mission, master),
                    planned_times["pure"],
                    pulls_complete,
                    operation,
                    gateway,
                ),
            }
        )

    cards.sort(key=lambda card: card["_sort_key"])
    for card in cards:
        card.pop("_sort_key", None)
    return cards


def _door_card_sort_key(
    destination,
    flight_number,
    effective_pure_pull_time,
    pulls_complete,
    operation,
    gateway,
):
    return (
        1 if pulls_complete else 0,
        *_effective_pull_sort_key(operation, effective_pure_pull_time, gateway),
        normalize_destination(destination),
        str(flight_number or "").strip().upper(),
    )


def _effective_pull_sort_key(operation, planned_time, gateway=None):
    if not planned_time:
        return (1, 0)

    if not operation:
        return (
            0,
            planned_time.hour * 3600 + planned_time.minute * 60 + planned_time.second,
        )

    start_local, _end_local = sort_lookup_window_for_operation(
        operation,
        gateway or operation.gateway,
    )
    planned_local = datetime.combine(operation.sort_date, planned_time)
    if planned_local < start_local:
        planned_local += timedelta(days=1)
    return (0, int((planned_local - start_local).total_seconds()))


def _parking_assignments_by_tail(operation):
    if not operation:
        return {}
    assignments = SortDateParkingAssignment.query.filter_by(
        sort_date_operation_id=operation.id,
    ).all()
    return {
        normalize_tail_number(assignment.tail_number): assignment.position_code
        for assignment in assignments
        if normalize_tail_number(assignment.tail_number) and assignment.position_code
    }


def _parking_for_mission(mission, parking_by_tail):
    if not mission:
        return ""
    tail = normalize_tail_number(mission.assigned_tail_number)
    if not tail:
        return ""
    return str(parking_by_tail.get(tail) or "").strip().upper()


def _current_operation(gateway):
    return current_operational_sort_operation(gateway)


def _master_departures_by_destination(gateway):
    masters = (
        MasterFlightSchedule.query.filter(
            MasterFlightSchedule.mission_type == "departure",
            MasterFlightSchedule.active.is_(True),
            or_(
                MasterFlightSchedule.gateway_id == gateway.id,
                MasterFlightSchedule.gateway_code == gateway.code,
            ),
        )
        .order_by(MasterFlightSchedule.planned_time_local.asc(), MasterFlightSchedule.id.asc())
        .all()
    )
    result = {}
    for master in masters:
        destination = normalize_destination(master.destination)
        if destination and destination not in result:
            result[destination] = master
    return result


def _door_pull_record(
    gateway,
    selected_door,
    destination,
    operation,
    create=False,
    *,
    bundle=None,
):
    if bundle is not None:
        key = (normalize_door(selected_door), normalize_destination(destination))
        record = bundle.door_pulls_by_door_destination.get(key)
        if record is None and create:
            record = NeoErmacDoorPull(
                gateway_id=gateway.id,
                sort_date_operation_id=operation.id if operation else None,
                door=selected_door,
                destination=destination,
            )
            db.session.add(record)
            bundle.register_door_pull(record)
        return record

    query = NeoErmacDoorPull.query.filter_by(
        gateway_id=gateway.id,
        door=selected_door,
        destination=destination,
    )
    if operation:
        query = query.filter_by(sort_date_operation_id=operation.id)
    else:
        query = query.filter(NeoErmacDoorPull.sort_date_operation_id.is_(None))

    record = query.first()
    if not record and create:
        record = NeoErmacDoorPull(
            gateway_id=gateway.id,
            sort_date_operation_id=operation.id if operation else None,
            door=selected_door,
            destination=destination,
        )
        db.session.add(record)
    return record


def _uld_request_for_door(gateway, selected_door, operation):
    return aggregate_uld_request_for_door(gateway, selected_door, operation)


def _mission_timing_data(mission, operation):
    if not mission:
        return {}
    return mission_display_timing_data(mission, operation)


def _flight_number_for_card(mission, master):
    flight_number = getattr(mission, "flight_number", None) or getattr(
        master,
        "flight_number",
        None,
    )
    return str(flight_number or "").strip().upper()


def _status_for_card(mission, master, tail_presence=None):
    if mission:
        override = tail_presence_status_override(mission, tail_presence)
        if override:
            return override
        status = str(getattr(mission, "departure_status", "") or "").strip()
        if (
            tail_presence
            and tail_presence["state"] == "arrived"
            and status.lower() in {"", "scheduled"}
        ):
            return "ARRIVED"
        return _labelize(status) if status else "Scheduled"
    if master:
        return "MASTER SCHEDULE"
    return "NO FLIGHT DATA"


def _planned_pull_time(timing_data, master, pull_key):
    adjusted_key = {
        "pure": "adjusted_pure_pull_time",
        "mix": "adjusted_mix_pull_time",
    }[pull_key]
    return timing_data.get(adjusted_key) or _master_pull_time(master, pull_key)


def _base_pull_time(timing_data, master, pull_key):
    base_key = {
        "pure": "base_pure_pull_time",
        "mix": "base_mix_pull_time",
    }[pull_key]
    return timing_data.get(base_key) or _master_pull_time(master, pull_key)


def _master_pull_time(master, pull_key):
    attr = {
        "pure": "pure_pull_time_local",
        "mix": "mix_pull_time_local",
    }[pull_key]
    return getattr(master, attr, None)


def _pull_alerts_for_card(
    gateway,
    selected_door,
    destination,
    operation,
    planned_times,
    actual,
    no_pull,
):
    alerts = {field["key"]: _empty_pull_alert() for field in PULL_FIELDS}
    if not operation:
        return alerts

    local_now = current_gateway_local_datetime(gateway)
    start_local, end_local = sort_lookup_window_for_operation(operation, gateway)
    operation_is_active = operation_is_active_at(operation, local_now, gateway)
    for field in PULL_FIELDS:
        pull_key = field["key"]
        accounted = bool(no_pull.get(pull_key) or actual.get(pull_key))
        alerts[pull_key]["accounted"] = accounted
        planned_time = planned_times.get(pull_key)
        planned_local = _pull_planned_datetime(operation, start_local, end_local, planned_time)
        if not planned_local:
            continue

        alerts[pull_key].update(
            _pull_alert_timing(
                gateway,
                operation,
                selected_door,
                destination,
                pull_key,
                planned_local,
                start_local,
                end_local,
                accounted,
            )
        )
        if accounted or not operation_is_active:
            continue

        seconds_until = (planned_local - local_now).total_seconds()
        if seconds_until <= -(PULL_DUE_WARNING_MINUTES * 60):
            alerts[pull_key].update(
                {
                    "state": "late",
                    "css_class": "is-pull-late",
                    "label": "LATE",
                    "minutes": int(abs(seconds_until) // 60),
                }
            )
        elif seconds_until <= 0:
            alerts[pull_key].update(
                {
                    "state": "due_now",
                    "css_class": "is-pull-due-now",
                    "label": "PULL NOW",
                    "minutes": int(abs(seconds_until) // 60),
                }
            )
        elif seconds_until <= PULL_DUE_WARNING_MINUTES * 60:
            alerts[pull_key].update(
                {
                    "state": "due_soon",
                    "css_class": "is-pull-due-soon",
                    "label": "DUE SOON",
                    "minutes": int(seconds_until // 60),
                }
            )
    return alerts


def _empty_pull_alert():
    return {
        "state": "",
        "css_class": "",
        "label": "",
        "key": "",
        "minutes": None,
        "accounted": False,
        "due_soon_epoch_ms": None,
        "due_now_epoch_ms": None,
        "late_epoch_ms": None,
        "window_start_epoch_ms": None,
        "window_end_epoch_ms": None,
    }


def _empty_door_tab_alert():
    return _door_tab_alert("")


def _door_tab_alert(state, pulls=()):
    pulls = list(pulls or ())
    if state == "late":
        return {
            "state": "late",
            "css_class": "is-pull-late",
            "label": "Late pull",
            "pulls": pulls,
        }
    if state == "due_now":
        return {
            "state": "due_now",
            "css_class": "is-pull-due-now",
            "label": "Pull now",
            "pulls": pulls,
        }
    return {"state": "", "css_class": "", "label": "", "pulls": pulls}


def _pull_alert_timing(
    gateway,
    operation,
    selected_door,
    destination,
    pull_key,
    planned_local,
    start_local,
    end_local,
    accounted,
):
    return {
        "key": _pull_alert_key(
            operation,
            selected_door,
            destination,
            pull_key,
            planned_local,
        ),
        "accounted": bool(accounted),
        "due_soon_epoch_ms": _gateway_local_epoch_ms(
            gateway,
            planned_local - timedelta(minutes=PULL_DUE_WARNING_MINUTES),
        ),
        "due_now_epoch_ms": _gateway_local_epoch_ms(gateway, planned_local),
        "late_epoch_ms": _gateway_local_epoch_ms(
            gateway,
            planned_local + timedelta(minutes=PULL_DUE_WARNING_MINUTES),
        ),
        "window_start_epoch_ms": _gateway_local_epoch_ms(gateway, start_local),
        "window_end_epoch_ms": _gateway_local_epoch_ms(gateway, end_local),
    }


def _gateway_local_epoch_ms(gateway, value):
    if value is None:
        return None
    try:
        zone = ZoneInfo(gateway_timezone(gateway))
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("America/Chicago")
    return int(value.replace(tzinfo=zone).timestamp() * 1000)


def _pull_planned_datetime(operation, start_local, end_local, planned_time):
    if not operation or not start_local or not end_local or not planned_time:
        return None

    planned_local = datetime.combine(operation.sort_date, planned_time)
    if planned_local < start_local:
        planned_local += timedelta(days=1)
    if planned_local < start_local or planned_local >= end_local:
        return None
    return planned_local


def _pull_alert_key(operation, selected_door, destination, pull_key, planned_local):
    return ":".join(
        (
            f"op-{operation.id}",
            normalize_door(selected_door) or "-",
            normalize_destination(destination) or "-",
            pull_key,
            planned_local.strftime("%Y%m%d%H%M"),
        )
    )


def _labelize(value):
    return str(value or "").strip().replace("_", " ").title()


def _parse_optional_time(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time().replace(second=0, microsecond=0)
    except ValueError as exc:
        raise ValueError("Actual pull times must use HH:MM format.") from exc


def _time_value(value):
    if not value:
        return ""
    return value.strftime("%H:%M")


def _pull_field_by_key(pull_key):
    pull_key = str(pull_key or "").strip()
    for field in PULL_FIELDS:
        if field["key"] == pull_key:
            return field
    return None


def _pulls_complete(actual, no_pull, planned_times):
    required_fields = [
        field
        for field in PULL_FIELDS
        if planned_times.get(field["key"]) is not None
    ]
    return bool(required_fields) and all(
        bool(no_pull[field["key"]] or actual[field["key"]])
        for field in required_fields
    )


def _pull_summary(actual, no_pull):
    labels = {
        "pure": "PURE",
        "mix": "MIX",
    }
    parts = []
    for field in PULL_FIELDS:
        key = field["key"]
        value = "NONE" if no_pull[key] else (actual[key] or "-")
        parts.append(f"{labels[key]} {value}")
    return " · ".join(parts)


def _complete_title(destination, parking):
    destination = normalize_destination(destination) or "-"
    parking = str(parking or "").strip().upper() or "-"
    return f"{destination} {parking} COMPLETE"


def _int_value(value, default=0):
    value = str(value if value is not None else "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("ULD counts must be whole numbers.") from exc
    if parsed < 0:
        raise ValueError("ULD counts cannot be negative.")
    return parsed
