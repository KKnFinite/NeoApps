from flask import flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import (
    NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroPretreatState,
    SortDateMission,
)
from app.neonodes.neosubzero import bp
from app.neonodes.neosubzero.services import (
    PRETREAT_REFRESH_KEY, SURFACE_LABELS, NeoSubZeroPretreatError,
    current_neosubzero_operation, mutate_pretreat, pretreat_context,
    pretreat_refresh_status, pretreat_revision, subzero_refresh_status,
)
from app.services.neosubzero_departure_deice import (
    COORDINATOR_REFRESH_KEY,
    OUTBOUND_REFRESH_KEY,
    PLAN_LABELS,
    RAMP_ORDER,
    NeoSubZeroDepartureDeiceError,
    departure_deice_context,
    departure_deice_revision,
    mutate_departure_deice,
    neosubzero_fluid_settings,
    set_neosubzero_fluid_settings,
)
from app.services.access_control import get_current_gateway
from app.services.live_collaboration import version_conflict
from app.services.live_screen_refresh import (
    LIVE_SCREEN_REFRESH_ALLOWED_SECONDS,
    live_screen_refresh_values,
    save_live_screen_refresh_override,
)
from app.services.gateway_matrix import gateway_timezone
from app.services.permission_rules import permission_access, user_can

LAST_PAGE_KEY = "neosubzero.last_page"
NEOSUBZERO_PAGES = (
    (
        "Pretreat",
        "neosubzero.pretreat",
        "neosubzero.pretreat.view",
        "neosubzero.pretreat.edit",
    ),
    (
        "Outbound",
        "neosubzero.outbound",
        "neosubzero.outbound.view",
        "neosubzero.outbound.edit",
    ),
    (
        "Coordinator",
        "neosubzero.coordinator",
        "neosubzero.coordinator.view",
        "neosubzero.coordinator.edit",
    ),
    (
        "Settings",
        "neosubzero.settings",
        "neosubzero.settings.view",
        "neosubzero.settings.edit",
    ),
)
REFRESH_KEYS = (PRETREAT_REFRESH_KEY, OUTBOUND_REFRESH_KEY, COORDINATOR_REFRESH_KEY)


@bp.context_processor
def navigation():
    return {
        "neosubzero_menu_items": lambda: [
            (label, endpoint)
            for label, endpoint, view_permission, _edit_permission in NEOSUBZERO_PAGES
            if user_can(view_permission)
        ]
    }


@bp.route("")
@bp.route("/")
@gateway_node_required("subzero")
def index():
    endpoint = session.get(LAST_PAGE_KEY)
    permitted_endpoints = [
        page[1] for page in NEOSUBZERO_PAGES if user_can(page[2])
    ]
    if endpoint not in permitted_endpoints:
        endpoint = permitted_endpoints[0] if permitted_endpoints else None
    if endpoint is None:
        flash("Access denied.", "error")
        return redirect(url_for("neomotherbrain.rfd_hub"))
    return redirect(url_for(endpoint))


@bp.route("/pretreat")
@gateway_node_required("subzero")
def pretreat():
    access = permission_access("neosubzero.pretreat.view", "neosubzero.pretreat.edit")
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosubzero.index"))
    session[LAST_PAGE_KEY] = "neosubzero.pretreat"
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    return render_template(
        "neonodes/neosubzero/pretreat.html",
        gateway=gateway,
        can_edit=access["can_edit"],
        revision=pretreat_revision(gateway, operation),
        refresh_status=pretreat_refresh_status(gateway, operation),
        gateway_timezone=gateway_timezone(gateway),
        surface_labels=SURFACE_LABELS,
        **pretreat_context(gateway, operation),
    )


@bp.route("/pretreat/revision")
@gateway_node_required("subzero")
def pretreat_revision_endpoint():
    if not user_can("neosubzero.pretreat.view"):
        return jsonify({"ok": False, "error": "Access denied."}), 403
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    revision = pretreat_revision(gateway, operation)
    return jsonify(
        {
            "ok": True,
            "revision": revision,
            "changed": revision != str(request.args.get("revision") or ""),
            "refresh": pretreat_refresh_status(gateway, operation),
        }
    )


@bp.route("/outbound")
@gateway_node_required("subzero")
def outbound():
    access = permission_access("neosubzero.outbound.view", "neosubzero.outbound.edit")
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosubzero.index"))
    session[LAST_PAGE_KEY] = "neosubzero.outbound"
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    context = departure_deice_context(gateway, operation)
    return render_template(
        "neonodes/neosubzero/outbound.html",
        gateway=gateway,
        can_edit=access["can_edit"],
        plan_labels=PLAN_LABELS,
        surface_labels=SURFACE_LABELS,
        revision=departure_deice_revision(gateway, operation),
        refresh_status=subzero_refresh_status(
            gateway, operation, OUTBOUND_REFRESH_KEY
        ),
        gateway_timezone=gateway_timezone(gateway),
        **context,
    )


@bp.route("/outbound/revision")
@gateway_node_required("subzero")
def outbound_revision_endpoint():
    return _departure_revision_response(
        "neosubzero.outbound.view", OUTBOUND_REFRESH_KEY
    )


@bp.route("/coordinator")
@gateway_node_required("subzero")
def coordinator():
    access = permission_access(
        "neosubzero.coordinator.view", "neosubzero.coordinator.edit"
    )
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosubzero.index"))
    session[LAST_PAGE_KEY] = "neosubzero.coordinator"
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    context = departure_deice_context(gateway, operation)
    coordinator_state = _coordinator_workspace_state(operation, context["rows"])
    return render_template(
        "neonodes/neosubzero/coordinator.html",
        gateway=gateway,
        can_edit=access["can_edit"],
        plan_labels=PLAN_LABELS,
        surface_labels=SURFACE_LABELS,
        revision=departure_deice_revision(gateway, operation),
        refresh_status=subzero_refresh_status(
            gateway, operation, COORDINATOR_REFRESH_KEY
        ),
        gateway_timezone=gateway_timezone(gateway),
        **context,
        **coordinator_state,
    )


@bp.route("/coordinator/revision")
@gateway_node_required("subzero")
def coordinator_revision_endpoint():
    return _departure_revision_response(
        "neosubzero.coordinator.view", COORDINATOR_REFRESH_KEY
    )


@bp.route("/departure-deice/mutate", methods=["POST"])
@gateway_node_required("subzero")
def departure_deice_mutate():
    board = str(request.form.get("board") or "").strip().lower()
    endpoint, permission = {
        "outbound": ("neosubzero.outbound", "neosubzero.outbound.edit"),
        "coordinator": (
            "neosubzero.coordinator",
            "neosubzero.coordinator.edit",
        ),
    }.get(board, (None, None))
    if permission is None or not user_can(permission):
        return "Access denied.", 403
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    mission = (
        SortDateMission.query.filter_by(
            id=request.form.get("mission_id", type=int),
            sort_date_operation_id=getattr(operation, "id", None),
            mission_type="departure",
        ).one_or_none()
        if operation
        else None
    )
    coordinator_return_to_list = False
    try:
        event = NeoSubZeroDepartureDeiceEvent.query.filter_by(
            sort_date_mission_id=getattr(mission, "id", None),
            sort_date_operation_id=getattr(operation, "id", None),
        ).with_for_update().one_or_none()
        expected_version = str(request.form.get("expected_version") or "").strip()
        if event and (
            not expected_version or version_conflict(event, expected_version)
        ):
            raise NeoSubZeroDepartureDeiceError(
                "Departure deice changed while you were editing. Review current values."
            )
        event = mutate_departure_deice(
            operation,
            mission,
            request.form.get("action"),
            request.form,
            event=event,
        )
        db.session.commit()
        flash("DEPARTURE DEICE SAVED.", "success")
        if board == "coordinator":
            coordinator_return_to_list = event.status in {
                "cleared",
                "negative",
                "not_sprayed",
            }
            _remember_coordinator_mission(
                operation,
                None if coordinator_return_to_list else mission.id,
            )
    except (NeoSubZeroDepartureDeiceError, IntegrityError) as exc:
        db.session.rollback()
        flash(
            str(exc)
            if isinstance(exc, NeoSubZeroDepartureDeiceError)
            else "Unable to save departure deice.",
            "error",
        )
    query = {}
    if mission and not (board == "coordinator" and coordinator_return_to_list):
        query["mission"] = mission.id
    if board == "coordinator":
        query["ramp"] = request.form.get("ramp")
    return redirect(url_for(endpoint, **query))


@bp.route("/pretreat/mutate", methods=["POST"])
@gateway_node_required("subzero")
def pretreat_mutate():
    if not user_can("neosubzero.pretreat.edit"):
        return "Access denied.", 403
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    if operation is None:
        flash("No current sort.", "error")
        return redirect(url_for("neosubzero.pretreat"))
    mission = SortDateMission.query.filter_by(
        id=request.form.get("mission_id", type=int),
        sort_date_operation_id=operation.id,
        mission_type="departure",
    ).one_or_none()
    tail = str(getattr(mission, "assigned_tail_number", "") or "").strip().upper()
    try:
        state = NeoSubZeroPretreatState.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number=tail,
        ).with_for_update().one_or_none()
        if state and version_conflict(state, request.form.get("expected_version")):
            raise NeoSubZeroPretreatError(
                "Pretreat changed while you were editing. Review the current values."
            )
        mutate_pretreat(
            operation,
            mission,
            request.form.get("action"),
            request.form,
            state=state,
        )
        db.session.commit()
        flash("PRETREAT SAVED.", "success")
    except (NeoSubZeroPretreatError, IntegrityError) as exc:
        db.session.rollback()
        message = (
            str(exc)
            if isinstance(exc, NeoSubZeroPretreatError)
            else "Unable to save Pretreat."
        )
        flash(message, "error")
    return redirect(
        url_for("neosubzero.pretreat", mission=mission.id if mission else None)
    )


@bp.route("/settings", methods=["GET", "POST"])
@gateway_node_required("subzero")
def settings():
    access = permission_access("neosubzero.settings.view", "neosubzero.settings.edit")
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosubzero.index"))
    gateway = get_current_gateway()
    session[LAST_PAGE_KEY] = "neosubzero.settings"
    if request.method == "POST":
        if not access["can_edit"]:
            return "Access denied.", 403
        try:
            action = request.form.get("action")
            if action == "save_refresh":
                result = save_live_screen_refresh_override(
                    gateway,
                    request.form.get("screen_key"),
                    request.form.get("refresh_interval_seconds"),
                    allowed_screen_keys=REFRESH_KEYS,
                )
                changed = result.changed
                message = "REFRESH SETTING SAVED."
            elif action == "save_fluids":
                set_neosubzero_fluid_settings(
                    gateway,
                    request.form.get("type_i_fluid_name"),
                    request.form.get("type_i_concentration_percent"),
                    request.form.get("type_iv_fluid_name"),
                )
                changed = True
                message = "DEICE FLUID SETTINGS SAVED."
            else:
                raise ValueError("Choose a valid Settings action.")
            if changed:
                db.session.commit()
            else:
                db.session.rollback()
            flash(message, "success")
        except (ValueError, NeoSubZeroDepartureDeiceError, IntegrityError) as exc:
            db.session.rollback()
            flash(
                "Unable to save NeoSub-Zero settings."
                if isinstance(exc, IntegrityError)
                else str(exc),
                "error",
            )
        return redirect(url_for("neosubzero.settings"))
    return render_template(
        "neonodes/neosubzero/settings.html",
        can_edit=access["can_edit"],
        refresh_settings=live_screen_refresh_values(gateway, REFRESH_KEYS),
        refresh_rows=(
            ("Pretreat", PRETREAT_REFRESH_KEY),
            ("Outbound", OUTBOUND_REFRESH_KEY),
            ("Coordinator", COORDINATOR_REFRESH_KEY),
        ),
        fluid_settings=neosubzero_fluid_settings(gateway),
        choices=LIVE_SCREEN_REFRESH_ALLOWED_SECONDS,
    )


def _departure_revision_response(permission, screen_key):
    if not user_can(permission):
        return jsonify({"ok": False, "error": "Access denied."}), 403
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    revision = departure_deice_revision(gateway, operation)
    response = jsonify(
        {
            "ok": True,
            "revision": revision,
            "changed": revision != str(request.args.get("revision") or ""),
            "refresh": subzero_refresh_status(gateway, operation, screen_key),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _coordinator_workspace_state(operation, rows):
    if operation is None:
        return {
            "ramps": [],
            "selected_ramp": None,
            "selected_mission_id": None,
            "selected_rows": [],
            "completed_rows": [],
        }
    prefix = f"neosubzero.coordinator.{operation.id}"
    present_ramps = {row["ramp"] for row in rows if row["ramp"] in RAMP_ORDER}
    active_ramps = {
        row["ramp"]
        for row in rows
        if row["ramp"] in RAMP_ORDER and not row["terminal"]
    }
    remembered = set(session.get(f"{prefix}.ramps", ()))
    known_ramps = [ramp for ramp in RAMP_ORDER if ramp in present_ramps | remembered]
    if known_ramps != list(session.get(f"{prefix}.ramps", ())):
        session[f"{prefix}.ramps"] = known_ramps
    requested_ramp = str(request.args.get("ramp") or "").strip().title()
    selected_ramp = requested_ramp or session.get(f"{prefix}.ramp")
    if selected_ramp not in known_ramps:
        selected_ramp = next(
            (ramp for ramp in known_ramps if ramp in active_ramps),
            known_ramps[0] if known_ramps else None,
        )
    session[f"{prefix}.ramp"] = selected_ramp
    selected_rows = [row for row in rows if row["ramp"] == selected_ramp]
    requested_mission = request.args.get("mission", type=int)
    selected_mission_id = requested_mission or session.get(f"{prefix}.mission")
    if selected_mission_id not in {row["mission_id"] for row in selected_rows}:
        selected_mission_id = None
    session[f"{prefix}.mission"] = selected_mission_id
    return {
        "ramps": [
            {"name": ramp, "active": ramp in active_ramps} for ramp in known_ramps
        ],
        "selected_ramp": selected_ramp,
        "selected_mission_id": selected_mission_id,
        "selected_rows": [row for row in selected_rows if not row["terminal"]],
        "completed_rows": [row for row in selected_rows if row["terminal"]],
    }


def _remember_coordinator_mission(operation, mission_id):
    if operation:
        session[f"neosubzero.coordinator.{operation.id}.mission"] = mission_id
