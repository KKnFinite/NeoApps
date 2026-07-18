"""Stable browser-tab titles for NeoApps rendered pages.

Titles intentionally come from route context instead of template data so an
operation, flight, employee, or sort date can never become a browser-tab
title by accident.
"""


def browser_tab_title(request):
    """Return the browser title for the current request.

    Keep the title format uniform: ``Page Name | Owning App or Node``.  The
    resolver is shared by the base template so individual page templates do
    not need to (and must not) build titles from record-specific context.
    """

    path = request.path.rstrip("/") or "/"

    page_name, owner = _title_parts(path, request.args.get("side", ""))
    return f"{page_name} | {owner}"


def _title_parts(path, ballmat_side):
    """Resolve stable title parts from a canonical request path."""

    if path.startswith("/portal/manage/users/all") or path.startswith("/admin/users/all"):
        return "All Users", "NeoApps"
    if path.startswith("/portal/manage/users/edit-users") or path.startswith("/admin/users/edit-users"):
        return "Edit Users", "NeoApps"
    if path.startswith("/portal/manage/users/manage-roles") or path.startswith("/admin/users/manage-roles"):
        return "Node Roles", "NeoApps"
    if path.startswith("/portal/manage/users/pending") or path.startswith("/admin/users/pending"):
        return "Pending Users", "NeoApps"
    if path.startswith("/portal/manage/users/") or path.startswith("/admin/users/"):
        if "/emergency-" in path or path.endswith("/emergency-reset"):
            return "Emergency Password Reset", "NeoApps"
        if path.endswith("/roles"):
            return "Node Roles", "NeoApps"
        if path.endswith("/edit"):
            return "Edit User", "NeoApps"
        return "User Management", "NeoApps"
    if path in {"/portal/manage/users", "/admin/users"}:
        return "User Management", "NeoApps"
    if path.startswith("/portal/manage/access-requests") or path.startswith("/admin/access-requests"):
        return "Access Requests", "NeoApps"
    if path.startswith("/portal/manage"):
        return "Portal Management", "NeoApps"
    if path == "/admin/permissions" or path.startswith("/motherbrain/permissions"):
        return "Permission Rules", "NeoApps"

    if path == "/nodes":
        return "Node Directory", "NeoApps"
    if path == "/portal":
        return "NeoPortal", "NeoApps"
    if path == "/neobid" or path.startswith("/neobid/"):
        return "NeoBid", "NeoApps"
    if path in {"/", "/login"}:
        return "Sign In", "NeoApps"
    if path == "/create-account":
        return "Create Account", "NeoApps"
    if path.startswith("/verify-email"):
        return "Email Verification", "NeoApps"
    if path == "/access-pending":
        return "Access Review", "NeoApps"
    if path == "/forgot-password":
        return "Forgot Password", "NeoApps"
    if path.startswith("/reset-password"):
        return "Reset Password", "NeoApps"
    if path == "/change-password":
        return "Change Password", "NeoApps"

    if path == "/rfd" or path.startswith("/rfd/"):
        return "RFD", "NeoGateway"
    if path == "/motherbrain":
        return "Dashboard", "NeoMotherBrain"
    if path.startswith("/motherbrain/parking-plan"):
        return "Parking Plan", "NeoMotherBrain"
    if path.startswith("/motherbrain/parking-rules"):
        return "Parking Rules", "NeoMotherBrain"
    if path.startswith("/motherbrain/gateway-matrix"):
        return "Gateway Matrix", "NeoGateway"
    if path.startswith("/motherbrain/sort-timeline"):
        return "Sort Timeline", "NeoGateway"
    if path.startswith("/motherbrain/master-schedule"):
        return "Master Schedule", "NeoGateway"
    if path.startswith("/motherbrain/flight-api-test"):
        return "Manage API", "NeoGateway"
    if path.startswith("/motherbrain/flight-api-review"):
        return "Unmatched Queue", "NeoGateway"
    if path.endswith("/arrivals") or "/alp/arrival" in path:
        return "Arrival Planning", "NeoGateway"
    if path.endswith("/departures") or "/alp/departure" in path:
        return "Departure Planning", "NeoGateway"
    if path.startswith("/motherbrain/manage-sort") or path.startswith("/motherbrain/operations"):
        return "Manage Sort", "NeoGateway"

    if path == "/neoermac":
        return "Dashboard", "NeoErmac"
    if path.startswith("/neoermac/building-lineup"):
        return "Building Lineup", "NeoErmac"
    if path.startswith("/neoermac/door-view"):
        return "Door View", "NeoErmac"
    if path.startswith("/neoermac/outbound") or path.startswith("/neoermac/view-outbound"):
        return "Outbound", "NeoErmac"
    if path.startswith("/neoermac/upcoming-pulls"):
        return "Upcoming Pulls", "NeoErmac"
    if path.startswith("/neoermac/tug-assignments"):
        return "Tug Assignments", "NeoErmac"

    if path == "/neosektor":
        return "Dashboard", "NeoSektor"
    if path.startswith("/neosektor/tunnel-conductor"):
        return "Tunnel Conductor", "NeoSektor"
    if path.startswith("/neosektor/live-counts"):
        return "Live Counts", "NeoSektor"
    if path.startswith("/neosektor/ebm") or (
        path.startswith("/neosektor/ballmat") and ballmat_side.lower() == "east"
    ):
        return "EBM", "NeoSektor"
    if path.startswith("/neosektor/wbm") or (
        path.startswith("/neosektor/ballmat") and ballmat_side.lower() == "west"
    ):
        return "WBM", "NeoSektor"
    if path.startswith("/neosektor/ballmat"):
        return "Ballmat", "NeoSektor"
    if path.startswith("/neosektor/discharge"):
        return "Discharge", "NeoSektor"
    if path.startswith("/neosektor/driver-routing"):
        return "Driver Routing", "NeoSektor"

    if path == "/neoscorpion":
        return "Dashboard", "NeoScorpion"
    if path.startswith("/neoscorpion/fuel-dispatch"):
        return "Fuel Dispatch", "NeoScorpion"
    if path.startswith("/neoscorpion/fueler"):
        return "Fueler", "NeoScorpion"
    if path.startswith("/neoscorpion/truck-manager"):
        return "Truck Manager", "NeoScorpion"
    if path.startswith("/neoscorpion/history") or path.startswith("/neoscorpion/completed-fuel"):
        return "Fuel History", "NeoScorpion"
    if path.startswith("/neoscorpion/settings"):
        return "Settings", "NeoScorpion"

    if path == "/neostaffing":
        return "Home", "NeoStaffing"
    if path.startswith("/neostaffing/people/attendance") or path.startswith("/neostaffing/attendance"):
        return "Attendance", "NeoStaffing"
    if path.startswith("/neostaffing/people"):
        return "People", "NeoStaffing"
    if path.startswith("/neostaffing/org-chart") or path.startswith("/neostaffing/app-management/hierarchy"):
        return "Org Chart", "NeoStaffing"
    if path.startswith("/neostaffing/reports"):
        return "Reports", "NeoStaffing"
    if path.startswith("/neostaffing/seniority"):
        return "Seniority", "NeoStaffing"
    if path.startswith("/neostaffing"):
        return "NeoStaffing", "NeoStaffing"

    for prefix, owner in (
        ("/neoreptile", "NeoReptile"),
        ("/neosubzero", "NeoSub-Zero"),
        ("/neosub-zero", "NeoSub-Zero"),
        ("/neo-sub-zero", "NeoSub-Zero"),
        ("/neorain", "NeoRain"),
        ("/neoraiden", "NeoRaiden"),
    ):
        if path == prefix or path.startswith(f"{prefix}/"):
            return "Dashboard", owner

    return "NeoApps", "NeoApps"
