"""Resolved shared-shell metadata for NeoApps templates.

The base template consumes these values as plain context keys. Keeping the
request classifier here prevents page and node label rules from drifting
between desktop and mobile shell markup.
"""


NODE_IDENTITIES = {
    "motherbrain": {
        "name": "NeoMotherBrain",
        "word": "MotherBrain",
        "home_endpoint": "neomotherbrain.motherbrain",
        "locked_icon": "images/icons/neomotherbrain/inapp/neomotherbrain-inapp-128.png",
        "desktop_icon": "images/icons/neomotherbrain/inapp/neomotherbrain-inapp-256.png",
        "icon_alt": "NeoMotherBrain icon",
    },
    "ermac": {
        "name": "NeoErmac",
        "word": "Ermac",
        "home_endpoint": "neoermac.index",
        "locked_icon": "images/icons/neoermac/inapp/neoermac-inapp-128.png",
        "desktop_icon": "images/icons/neoermac/inapp/neoermac-inapp-256.png",
        "icon_alt": "NeoErmac icon",
    },
    "sektor": {
        "name": "NeoSektor",
        "word": "Sektor",
        "home_endpoint": "neosektor.index",
        "locked_icon": "images/icons/neosektor/inapp/neosektor-icon-128x128.png",
        "desktop_icon": "images/icons/neosektor/inapp/neosektor-icon-256x256.png",
        "icon_alt": "NeoSektor icon",
    },
    "scorpion": {
        "name": "NeoScorpion",
        "word": "Scorpion",
        "home_endpoint": "neoscorpion.index",
        "locked_icon": "images/icons/neoscorpion/inapp/neoscorpion-128x128.png",
        "desktop_icon": "images/icons/neoscorpion/inapp/neoscorpion-256x256.png",
        "icon_alt": "NeoScorpion icon",
    },
    "reptile": {
        "name": "NeoReptile",
        "word": "Reptile",
        "home_endpoint": "neoreptile.index",
    },
    "subzero": {
        "name": "NeoSub-Zero",
        "word": "Sub-Zero",
        "home_endpoint": "neosubzero.index",
    },
    "rain": {
        "name": "NeoRain",
        "word": "Rain",
        "home_endpoint": "neorain.index",
    },
}


def resolve_shell_metadata(
    request,
    *,
    is_authenticated=False,
    user_last_name=None,
    user_display_name=None,
    default_gateway_code="RFD",
):
    """Return the shared desktop and mobile shell values for ``request``.

    The returned keys intentionally match the historical base-template names,
    making this a behavior-only refactor for every existing shell consumer.
    """

    path = request.path
    normalized_path = path.rstrip("/") or "/"
    blueprint = request.blueprint or ""
    endpoint = request.endpoint or ""
    ballmat_side = str(request.args.get("side", "")).lower()
    is_authenticated_app = bool(is_authenticated) and endpoint != "auth.login"

    is_portal_page = (
        is_authenticated_app
        and blueprint == "auth"
        and (path.startswith("/portal") or path.startswith("/neobid"))
    )
    is_portal_dashboard_page = (
        is_authenticated_app and blueprint == "auth" and path.startswith("/portal")
    )
    is_neostaffing_page = is_authenticated_app and blueprint == "neostaffing"
    is_motherbrain_page = (
        is_authenticated_app
        and blueprint == "neomotherbrain"
        and path.startswith("/motherbrain")
    )
    is_rfd_hub_page = (
        is_authenticated_app
        and blueprint == "neomotherbrain"
        and normalized_path == "/rfd"
    )
    is_motherbrain_landing = is_motherbrain_page and path == "/motherbrain"
    is_user_management_page = (
        is_authenticated_app
        and blueprint == "auth"
        and (path.startswith("/admin/users") or path.startswith("/portal/manage"))
    )
    is_permission_management_page = (
        is_authenticated_app
        and blueprint == "auth"
        and (
            path.startswith("/admin/permissions")
            or path.startswith("/motherbrain/permissions")
        )
    )
    uses_motherbrain_header = (
        is_motherbrain_page
        or is_user_management_page
        or is_permission_management_page
    )

    is_neoermac_page = is_authenticated_app and blueprint == "neoermac"
    is_neosektor_page = is_authenticated_app and blueprint == "neosektor"
    is_neoscorpion_page = is_authenticated_app and blueprint == "neoscorpion"
    is_neoreptile_page = is_authenticated_app and blueprint == "neoreptile"
    is_neosubzero_page = is_authenticated_app and blueprint == "neosubzero"
    is_neorain_page = is_authenticated_app and blueprint == "neorain"

    is_arrival_planning_page = is_motherbrain_page and path.endswith("/alp/arrival")
    is_departure_planning_page = is_motherbrain_page and path.endswith(
        "/alp/departure"
    )
    is_manage_sort_page = is_motherbrain_page and (
        path.startswith("/motherbrain/manage-sort")
        or (
            path.startswith("/motherbrain/operations")
            and not is_arrival_planning_page
            and not is_departure_planning_page
        )
    )
    is_motherbrain_mobile_tile_landing = (
        is_motherbrain_page and path == "/motherbrain/manage-sort"
    )
    is_motherbrain_manage_sort_detail = (
        is_motherbrain_page
        and path.startswith("/motherbrain/operations")
        and not is_arrival_planning_page
        and not is_departure_planning_page
    )
    is_gateway_matrix_page = is_motherbrain_page and path.startswith(
        "/motherbrain/gateway-matrix"
    )
    is_master_schedule_page = is_motherbrain_page and path.startswith(
        "/motherbrain/master-schedule"
    )
    is_parking_plan_page = is_motherbrain_page and path.startswith(
        "/motherbrain/parking-plan"
    )
    is_sort_timeline_page = is_motherbrain_page and path.startswith(
        "/motherbrain/sort-timeline"
    )
    is_flight_api_test_page = is_motherbrain_page and path.startswith(
        "/motherbrain/flight-api-test"
    )
    is_flight_api_review_page = is_motherbrain_page and path.startswith(
        "/motherbrain/flight-api-review"
    )
    is_parking_rules_page = is_motherbrain_page and path.startswith(
        "/motherbrain/parking-rules"
    )

    motherbrain_current_label, motherbrain_mobile_label = _motherbrain_labels(
        is_permission_management_page=is_permission_management_page,
        is_user_management_page=is_user_management_page,
        is_flight_api_review_page=is_flight_api_review_page,
        is_flight_api_test_page=is_flight_api_test_page,
        is_parking_rules_page=is_parking_rules_page,
        is_parking_plan_page=is_parking_plan_page,
        is_sort_timeline_page=is_sort_timeline_page,
        is_gateway_matrix_page=is_gateway_matrix_page,
        is_master_schedule_page=is_master_schedule_page,
        is_departure_planning_page=is_departure_planning_page,
        is_arrival_planning_page=is_arrival_planning_page,
        is_manage_sort_page=is_manage_sort_page,
        is_motherbrain_landing=is_motherbrain_landing,
        is_motherbrain_mobile_tile_landing=is_motherbrain_mobile_tile_landing,
    )

    is_neoermac_view_outbound_page = path.startswith(
        "/neoermac/view-outbound"
    ) or path.startswith("/neoermac/outbound")
    neoermac_current_label, neoermac_mobile_label = _neoermac_labels(
        path, is_neoermac_view_outbound_page
    )
    neoscorpion_current_label, neoscorpion_mobile_label = _neoscorpion_labels(path)

    is_neosektor_ebm_page = path.startswith("/neosektor/ebm") or (
        path.startswith("/neosektor/ballmat") and ballmat_side == "east"
    )
    is_neosektor_wbm_page = path.startswith("/neosektor/wbm") or (
        path.startswith("/neosektor/ballmat") and ballmat_side == "west"
    )
    is_neosektor_ballmat_operator_page = (
        is_neosektor_ebm_page or is_neosektor_wbm_page
    )
    is_neosektor_tunnel_operator_page = is_neosektor_page and path.startswith(
        "/neosektor/tunnel-conductor"
    )
    is_neosektor_live_counts_page = is_neosektor_page and path.startswith(
        "/neosektor/live-counts"
    )
    is_neosektor_driver_page = is_neosektor_page and path.startswith(
        "/neosektor/driver-routing"
    )
    is_neosektor_standalone_operator_page = (
        is_neosektor_ballmat_operator_page
        or is_neosektor_tunnel_operator_page
        or is_neosektor_live_counts_page
    )
    neosektor_current_label, neosektor_mobile_label = _neosektor_labels(
        path,
        is_neosektor_ebm_page=is_neosektor_ebm_page,
        is_neosektor_wbm_page=is_neosektor_wbm_page,
        is_neosektor_live_counts_page=is_neosektor_live_counts_page,
    )

    uses_node_desktop_shell = is_neoermac_page or is_neoscorpion_page or (
        is_neosektor_page and not is_neosektor_driver_page
    )
    is_neosektor_landing = is_neosektor_page and normalized_path == "/neosektor"
    uses_node_header = (
        uses_motherbrain_header
        or is_neoermac_page
        or is_neoscorpion_page
        or is_neoreptile_page
        or is_neosubzero_page
        or is_neorain_page
        or (is_neosektor_page and not is_neosektor_driver_page)
    )

    node_key = _node_key(
        is_neosektor_page=is_neosektor_page,
        is_neoermac_page=is_neoermac_page,
        is_neoscorpion_page=is_neoscorpion_page,
        is_neoreptile_page=is_neoreptile_page,
        is_neosubzero_page=is_neosubzero_page,
        is_neorain_page=is_neorain_page,
    )
    node_identity = NODE_IDENTITIES[node_key]
    header_identity_key = (
        "sektor"
        if is_neosektor_page
        else "ermac"
        if is_neoermac_page
        else "scorpion"
        if is_neoscorpion_page
        else "motherbrain"
        if uses_motherbrain_header
        else None
    )
    header_identity = (
        NODE_IDENTITIES[header_identity_key] if header_identity_key else None
    )
    node_header_logo = (
        header_identity["locked_icon"]
        if header_identity
        else "images/motherbrain_logo1.png"
    )
    node_desktop_sidebar_logo = (
        header_identity["desktop_icon"] if header_identity else node_header_logo
    )
    node_header_alt = (
        header_identity["icon_alt"] if header_identity else "MotherBrain logo"
    )
    node_current_label = (
        neosektor_current_label
        if is_neosektor_page
        else neoermac_current_label
        if is_neoermac_page
        else neoscorpion_current_label
        if is_neoscorpion_page
        else motherbrain_current_label
    )
    has_node_shell_identity = (
        uses_node_header
        or is_neosektor_page
        or is_neoermac_page
        or is_neoscorpion_page
        or is_neoreptile_page
        or is_neosubzero_page
        or is_neorain_page
    )
    neostaffing_current_label = _neostaffing_label(endpoint)
    mobile_shell_key = (
        node_key
        if has_node_shell_identity
        else "staffing"
        if is_neostaffing_page
        else "gateway"
        if is_rfd_hub_page
        else "apps"
    )
    mobile_shell_name = (
        node_identity["name"]
        if has_node_shell_identity
        else "NeoStaffing"
        if is_neostaffing_page
        else "NeoGateway"
        if is_rfd_hub_page
        else "NeoApps"
    )
    mobile_shell_word = (
        node_identity["word"]
        if has_node_shell_identity
        else "Staffing"
        if is_neostaffing_page
        else "Gateway"
        if is_rfd_hub_page
        else "Apps"
    )
    mobile_shell_label = (
        motherbrain_mobile_label
        if uses_motherbrain_header
        else neoermac_mobile_label
        if is_neoermac_page
        else neosektor_mobile_label
        if is_neosektor_page
        else neoscorpion_mobile_label
        if is_neoscorpion_page
        else node_current_label
        if has_node_shell_identity
        else default_gateway_code
        if is_rfd_hub_page
        else neostaffing_current_label
        if is_neostaffing_page
        else "PORTAL"
    )
    node_home_endpoint = node_identity["home_endpoint"]
    mobile_home_endpoint = (
        "neomotherbrain.manage_sort"
        if uses_motherbrain_header
        else node_home_endpoint
        if has_node_shell_identity
        else "neostaffing.index"
        if is_neostaffing_page
        else "neomotherbrain.rfd_hub"
        if is_rfd_hub_page
        else "auth.portal_dashboard"
    )
    mobile_back_endpoint = (
        "neomotherbrain.rfd_hub"
        if is_neosektor_landing
        else "neosektor.index"
        if is_neosektor_page
        else mobile_home_endpoint
    )
    mobile_back_word = (
        "Gateway"
        if is_neosektor_landing
        else "NeoSektor"
        if is_neosektor_page
        else mobile_shell_word
    )
    uses_gateway_mobile_shell = is_rfd_hub_page or has_node_shell_identity
    mobile_node_icon = (
        "images/icons/neogateway/inapp/neogateway-inapp-128.png"
        if is_rfd_hub_page
        else node_header_logo
    )
    mobile_account_initial_source = user_last_name or user_display_name or "?"
    uses_mobile_chrome = is_authenticated_app and not is_neosektor_driver_page

    return {
        "is_authenticated_app": is_authenticated_app,
        "is_portal_page": is_portal_page,
        "is_portal_dashboard_page": is_portal_dashboard_page,
        "is_neostaffing_page": is_neostaffing_page,
        "is_motherbrain_page": is_motherbrain_page,
        "is_rfd_hub_page": is_rfd_hub_page,
        "is_motherbrain_landing": is_motherbrain_landing,
        "is_user_management_page": is_user_management_page,
        "is_permission_management_page": is_permission_management_page,
        "uses_motherbrain_header": uses_motherbrain_header,
        "is_neoermac_page": is_neoermac_page,
        "is_neosektor_page": is_neosektor_page,
        "is_neoscorpion_page": is_neoscorpion_page,
        "is_neoreptile_page": is_neoreptile_page,
        "is_neosubzero_page": is_neosubzero_page,
        "is_neorain_page": is_neorain_page,
        "uses_node_desktop_shell": uses_node_desktop_shell,
        "is_neosektor_landing": is_neosektor_landing,
        "is_gateway_matrix_page": is_gateway_matrix_page,
        "is_master_schedule_page": is_master_schedule_page,
        "is_arrival_planning_page": is_arrival_planning_page,
        "is_departure_planning_page": is_departure_planning_page,
        "is_manage_sort_page": is_manage_sort_page,
        "is_motherbrain_mobile_tile_landing": is_motherbrain_mobile_tile_landing,
        "is_motherbrain_manage_sort_detail": is_motherbrain_manage_sort_detail,
        "is_parking_plan_page": is_parking_plan_page,
        "is_sort_timeline_page": is_sort_timeline_page,
        "is_flight_api_test_page": is_flight_api_test_page,
        "is_flight_api_review_page": is_flight_api_review_page,
        "is_parking_rules_page": is_parking_rules_page,
        "motherbrain_current_label": motherbrain_current_label,
        "motherbrain_mobile_label": motherbrain_mobile_label,
        "is_neoermac_view_outbound_page": is_neoermac_view_outbound_page,
        "neoermac_current_label": neoermac_current_label,
        "neoermac_mobile_label": neoermac_mobile_label,
        "neoscorpion_current_label": neoscorpion_current_label,
        "neoscorpion_mobile_label": neoscorpion_mobile_label,
        "neosektor_ballmat_side": ballmat_side,
        "is_neosektor_ebm_page": is_neosektor_ebm_page,
        "is_neosektor_wbm_page": is_neosektor_wbm_page,
        "is_neosektor_ballmat_operator_page": is_neosektor_ballmat_operator_page,
        "is_neosektor_tunnel_operator_page": is_neosektor_tunnel_operator_page,
        "is_neosektor_live_counts_page": is_neosektor_live_counts_page,
        "is_neosektor_driver_page": is_neosektor_driver_page,
        "is_neosektor_standalone_operator_page": is_neosektor_standalone_operator_page,
        "uses_node_header": uses_node_header,
        "neosektor_current_label": neosektor_current_label,
        "neosektor_mobile_label": neosektor_mobile_label,
        "node_home_endpoint": node_home_endpoint,
        "motherbrain_locked_icon": NODE_IDENTITIES["motherbrain"]["locked_icon"],
        "neogateway_locked_icon": "images/icons/neogateway/inapp/neogateway-inapp-128.png",
        "neoermac_locked_icon": NODE_IDENTITIES["ermac"]["locked_icon"],
        "neosektor_locked_icon": NODE_IDENTITIES["sektor"]["locked_icon"],
        "neoscorpion_locked_icon": NODE_IDENTITIES["scorpion"]["locked_icon"],
        "neostaffing_locked_icon": "images/icons/neostaffing/inapp/neostaffing-inapp-128.png",
        "motherbrain_desktop_sidebar_icon": NODE_IDENTITIES["motherbrain"][
            "desktop_icon"
        ],
        "neoermac_desktop_sidebar_icon": NODE_IDENTITIES["ermac"]["desktop_icon"],
        "neosektor_desktop_sidebar_icon": NODE_IDENTITIES["sektor"]["desktop_icon"],
        "neoscorpion_desktop_sidebar_icon": NODE_IDENTITIES["scorpion"]["desktop_icon"],
        "node_header_logo": node_header_logo,
        "node_desktop_sidebar_logo": node_desktop_sidebar_logo,
        "node_header_alt": node_header_alt,
        "node_header_key": node_key,
        "node_header_word": node_identity["word"],
        "node_header_name": node_identity["name"],
        "node_current_label": node_current_label,
        "mobile_shell_key": mobile_shell_key,
        "mobile_shell_name": mobile_shell_name,
        "mobile_shell_word": mobile_shell_word,
        "neostaffing_current_label": neostaffing_current_label,
        "mobile_shell_label": mobile_shell_label,
        "mobile_home_endpoint": mobile_home_endpoint,
        "mobile_back_endpoint": mobile_back_endpoint,
        "mobile_back_word": mobile_back_word,
        "uses_gateway_mobile_shell": uses_gateway_mobile_shell,
        "mobile_node_icon": mobile_node_icon,
        "mobile_account_initial_source": mobile_account_initial_source,
        "mobile_account_initial": str(mobile_account_initial_source)[:1].upper(),
        "uses_mobile_chrome": uses_mobile_chrome,
        "show_mobile_bottom_nav": uses_mobile_chrome and not is_rfd_hub_page,
    }


def _motherbrain_labels(**flags):
    if flags["is_permission_management_page"]:
        return "Permission Rules", "Permissions"
    if flags["is_user_management_page"]:
        return "Portal Management", "Portal Mgmt"
    if flags["is_flight_api_review_page"]:
        return "Unmatched Queue", "Queue"
    if flags["is_flight_api_test_page"]:
        return "Manage API", "API"
    if flags["is_parking_rules_page"]:
        return "Parking Rules", "Rules"
    if flags["is_parking_plan_page"]:
        return "Parking Plan", "Parking"
    if flags["is_sort_timeline_page"]:
        return "Sort Timeline", "Timeline"
    if flags["is_gateway_matrix_page"]:
        return "Gateway Matrix", "Matrix"
    if flags["is_master_schedule_page"]:
        return "Master Schedule", "Schedule"
    if flags["is_departure_planning_page"]:
        return "Departure Planning", "Depart"
    if flags["is_arrival_planning_page"]:
        return "Arrival Planning", "Arrivals"
    if flags["is_motherbrain_mobile_tile_landing"]:
        return "Manage Sort", "Dashboard"
    if flags["is_manage_sort_page"]:
        return "Manage Sort", "Sort"
    if flags["is_motherbrain_landing"]:
        return "Dashboard", "Dashboard"
    return "Manage Sort", "Dashboard"


def _neoermac_labels(path, is_view_outbound):
    if path.startswith("/neoermac/building-lineup"):
        return "BUILDING LINEUP", "LINEUP"
    if path.startswith("/neoermac/door-view"):
        return "DOOR VIEW", "DOORS"
    if is_view_outbound:
        return "VIEW OUTBOUND", "OUTBOUND"
    if path.startswith("/neoermac/upcoming-pulls"):
        return "UPCOMING PULLS", "PULLS"
    if path.startswith("/neoermac/tug-assignments"):
        return "TUG ASSIGNMENTS", "TUGS"
    return "DASHBOARD", "DASHBOARD"


def _neoscorpion_labels(path):
    if path.startswith("/neoscorpion/fuel-dispatch"):
        return "FUEL DISPATCH", "DISPATCH"
    if path.startswith("/neoscorpion/fueler"):
        return "FUELER", "FUELER"
    if path.startswith("/neoscorpion/truck-manager"):
        return "TRUCK MANAGER", "TRUCKS"
    if path.startswith("/neoscorpion/settings"):
        return "SETTINGS", "SETTINGS"
    if path.startswith("/neoscorpion/history") or path.startswith(
        "/neoscorpion/completed-fuel"
    ):
        return "FUEL HISTORY", "HISTORY"
    return "DASHBOARD", "DASHBOARD"


def _neosektor_labels(
    path,
    *,
    is_neosektor_ebm_page,
    is_neosektor_wbm_page,
    is_neosektor_live_counts_page,
):
    if path.startswith("/neosektor/tunnel-conductor"):
        return "TUNNEL CONDUCTOR", "TUNNEL"
    if is_neosektor_ebm_page:
        return "EBM", "EBM"
    if is_neosektor_wbm_page:
        return "WBM", "WBM"
    if path.startswith("/neosektor/discharge"):
        return "DISCHARGE", "DISCHARGE"
    if is_neosektor_live_counts_page:
        return "LIVE COUNTS", "COUNTS"
    if path.startswith("/neosektor/driver-routing"):
        return "DRIVER ROUTING", "ROUTING"
    if path.startswith("/neosektor/settings"):
        return "SETTINGS", "DASHBOARD"
    return "DASHBOARD", "DASHBOARD"


def _neostaffing_label(endpoint):
    if endpoint in ("neostaffing.attendance", "neostaffing.people_attendance"):
        return "Attendance"
    if endpoint == "neostaffing.people":
        return "People"
    if endpoint == "neostaffing.org_chart":
        return "Org Chart"
    if endpoint == "neostaffing.reports":
        return "Reports"
    return "NeoStaffing"


def _node_key(**flags):
    if flags["is_neosektor_page"]:
        return "sektor"
    if flags["is_neoermac_page"]:
        return "ermac"
    if flags["is_neoscorpion_page"]:
        return "scorpion"
    if flags["is_neoreptile_page"]:
        return "reptile"
    if flags["is_neosubzero_page"]:
        return "subzero"
    if flags["is_neorain_page"]:
        return "rain"
    return "motherbrain"
