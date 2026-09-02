"""Canonical inventory of operational screens allowed to auto-refresh."""

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveScreenDefinition:
    node: str
    node_label: str
    label: str
    screen_key: str
    route_endpoint: str
    refresh_endpoint: str
    route_values: tuple[tuple[str, str], ...] = ()


LIVE_SCREEN_REGISTRY = (
    LiveScreenDefinition("motherbrain", "NeoMotherBrain", "Arrival Planning", "neomotherbrain.arrival_planning", "neomotherbrain.alp_import", "neomotherbrain.planning_live_state", (("mission_type", "arrival"),)),
    LiveScreenDefinition("motherbrain", "NeoMotherBrain", "Departure Planning", "neomotherbrain.departure_planning", "neomotherbrain.alp_import", "neomotherbrain.planning_live_state", (("mission_type", "departure"),)),
    LiveScreenDefinition("motherbrain", "NeoMotherBrain", "Parking Plan", "neomotherbrain.parking_plan", "neomotherbrain.parking_plan", "neomotherbrain.parking_plan_live_state_endpoint"),
    LiveScreenDefinition("sektor", "NeoSektor", "Live Counts", "neosektor.live_counts", "neosektor.live_counts", "neosektor.live_counts_state"),
    LiveScreenDefinition("sektor", "NeoSektor", "Tunnel Conductor", "neosektor.tunnel_conductor", "neosektor.tunnel_conductor", "neosektor.tunnel_conductor_state"),
    LiveScreenDefinition("sektor", "NeoSektor", "EBM", "neosektor.ebm", "neosektor.ballmat_operations", "neosektor.ballmat_state", (("side", "east"),)),
    LiveScreenDefinition("sektor", "NeoSektor", "WBM", "neosektor.wbm", "neosektor.ballmat_operations", "neosektor.ballmat_state", (("side", "west"),)),
    LiveScreenDefinition("sektor", "NeoSektor", "Discharge", "neosektor.discharge", "neosektor.discharge", "neosektor.discharge_state"),
    LiveScreenDefinition("sektor", "NeoSektor", "Driver Routing", "neosektor.driver_routing", "neosektor.driver_routing", "neosektor.driver_routing_state"),
    LiveScreenDefinition("ermac", "NeoErmac", "Upcoming Pulls", "neoermac.upcoming_pulls", "neoermac.upcoming_pulls", "neoermac.upcoming_pulls_state"),
    LiveScreenDefinition("ermac", "NeoErmac", "Building Lineup", "neoermac.building_lineup", "neoermac.building_lineup", "neoermac.building_lineup_state"),
    LiveScreenDefinition("ermac", "NeoErmac", "View Outbound", "neoermac.view_outbound", "neoermac.view_outbound", "neoermac.view_outbound_state"),
    LiveScreenDefinition("ermac", "NeoErmac", "Door View", "neoermac.door_view", "neoermac.door_view", "neoermac.door_view_state"),
    LiveScreenDefinition("scorpion", "NeoScorpion", "Fuel Dispatch", "neoscorpion.fuel_dispatch", "neoscorpion.fuel_dispatch", "neoscorpion.fuel_dispatch_revision"),
    LiveScreenDefinition("scorpion", "NeoScorpion", "Fuel Assignments", "neoscorpion.fuel_assignments", "neoscorpion.fueler", "neoscorpion.fuel_assignments_revision"),
    LiveScreenDefinition("scorpion", "NeoScorpion", "Hanzo", "neoscorpion.hanzo", "neoscorpion.hanzo", "neoscorpion.hanzo_revision"),
    LiveScreenDefinition("rain", "NeoRain", "Inbound", "neorain.inbound", "neorain.inbound", "neorain.inbound_revision"),
    LiveScreenDefinition("rain", "NeoRain", "Outbound", "neorain.outbound", "neorain.outbound", "neorain.outbound_revision"),
    LiveScreenDefinition("subzero", "NeoSubZero", "Pretreat", "neosubzero.pretreat", "neosubzero.pretreat", "neosubzero.pretreat_revision_endpoint"),
    LiveScreenDefinition("subzero", "NeoSubZero", "Outbound", "neosubzero.outbound", "neosubzero.outbound", "neosubzero.outbound_revision_endpoint"),
    LiveScreenDefinition("subzero", "NeoSubZero", "Coordinator", "neosubzero.coordinator", "neosubzero.coordinator", "neosubzero.coordinator_revision_endpoint"),
    LiveScreenDefinition("subzero", "NeoSubZero", "UCC", "neosubzero.ucc", "neosubzero.ucc", "neosubzero.ucc_revision_endpoint"),
    LiveScreenDefinition("subzero", "NeoSubZero", "Deicer Mobile", "neosubzero.deicer_mobile", "neosubzero.deicer_mobile", "neosubzero.deicer_mobile_revision_endpoint"),
)

_BY_KEY = {item.screen_key: item for item in LIVE_SCREEN_REGISTRY}


def live_screen_definition(screen_key):
    return _BY_KEY.get(str(screen_key or "").strip().lower())


def registered_live_screen_keys():
    return tuple(_BY_KEY)


def grouped_live_screens():
    groups = []
    for item in LIVE_SCREEN_REGISTRY:
        if not groups or groups[-1]["key"] != item.node:
            groups.append({"key": item.node, "label": item.node_label, "screens": []})
        groups[-1]["screens"].append(item)
    return groups


def live_screen_for_refresh_request(endpoint, view_args=None):
    values = {key: str(value).strip().lower() for key, value in (view_args or {}).items()}
    for item in LIVE_SCREEN_REGISTRY:
        if item.refresh_endpoint != endpoint:
            continue
        if all(values.get(key) == expected for key, expected in item.route_values):
            return item
    return next(
        (item for item in LIVE_SCREEN_REGISTRY if item.refresh_endpoint == endpoint),
        None,
    )
