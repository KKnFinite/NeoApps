from flask import flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import SortDateOperation
from app.models.user import MANAGEMENT_LEVELS
from app.neonodes.neosektor import bp
from app.services.access_control import (
    access_initialization_changed_this_request,
    get_current_gateway,
)
from app.services.neosektor_live_counts import (
    NeoSektorOperationalStateBundle,
    TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    TUNNEL_CONDUCTOR_VIEW_PERMISSION,
    adjust_tunnel_wave_arrivals,
    ballmat_operations_context,
    ballmat_state_payload,
    driver_routing_context,
    driver_routing_refresh_status,
    driver_routing_state_payload,
    live_counts_context,
    NEOSEKTOR_DISCHARGE_REFRESH_KEY,
    NEOSEKTOR_DRIVER_ROUTING_REFRESH_KEY,
    NEOSEKTOR_EBM_REFRESH_KEY,
    NEOSEKTOR_LIVE_COUNTS_REFRESH_KEY,
    NEOSEKTOR_REFRESH_KEYS,
    NEOSEKTOR_TUNNEL_CONDUCTOR_REFRESH_KEY,
    NEOSEKTOR_WBM_REFRESH_KEY,
    neosektor_refresh_status,
    normalize_ballmat_side,
    tunnel_conductor_context,
    update_neosektor_operational_settings,
    update_tunnel_driver_offset,
    update_ballmat_side,
)
from app.services.neosektor_live_refresh import (
    COUNT_STATE_SCOPE,
    ROUTING_STATE_SCOPE,
    neosektor_discharge_revision,
    neosektor_state_revision,
)
from app.services.neosektor_sheets_compat import (
    NEO_PRIMARY_GOOGLE_MIRROR,
    NeoSektorGoogleError,
    mirror_neosektor_operational_values,
    neosektor_integration_status,
)
from app.services.permission_rules import preload_permission_rules, user_can
from app.services.memory_diagnostics import memory_diagnostics
from app.services.live_screen_refresh import (
    LIVE_SCREEN_REFRESH_ALLOWED_SECONDS,
    live_screen_refresh_values,
    save_live_screen_refresh_override,
)
from app.services import neostaffing as staffing_service
from app.services.uld_requests import (
    discharge_context,
    discharge_state_payload,
    send_uld_totals_on_the_way,
    send_uld_on_the_way,
)


EBM_VIEW_PERMISSION = "neosektor.ebm.view"
EBM_EDIT_PERMISSION = "neosektor.ebm.edit"
WBM_VIEW_PERMISSION = "neosektor.wbm.view"
WBM_EDIT_PERMISSION = "neosektor.wbm.edit"
LIVE_COUNTS_VIEW_PERMISSION = "neosektor.live_counts.view"
NEOSEKTOR_DASHBOARD_VIEW_PERMISSION = "neosektor.dashboard.view"
NEOSEKTOR_SETTINGS_VIEW_PERMISSION = "neosektor.settings.view"
NEOSEKTOR_SETTINGS_EDIT_PERMISSION = "neosektor.settings.edit"

NEOSEKTOR_PAGES = (
    (
        "TUNNEL CONDUCTOR",
        "neosektor.tunnel_conductor",
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
        "Tunnel Conductor live count controls.",
    ),
    (
        "SETTINGS",
        "neosektor.settings",
        NEOSEKTOR_SETTINGS_VIEW_PERMISSION,
        NEOSEKTOR_SETTINGS_EDIT_PERMISSION,
        "NeoSektor application settings.",
    ),
    (
        "EBM",
        "neosektor.ebm",
        EBM_VIEW_PERMISSION,
        EBM_EDIT_PERMISSION,
        "East Ballmat Operations count entry.",
    ),
    (
        "WBM",
        "neosektor.wbm",
        WBM_VIEW_PERMISSION,
        WBM_EDIT_PERMISSION,
        "West Ballmat Operations count entry.",
    ),
    (
        "DISCHARGE",
        "neosektor.discharge",
        "neosektor.discharge.view",
        "neosektor.discharge.edit",
        "NeoSektor ULD request discharge queue.",
    ),
    (
        "DRIVER ROUTING",
        "neosektor.driver_routing",
        "neosektor.driver_routing.view",
        None,
        "Driver routing foundation.",
    ),
)

NEOSEKTOR_INTERNAL_MENU = (
    ("Live Counts", "neosektor.live_counts", LIVE_COUNTS_VIEW_PERMISSION),
    ("Settings", "neosektor.settings", NEOSEKTOR_SETTINGS_VIEW_PERMISSION),
    ("Tunnel Conductor", "neosektor.tunnel_conductor", TUNNEL_CONDUCTOR_VIEW_PERMISSION),
    ("East Ballmat", "neosektor.ebm", EBM_VIEW_PERMISSION),
    ("West Ballmat", "neosektor.wbm", WBM_VIEW_PERMISSION),
    ("Driver Routing", "neosektor.driver_routing", "neosektor.driver_routing.view"),
    ("Discharge", "neosektor.discharge", "neosektor.discharge.view"),
)


NEOSEKTOR_MOBILE_DASHBOARD = (
    (
        "Live Counts",
        "neosektor.live_counts",
        LIVE_COUNTS_VIEW_PERMISSION,
        "live-counts",
        "Live flow and bay status.",
    ),
    (
        "Tunnel Conductor",
        "neosektor.tunnel_conductor",
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        "tunnel",
        "Tunnel counts and down timer.",
    ),
    (
        "Settings",
        "neosektor.settings",
        NEOSEKTOR_SETTINGS_VIEW_PERMISSION,
        "settings",
        "NeoSektor application settings.",
    ),
    (
        "EBM",
        "neosektor.ebm",
        EBM_VIEW_PERMISSION,
        "ebm",
        "East ballmat count entry.",
    ),
    (
        "WBM",
        "neosektor.wbm",
        WBM_VIEW_PERMISSION,
        "wbm",
        "West ballmat count entry.",
    ),
    (
        "Discharge",
        "neosektor.discharge",
        "neosektor.discharge.view",
        "discharge",
        "ULD request queue.",
    ),
    (
        "Driver Routing",
        "neosektor.driver_routing",
        "neosektor.driver_routing.view",
        "driver-routing",
        "Driver need and route board.",
    ),
)


@bp.context_processor
def inject_neosektor_navigation():
    return {
        "neosektor_internal_menu_items": _visible_neosektor_menu_items,
    }


@bp.route("")
@gateway_node_required("sektor")
def index():
    if not user_can(NEOSEKTOR_DASHBOARD_VIEW_PERMISSION):
        flash("Access denied.", "error")
        return redirect(url_for("neomotherbrain.rfd_hub"))

    gateway = get_current_gateway()
    return render_template(
        "neonodes/neosektor/index.html",
        gateway=gateway,
        can_view=True,
        menu_items=_visible_neosektor_page_items(),
        mobile_dashboard_items=_visible_neosektor_mobile_dashboard_items(),
    )


@bp.route("/")
@gateway_node_required("sektor")
def index_slash():
    return redirect(url_for("neosektor.index"))


@bp.route("/tunnel-conductor")
@gateway_node_required("sektor")
def tunnel_conductor():
    access = _neosektor_access(
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    )
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))

    gateway = get_current_gateway()
    try:
        refresh_status = neosektor_refresh_status(
            gateway,
            screen_key=NEOSEKTOR_TUNNEL_CONDUCTOR_REFRESH_KEY,
        )
        bundle = NeoSektorOperationalStateBundle.load(
            gateway,
            include_routing=True,
            refresh_status=refresh_status,
        )
        context = tunnel_conductor_context(gateway, bundle=bundle)
    except NeoSektorGoogleError as exc:
        flash(str(exc), "error")
        return redirect(url_for("neosektor.index"))
    context["live_revision"] = neosektor_state_revision(
        gateway,
        ROUTING_STATE_SCOPE,
    )
    _commit_neosektor_initialization_if_changed(bundle)
    return render_template(
        "neonodes/neosektor/tunnel_conductor.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        can_manage_employees=_can_manage_employees(),
        **context,
    )


@bp.route("/tunnel-conductor/state")
@gateway_node_required("sektor")
def tunnel_conductor_state():
    access = _neosektor_access(
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    )
    if not access["can_view"]:
        return jsonify({"ok": False, "error": "Access denied."}), 403

    gateway = get_current_gateway()
    try:
        return _neosektor_live_state_response(
            gateway,
            ROUTING_STATE_SCOPE,
            driver_routing_state_payload,
            screen_key=NEOSEKTOR_TUNNEL_CONDUCTOR_REFRESH_KEY,
        )
    except NeoSektorGoogleError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@bp.route("/tunnel-conductor/wave", methods=["POST"])
@gateway_node_required("sektor")
def tunnel_conductor_wave():
    access = _neosektor_access(
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        return jsonify({"ok": False, "error": "Edit access denied."}), 403

    payload = request.get_json(silent=True) or request.form
    try:
        gateway = get_current_gateway()
        bundle, before_values, warning_pending = _neosektor_write_bundle(
            gateway,
            include_routing=True,
        )
        state = adjust_tunnel_wave_arrivals(
            gateway,
            payload.get("wave"),
            payload.get("delta"),
            value=payload.get("value") if "value" in payload else None,
            bundle=bundle,
        )
    except NeoSektorGoogleError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 502
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    _commit_neosektor_update_and_mirror(
        bundle,
        before_values,
        warning_pending,
    )
    return jsonify({"ok": True, "state": state})


@bp.route("/tunnel-conductor/offset", methods=["POST"])
@gateway_node_required("sektor")
def tunnel_conductor_offset():
    access = _neosektor_access(
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        return jsonify({"ok": False, "error": "Edit access denied."}), 403

    payload = request.get_json(silent=True) or request.form
    gateway = get_current_gateway()
    bundle, before_values, warning_pending = _neosektor_write_bundle(
        gateway,
        include_routing=True,
    )
    state = update_tunnel_driver_offset(gateway, payload, bundle=bundle)
    _commit_neosektor_update_and_mirror(
        bundle,
        before_values,
        warning_pending,
    )
    return jsonify({"ok": True, "state": state})


@bp.route("/tunnel-conductor/settings", methods=["POST"])
@gateway_node_required("sektor")
def tunnel_conductor_settings():
    access = _neosektor_access(
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        return jsonify({"ok": False, "error": "Edit access denied."}), 403

    payload = request.get_json(silent=True) or request.form
    gateway = get_current_gateway()
    bundle, before_values, warning_pending = _neosektor_write_bundle(
        gateway,
        include_routing=True,
    )
    state = update_neosektor_operational_settings(
        gateway,
        payload,
        bundle=bundle,
    )
    _commit_neosektor_update_and_mirror(
        bundle,
        before_values,
        warning_pending,
    )
    return jsonify({"ok": True, "state": state})


@bp.route("/tunnel-conductor/ballmat", methods=["POST"])
@gateway_node_required("sektor")
def tunnel_conductor_ballmat():
    access = _neosektor_access(
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        return jsonify({"ok": False, "error": "Edit access denied."}), 403

    payload = request.get_json(silent=True) or request.form
    side = normalize_ballmat_side((payload or {}).get("side"))
    if not side:
        return jsonify({"ok": False, "error": "Invalid side."}), 400

    try:
        gateway = get_current_gateway()
        bundle, before_values, warning_pending = _neosektor_write_bundle(
            gateway,
            include_routing=True,
        )
        state = update_ballmat_side(
            gateway,
            side,
            payload,
            bundle=bundle,
            include_routing_state=True,
        )
    except NeoSektorGoogleError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 502
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    _commit_neosektor_update_and_mirror(
        bundle,
        before_values,
        warning_pending,
    )
    return jsonify({"ok": True, "state": state})


@bp.route("/ebm")
@gateway_node_required("sektor")
def ebm():
    return _render_ballmat_operations("east")


@bp.route("/wbm")
@gateway_node_required("sektor")
def wbm():
    return _render_ballmat_operations("west")


@bp.route("/ballmat")
@gateway_node_required("sektor")
def ballmat_operations():
    selected_side = _selected_ballmat_side()
    return redirect(_ballmat_route_for_side(selected_side))


def _render_ballmat_operations(selected_side):
    access = _ballmat_access(selected_side)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))

    session["neosektor_ballmat_side"] = selected_side
    gateway = get_current_gateway()
    try:
        screen_key = (
            NEOSEKTOR_WBM_REFRESH_KEY
            if selected_side == "west"
            else NEOSEKTOR_EBM_REFRESH_KEY
        )
        bundle = NeoSektorOperationalStateBundle.load(
            gateway,
            refresh_status=neosektor_refresh_status(gateway, screen_key=screen_key),
        )
        context = ballmat_operations_context(
            gateway,
            selected_side,
            bundle=bundle,
        )
    except NeoSektorGoogleError as exc:
        flash(str(exc), "error")
        return redirect(url_for("neosektor.index"))
    context["live_revision"] = neosektor_state_revision(
        gateway,
        COUNT_STATE_SCOPE,
    )
    _commit_neosektor_initialization_if_changed(bundle)
    return render_template(
        "neonodes/neosektor/ballmat.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        **context,
    )


@bp.route("/ballmat/state")
@gateway_node_required("sektor")
def ballmat_state():
    if not _can_view_any_ballmat():
        return jsonify({"ok": False, "error": "Access denied."}), 403

    gateway = get_current_gateway()
    try:
        return _neosektor_live_state_response(
            gateway,
            COUNT_STATE_SCOPE,
            ballmat_state_payload,
            screen_key=(
                NEOSEKTOR_WBM_REFRESH_KEY
                if _selected_ballmat_side() == "west"
                else NEOSEKTOR_EBM_REFRESH_KEY
            ),
        )
    except NeoSektorGoogleError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@bp.route("/ballmat/update", methods=["POST"])
@gateway_node_required("sektor")
def ballmat_update():
    selected_side = _selected_ballmat_side()
    access = _ballmat_access(selected_side)
    if not access["can_edit"]:
        return jsonify({"ok": False, "error": "Edit access denied."}), 403

    payload = request.get_json(silent=True) or request.form.to_dict(flat=False)
    try:
        gateway = get_current_gateway()
        bundle, before_values, warning_pending = _neosektor_write_bundle(gateway)
        state = update_ballmat_side(
            gateway,
            selected_side,
            payload,
            bundle=bundle,
        )
    except NeoSektorGoogleError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 502
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403

    session["neosektor_ballmat_side"] = selected_side
    _commit_neosektor_update_and_mirror(
        bundle,
        before_values,
        warning_pending,
    )
    return jsonify({"ok": True, "state": state})


@bp.route("/discharge")
@gateway_node_required("sektor")
def discharge():
    page = _page_by_title("DISCHARGE")
    access = _neosektor_access(page["view_permission"], page["edit_permission"])
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))

    gateway = get_current_gateway()
    context = discharge_context(gateway)
    context["refresh_status"] = neosektor_refresh_status(
        gateway,
        screen_key=NEOSEKTOR_DISCHARGE_REFRESH_KEY,
    )
    context["live_revision"] = neosektor_discharge_revision(
        gateway,
        operation_id=(context["operation"].id if context["operation"] else None),
    )
    selected_request_id = request.args.get("request_id", type=int)
    selected_request = next(
        (
            request_row
            for request_row in context["requests"]
            if request_row.get("id") == selected_request_id
        ),
        None,
    )
    if access_initialization_changed_this_request():
        db.session.commit()
    return render_template(
        "neonodes/neosektor/discharge.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        selected_request=selected_request,
        **context,
    )


@bp.route("/discharge/state")
@gateway_node_required("sektor")
def discharge_state():
    page = _page_by_title("DISCHARGE")
    access = _neosektor_access(page["view_permission"], page["edit_permission"])
    if not access["can_view"]:
        return jsonify({"ok": False, "error": "Access denied."}), 403

    return _neosektor_discharge_state_response(get_current_gateway())


@bp.route("/discharge/send", methods=["POST"])
@gateway_node_required("sektor")
def discharge_send():
    page = _page_by_title("DISCHARGE")
    access = _neosektor_access(page["view_permission"], page["edit_permission"])
    if not access["can_edit"]:
        if request.is_json:
            return jsonify({"ok": False, "error": "Edit access denied."}), 403
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.discharge"))

    payload = request.get_json(silent=True) if request.is_json else request.form
    try:
        if _has_multi_uld_send_payload(payload):
            events = send_uld_totals_on_the_way(
                get_current_gateway(),
                payload.get("door"),
                {
                    "A2": payload.get("send_a2_count"),
                    "A1": payload.get("send_a1_count"),
                    "AMP": payload.get("send_amp_count"),
                },
                request_id=payload.get("request_id"),
            )
            event = events[0]
        else:
            event = send_uld_on_the_way(
                get_current_gateway(),
                payload.get("door"),
                payload.get("uld_type"),
                payload.get("quantity"),
                request_id=payload.get("request_id"),
            )
    except ValueError as exc:
        db.session.rollback()
        if request.is_json:
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc), "error")
        return redirect(url_for("neosektor.discharge", request_id=payload.get("request_id")))

    db.session.commit()
    if request.is_json:
        gateway = get_current_gateway()
        state = discharge_state_payload(gateway)
        state["refresh"] = neosektor_refresh_status(
            gateway,
            screen_key=NEOSEKTOR_DISCHARGE_REFRESH_KEY,
        )
        return jsonify(
            {
                "ok": True,
                "event": {
                    "door": event.door,
                    "uld_type": event.uld_type,
                    "quantity": event.quantity,
                },
                "state": state,
            }
        )

    flash("ULDs marked on the way.", "success")
    return redirect(url_for("neosektor.discharge"))


@bp.route("/live-counts")
@gateway_node_required("sektor")
def live_counts():
    if not user_can(LIVE_COUNTS_VIEW_PERMISSION):
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))

    gateway = get_current_gateway()
    try:
        bundle = NeoSektorOperationalStateBundle.load(
            gateway,
            refresh_status=neosektor_refresh_status(
                gateway,
                screen_key=NEOSEKTOR_LIVE_COUNTS_REFRESH_KEY,
            ),
        )
        context = live_counts_context(gateway, bundle=bundle)
    except NeoSektorGoogleError as exc:
        flash(str(exc), "error")
        return redirect(url_for("neosektor.index"))
    context["live_revision"] = neosektor_state_revision(
        gateway,
        COUNT_STATE_SCOPE,
    )
    context["can_manage_employees"] = _can_manage_employees()
    context["manage_employees_default_area"] = (
        staffing_service.neosektor_manage_default_area(current_user)
    )
    _commit_neosektor_initialization_if_changed(bundle)
    return render_template(
        "neonodes/neosektor/live_counts.html",
        gateway=gateway,
        can_view=True,
        **context,
    )


def _has_multi_uld_send_payload(payload):
    return any(
        key in payload
        for key in ("send_a2_count", "send_a1_count", "send_amp_count")
    )


@bp.route("/live-counts/state")
@gateway_node_required("sektor")
def live_counts_state():
    if not user_can(LIVE_COUNTS_VIEW_PERMISSION):
        return jsonify({"ok": False, "error": "Access denied."}), 403

    gateway = get_current_gateway()
    try:
        return _neosektor_live_state_response(
            gateway,
            COUNT_STATE_SCOPE,
            ballmat_state_payload,
            screen_key=NEOSEKTOR_LIVE_COUNTS_REFRESH_KEY,
        )
    except NeoSektorGoogleError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@bp.route("/settings", methods=["GET", "POST"])
@gateway_node_required("sektor")
def settings():
    gateway = get_current_gateway()
    access = _neosektor_access(
        NEOSEKTOR_SETTINGS_VIEW_PERMISSION,
        NEOSEKTOR_SETTINGS_EDIT_PERMISSION,
    )
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))

    if request.method == "POST":
        if not access["can_edit"]:
            return "Access denied.", 403
        try:
            if request.form.get("action") != "save_live_refresh":
                raise ValueError("Choose a valid NeoSektor Settings action.")
            result = save_live_screen_refresh_override(
                gateway,
                request.form.get("screen_key"),
                request.form.get("refresh_interval_seconds"),
                allowed_screen_keys=NEOSEKTOR_REFRESH_KEYS,
            )
            if result.changed:
                db.session.commit()
            else:
                db.session.rollback()
            flash("LIVE REFRESH SETTING SAVED.", "success")
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash(
                "Unable to save NeoSektor live refresh settings."
                if isinstance(exc, IntegrityError)
                else str(exc),
                "error",
            )
        return redirect(url_for("neosektor.settings"))

    return _settings_response(gateway, access)


@bp.route("/driver-routing")
@gateway_node_required("sektor")
def driver_routing():
    page = _page_by_title("DRIVER ROUTING")
    access = _neosektor_access(page["view_permission"], page["edit_permission"])
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))

    gateway = get_current_gateway()
    try:
        refresh_status = driver_routing_refresh_status(gateway)
        bundle = NeoSektorOperationalStateBundle.load(
            gateway,
            include_routing=True,
            refresh_status=refresh_status,
        )
        context = driver_routing_context(gateway, bundle=bundle)
    except NeoSektorGoogleError as exc:
        flash(str(exc), "error")
        return redirect(url_for("neosektor.index"))
    context["live_revision"] = neosektor_state_revision(
        gateway,
        ROUTING_STATE_SCOPE,
    )
    _commit_neosektor_initialization_if_changed(bundle)
    return render_template(
        "neonodes/neosektor/driver_routing.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        **context,
    )


@bp.route("/manage-employees", methods=["GET", "POST"])
@gateway_node_required("sektor")
def manage_employees():
    if not _can_manage_employees():
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))
    names = {"dis": "Discharge", "ebm": "East Ballmat", "wbm": "West Ballmat"}
    requested = request.values.get("area", "").casefold()
    if not requested:
        requested = (
            "dis"
            if request.args.get("source") == "tunnel"
            else staffing_service.neosektor_manage_default_area(current_user)
        )
    area = requested if requested in names else "ebm"
    area_ids = staffing_service.attendance_deep_link_work_area_ids([names[area]])
    if request.method == "POST":
        try:
            saved = staffing_service.save_operational_manage_attendance(
                request.form, current_user, area_ids
            )
            db.session.commit()
            flash(f"Attendance saved for {saved} people.", "success")
        except (ValueError, IntegrityError) as exc:
            db.session.rollback()
            flash(str(getattr(exc, "orig", None) or exc), "error")
        return redirect(url_for("neosektor.manage_employees", area=area))
    context = staffing_service.operational_manage_employees_context(area_ids)
    tab_labels = {"dis": "DISCHARGE", "ebm": "EBM", "wbm": "WBM"}
    tabs = tuple(
        {"key": key, "label": tab_labels[key], "selected": key == area}
        for key in names
    )
    return render_template(
        "neostaffing/operational_manage_employees.html",
        title="MANAGE EMPLOYEES",
        attendance=context,
        can_edit_attendance=True,
        show_coming=False,
        area_tabs=tabs,
        attendance_scope_label=names[area].upper(),
        attendance_workspace="sektor",
        back_url=url_for("neosektor.index"),
    )


def _can_manage_employees():
    return bool(
        current_user.is_authenticated
        and current_user.management_level in MANAGEMENT_LEVELS
        and user_can("neostaffing.attendance.take")
    )


@bp.route("/driver-routing/state")
@gateway_node_required("sektor")
def driver_routing_state():
    page = _page_by_title("DRIVER ROUTING")
    access = _neosektor_access(page["view_permission"], page["edit_permission"])
    if not access["can_view"]:
        return jsonify({"ok": False, "error": "Access denied."}), 403

    gateway = get_current_gateway()
    try:
        return _neosektor_live_state_response(
            gateway,
            ROUTING_STATE_SCOPE,
            driver_routing_state_payload,
            screen_key=NEOSEKTOR_DRIVER_ROUTING_REFRESH_KEY,
            refresh_status_resolver=driver_routing_refresh_status,
        )
    except NeoSektorGoogleError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@memory_diagnostics("neosektor_live_state_response")
def _neosektor_live_state_response(
    gateway,
    revision_scope,
    state_builder,
    *,
    screen_key=NEOSEKTOR_LIVE_COUNTS_REFRESH_KEY,
    refresh_status_resolver=neosektor_refresh_status,
):
    client_revision = str(request.args.get("revision") or "").strip()
    refresh = refresh_status_resolver(gateway, screen_key=screen_key)
    if client_revision and not refresh.get("auto_refresh_enabled"):
        return _live_state_json(
            {
                "ok": True,
                "changed": False,
                "revision": client_revision,
                "refresh": refresh,
            }
        )

    revision = neosektor_state_revision(gateway, revision_scope)
    if client_revision and client_revision == revision:
        return _live_state_json(
            {
                "ok": True,
                "changed": False,
                "revision": revision,
                "refresh": refresh,
            }
        )

    state = state_builder(
        gateway,
        initialize=False,
        refresh_status=refresh,
    )
    return _live_state_json(
        {
            "ok": True,
            "changed": True,
            "revision": revision,
            "refresh": refresh,
            "state": state,
        }
    )


def _neosektor_discharge_state_response(gateway):
    client_revision = str(request.args.get("revision") or "").strip()
    refresh = neosektor_refresh_status(
        gateway,
        screen_key=NEOSEKTOR_DISCHARGE_REFRESH_KEY,
    )
    if client_revision and not refresh.get("auto_refresh_enabled"):
        return _live_state_json(
            {
                "ok": True,
                "changed": False,
                "revision": client_revision,
                "refresh": refresh,
            }
        )

    operation_id = refresh.get("operation_id")
    revision = neosektor_discharge_revision(
        gateway,
        operation_id=operation_id,
    )
    if client_revision and client_revision == revision:
        return _live_state_json(
            {
                "ok": True,
                "changed": False,
                "revision": revision,
                "refresh": refresh,
            }
        )

    operation = (
        db.session.get(SortDateOperation, operation_id) if operation_id else None
    )
    state = discharge_state_payload(gateway, operation=operation)
    state["refresh"] = refresh
    return _live_state_json(
        {
            "ok": True,
            "changed": True,
            "revision": revision,
            "refresh": refresh,
            "state": state,
        }
    )


def _live_state_json(payload):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


def _page_by_title(title):
    for label, endpoint, view_permission, edit_permission, description in NEOSEKTOR_PAGES:
        if label == title:
            return {
                "label": label,
                "endpoint": endpoint,
                "view_permission": view_permission,
                "edit_permission": edit_permission,
                "description": description,
            }
    raise ValueError(f"Unknown NeoSektor page: {title}")


def _visible_neosektor_menu_items():
    _preload_neosektor_menu_permissions()
    items = []
    for label, endpoint, view_permission in NEOSEKTOR_INTERNAL_MENU:
        if view_permission and not user_can(view_permission):
            continue
        items.append(
            {
                "label": label,
                "endpoint": endpoint,
                "active": request.endpoint == endpoint,
            }
        )
    return items


def _visible_neosektor_mobile_dashboard_items():
    _preload_neosektor_menu_permissions()
    items = []
    for label, endpoint, view_permission, key, description in NEOSEKTOR_MOBILE_DASHBOARD:
        if view_permission and not user_can(view_permission):
            continue
        items.append(
            {
                "label": label,
                "endpoint": endpoint,
                "key": key,
                "description": description,
                "active": request.endpoint == endpoint,
            }
        )
    return items


def _visible_neosektor_page_items():
    _preload_neosektor_menu_permissions()
    items = []
    for label, endpoint, view_permission, edit_permission, description in NEOSEKTOR_PAGES:
        if view_permission and not user_can(view_permission):
            continue
        items.append((label, endpoint, view_permission, edit_permission, description))
    return items


def _preload_neosektor_menu_permissions():
    preload_permission_rules(
        item[2]
        for menu in (
            NEOSEKTOR_INTERNAL_MENU,
            NEOSEKTOR_MOBILE_DASHBOARD,
            NEOSEKTOR_PAGES,
        )
        for item in menu
        if item[2]
    )


def _selected_ballmat_side():
    requested_side = normalize_ballmat_side(request.args.get("side"))
    session_side = normalize_ballmat_side(session.get("neosektor_ballmat_side"))
    return requested_side or session_side or "east"


def _ballmat_access(side):
    view_permission, edit_permission = _ballmat_permission_keys(side)
    return _neosektor_access(view_permission, edit_permission)


def _neosektor_access(view_permission, edit_permission=None):
    can_view = True if not view_permission else user_can(view_permission)
    can_edit = bool(edit_permission and can_view and user_can(edit_permission))

    return {
        "can_view": can_view,
        "can_edit": can_edit,
    }


def _can_view_any_ballmat():
    for side in ("east", "west"):
        if _ballmat_access(side)["can_view"]:
            return True
    return False


def _ballmat_permission_keys(side):
    selected_side = normalize_ballmat_side(side)
    if selected_side == "west":
        return WBM_VIEW_PERMISSION, WBM_EDIT_PERMISSION
    return EBM_VIEW_PERMISSION, EBM_EDIT_PERMISSION


def _ballmat_route_for_side(side):
    selected_side = normalize_ballmat_side(side)
    endpoint = "neosektor.wbm" if selected_side == "west" else "neosektor.ebm"
    return url_for(endpoint)


def _settings_response(gateway, access, status_code=200):
    response = render_template(
        "neonodes/neosektor/settings.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        integration_status=neosektor_integration_status(gateway),
        refresh_settings=live_screen_refresh_values(gateway, NEOSEKTOR_REFRESH_KEYS),
        refresh_rows=(
            ("Live Counts", NEOSEKTOR_LIVE_COUNTS_REFRESH_KEY),
            ("Tunnel Conductor", NEOSEKTOR_TUNNEL_CONDUCTOR_REFRESH_KEY),
            ("EBM", NEOSEKTOR_EBM_REFRESH_KEY),
            ("WBM", NEOSEKTOR_WBM_REFRESH_KEY),
            ("Discharge", NEOSEKTOR_DISCHARGE_REFRESH_KEY),
            ("Driver Routing", NEOSEKTOR_DRIVER_ROUTING_REFRESH_KEY),
        ),
        live_refresh_allowed_seconds=LIVE_SCREEN_REFRESH_ALLOWED_SECONDS,
    )
    return response, status_code


def _commit_neosektor_initialization_if_changed(bundle):
    if (
        bundle.persistent_state_changed
        or access_initialization_changed_this_request()
    ):
        db.session.commit()


def _neosektor_write_bundle(gateway, *, include_routing=False):
    bundle = NeoSektorOperationalStateBundle.load(
        gateway,
        include_routing=include_routing,
    )
    if bundle.integration_mode != NEO_PRIMARY_GOOGLE_MIRROR:
        return bundle, None, False

    settings = bundle.operational_settings
    warning_pending = bool(
        settings.google_mirror_sync_needed
        or settings.google_mirror_last_error
    )
    return bundle, bundle.operational_cell_values(), warning_pending


def _commit_neosektor_update_and_mirror(
    bundle,
    before_values,
    warning_pending,
):
    """Commit Neo first, then mirror only Mode 2's changed cell values."""
    after_values = (
        bundle.operational_cell_values()
        if before_values is not None
        else None
    )
    db.session.commit()
    if before_values is not None:
        mirror_neosektor_operational_values(
            before_values,
            after_values,
            gateway=bundle.gateway,
            integration_mode=bundle.integration_mode,
            settings=bundle.operational_settings,
            warning_pending=warning_pending,
        )
