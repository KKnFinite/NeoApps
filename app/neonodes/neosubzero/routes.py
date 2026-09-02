from flask import flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import (
    NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroCalloutAssignment,
    NeoSubZeroPretreatState,
    NeoSubZeroUccAssignment,
    NeoSubZeroUccTruckAssignment,
    NeoSubZeroSprayRecord,
    SortDateMission,
    SortDateOperation,
    StaffingPerson,
    StaffingPersonQualification,
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
from app.services.neosubzero_staffing import (
    DEICE_QUALIFICATION_KEY,
    NeoSubZeroStaffingError,
    neosubzero_callout_context,
    neosubzero_qualification_people,
    set_neosubzero_callout_membership,
    set_staffing_person_qualification,
)
from app.services.neosubzero_ucc import (
    UCC_REFRESH_KEY,
    NeoSubZeroUccError,
    move_neosubzero_ucc_assignment,
    neosubzero_ucc_context,
    neosubzero_ucc_revision,
)
from app.services.neosubzero_weather import (
    neosubzero_weather_context,
    neosubzero_weather_revision,
)
from app.services.neosubzero_preferences import (
    neosubzero_frost_risk_explanations_enabled,
    neosubzero_weather_animations_enabled,
    set_neosubzero_frost_risk_explanations_enabled,
    set_neosubzero_weather_animations_enabled,
)
from app.services.neosubzero_spray import (
    DEICER_REFRESH_KEY,
    NeoSubZeroSprayError,
    current_user_ucc_assignment,
    neosubzero_deice_log,
    set_departure_deice_reason,
    set_neosubzero_spray_gallons,
    set_neosubzero_ucc_truck,
)
from app.services.access_control import get_current_gateway
from app.services.live_collaboration import entity_version, version_conflict
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
        "UCC",
        "neosubzero.ucc",
        "neosubzero.ucc.view",
        "neosubzero.ucc.edit",
    ),
    (
        "Deicer Mobile",
        "neosubzero.deicer_mobile",
        "neosubzero.deicer_mobile.view",
        "neosubzero.deicer_mobile.view",
    ),
    (
        "Deice Log",
        "neosubzero.deice_log",
        "neosubzero.deice_log.view",
        "neosubzero.deice_log.view",
    ),
    (
        "Callout Management",
        "neosubzero.callouts",
        "neosubzero.callouts.view",
        "neosubzero.callouts.edit",
    ),
    (
        "Qualifications",
        "neosubzero.qualifications",
        "neosubzero.qualifications.view",
        "neosubzero.qualifications.edit",
    ),
    (
        "Settings",
        "neosubzero.settings",
        "neosubzero.settings.view",
        "neosubzero.settings.edit",
    ),
)
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
        application_context=_application_context(operation),
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
        application_context=_application_context(operation),
        **context,
        **coordinator_state,
    )


@bp.route("/coordinator/revision")
@gateway_node_required("subzero")
def coordinator_revision_endpoint():
    return _departure_revision_response(
        "neosubzero.coordinator.view", COORDINATOR_REFRESH_KEY
    )


@bp.route("/ucc", methods=["GET", "POST"])
@gateway_node_required("subzero")
def ucc():
    access = permission_access("neosubzero.ucc.view", "neosubzero.ucc.edit")
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosubzero.index"))
    session[LAST_PAGE_KEY] = "neosubzero.ucc"
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    if request.method == "POST":
        if not access["can_edit"]:
            return "Access denied.", 403
        try:
            if operation is None:
                raise NeoSubZeroUccError("No current sort is available.")
            locked_operation = SortDateOperation.query.filter_by(
                id=operation.id,
                gateway_code=gateway.code,
            ).with_for_update().one_or_none()
            if locked_operation is None:
                raise NeoSubZeroUccError("The current sort changed. Reload UCC.")
            ramp = str(request.form.get("ramp") or "").strip().title()
            active_ramps = {
                item["name"]
                for item in neosubzero_ucc_context(gateway, locked_operation)["ramps"]
            }
            if ramp not in active_ramps:
                raise NeoSubZeroUccError(
                    "Choose a ramp with current departure aircraft."
                )
            position = request.form.get("position_number", type=int)
            role = str(request.form.get("team_role") or "").strip().casefold()
            assignment = NeoSubZeroUccAssignment.query.filter_by(
                sort_date_operation_id=locked_operation.id,
                ramp=ramp,
                position_number=position,
                team_role=role,
            ).with_for_update().one_or_none()
            expected_version = str(
                request.form.get("expected_version") or ""
            ).strip()
            if assignment and (
                not expected_version
                or version_conflict(assignment, expected_version)
            ):
                raise NeoSubZeroUccError(
                    "UCC staffing changed while you were editing. Review current values."
                )
            person_id = request.form.get("person_id", type=int)
            person = (
                StaffingPerson.query.filter_by(id=person_id, active=True).one_or_none()
                if person_id
                else None
            )
            if person_id and person is None:
                raise NeoSubZeroUccError("Choose an active employee.")
            source_assignment = (
                NeoSubZeroUccAssignment.query.filter(
                    NeoSubZeroUccAssignment.sort_date_operation_id
                    == locked_operation.id,
                    NeoSubZeroUccAssignment.person_id == person.id,
                    NeoSubZeroUccAssignment.id
                    != (getattr(assignment, "id", None) or -1),
                )
                .with_for_update()
                .one_or_none()
                if person is not None
                else None
            )
            submitted_source_id = request.form.get(
                "source_assignment_id", type=int
            )
            source_expected_version = str(
                request.form.get("source_expected_version") or ""
            ).strip()
            if source_assignment is not None and (
                submitted_source_id != source_assignment.id
                or not source_expected_version
                or version_conflict(source_assignment, source_expected_version)
            ):
                raise NeoSubZeroUccError(
                    "The employee's UCC assignment changed while you were moving them. "
                    "Review current values."
                )
            if source_assignment is None and submitted_source_id is not None:
                raise NeoSubZeroUccError(
                    "The employee's UCC assignment changed while you were moving them. "
                    "Review current values."
                )
            result = move_neosubzero_ucc_assignment(
                locked_operation,
                ramp,
                position,
                role,
                person,
                resolution=request.form.get("move_resolution"),
                user_id=current_user.id,
                destination_assignment=assignment,
                source_assignment=source_assignment,
            )
            db.session.commit()
            flash(
                "UCC STAFFING SAVED." if result["changed"] else "UCC STAFFING UNCHANGED.",
                "success" if result["changed"] else "info",
            )
        except (NeoSubZeroUccError, IntegrityError) as exc:
            db.session.rollback()
            flash(
                str(exc)
                if isinstance(exc, NeoSubZeroUccError)
                else "Unable to save UCC staffing.",
                "error",
            )
        return redirect(url_for("neosubzero.ucc"))
    weather = neosubzero_weather_context(gateway, operation)
    return render_template(
        "neonodes/neosubzero/ucc.html",
        gateway=gateway,
        can_edit=access["can_edit"],
        revision=neosubzero_ucc_revision(
            gateway,
            operation,
            weather_revision=weather["revision"],
        ),
        refresh_status=subzero_refresh_status(gateway, operation, UCC_REFRESH_KEY),
        weather=weather,
        weather_animations_enabled=neosubzero_weather_animations_enabled(
            current_user
        ),
        frost_risk_explanations_enabled=(
            neosubzero_frost_risk_explanations_enabled(current_user)
        ),
        application_context=_application_context(operation),
        **neosubzero_ucc_context(gateway, operation),
    )


@bp.route("/ucc/revision")
@gateway_node_required("subzero")
def ucc_revision_endpoint():
    if not user_can("neosubzero.ucc.view"):
        return jsonify({"ok": False, "error": "Access denied."}), 403
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    revision = neosubzero_ucc_revision(
        gateway,
        operation,
        weather_revision=neosubzero_weather_revision(),
    )
    response = jsonify(
        {
            "ok": True,
            "revision": revision,
            "changed": revision != str(request.args.get("revision") or ""),
            "refresh": subzero_refresh_status(gateway, operation, UCC_REFRESH_KEY),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/ucc/weather-preference", methods=["POST"])
@gateway_node_required("subzero")
def ucc_weather_preference():
    if not user_can("neosubzero.ucc.view"):
        return jsonify({"ok": False, "error": "Access denied."}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"enabled"}:
        return jsonify({"ok": False, "error": "Invalid weather preference."}), 400
    try:
        preference = set_neosubzero_weather_animations_enabled(
            current_user,
            payload.get("enabled"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        error = (
            str(exc)
            if isinstance(exc, ValueError)
            else "Unable to save weather preference."
        )
        return jsonify({"ok": False, "error": error}), 400
    return jsonify(
        {
            "ok": True,
            "enabled": bool(preference.weather_animations_enabled),
        }
    )


@bp.route("/ucc/frost-explanation-preference", methods=["POST"])
@gateway_node_required("subzero")
def ucc_frost_explanation_preference():
    if not user_can("neosubzero.ucc.view"):
        return jsonify({"ok": False, "error": "Access denied."}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"enabled"}:
        return jsonify({"ok": False, "error": "Invalid frost explanation preference."}), 400
    try:
        preference = set_neosubzero_frost_risk_explanations_enabled(
            current_user,
            payload.get("enabled"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        error = (
            str(exc)
            if isinstance(exc, ValueError)
            else "Unable to save frost explanation preference."
        )
        return jsonify({"ok": False, "error": error}), 400
    return jsonify(
        {
            "ok": True,
            "enabled": bool(preference.frost_risk_explanations_enabled),
        }
    )


@bp.route("/truck", methods=["POST"])
@gateway_node_required("subzero")
def truck_mutate():
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    board = str(request.form.get("board") or "").strip().casefold()
    ramp = str(request.form.get("ramp") or "").strip().title()
    position = request.form.get("position_number", type=int)
    try:
        if operation is None:
            raise NeoSubZeroSprayError("No current sort is available.")
        locked_operation = SortDateOperation.query.filter_by(
            id=operation.id,
            gateway_code=gateway.code,
        ).with_for_update().one_or_none()
        if locked_operation is None:
            raise NeoSubZeroSprayError("The current sort changed. Reload NeoSub-Zero.")
        if not _can_edit_position(
            board, locked_operation, ramp, position, allow_ucc=True
        ):
            return "Access denied.", 403
        truck = NeoSubZeroUccTruckAssignment.query.filter_by(
            sort_date_operation_id=locked_operation.id,
            ramp=ramp,
            position_number=position,
        ).with_for_update().one_or_none()
        expected_version = str(request.form.get("expected_version") or "").strip()
        if truck and (
            not expected_version or version_conflict(truck, expected_version)
        ):
            raise NeoSubZeroSprayError(
                "Truck assignment changed while you were editing. Review current values."
            )
        set_neosubzero_ucc_truck(
            locked_operation,
            ramp,
            position,
            request.form.get("truck_number"),
            user_id=current_user.id,
            assignment=truck,
        )
        db.session.commit()
        flash("UCC TRUCK SAVED.", "success")
    except (NeoSubZeroSprayError, IntegrityError) as exc:
        db.session.rollback()
        flash(
            str(exc) if isinstance(exc, NeoSubZeroSprayError) else "Unable to save truck.",
            "error",
        )
    return redirect(_subzero_return_url(board, request.form))


@bp.route("/spray-gallons", methods=["POST"])
@gateway_node_required("subzero")
def spray_gallons_mutate():
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    board = str(request.form.get("board") or "").strip().casefold()
    mission_id = request.form.get("mission_id", type=int)
    pass_number = request.form.get("pass_number", type=int)
    position = request.form.get("position_number", type=int)
    try:
        if operation is None:
            raise NeoSubZeroSprayError("No current sort is available.")
        locked_operation = SortDateOperation.query.filter_by(
            id=operation.id,
            gateway_code=gateway.code,
        ).with_for_update().one_or_none()
        if locked_operation is None:
            raise NeoSubZeroSprayError("The current sort changed. Reload NeoSub-Zero.")
        mission = SortDateMission.query.filter_by(
            id=mission_id,
            sort_date_operation_id=locked_operation.id,
            mission_type="departure",
        ).one_or_none()
        event = NeoSubZeroDepartureDeiceEvent.query.filter_by(
            sort_date_operation_id=locked_operation.id,
            sort_date_mission_id=mission_id,
        ).with_for_update().one_or_none()
        row = _departure_row_for_mission(gateway, locked_operation, mission_id)
        if mission is None or event is None or row is None:
            raise NeoSubZeroSprayError("Choose a current departure-deice mission.")
        if not _can_edit_position(board, locked_operation, row["ramp"], position):
            return "Access denied.", 403
        record = NeoSubZeroSprayRecord.query.filter_by(
            departure_deice_event_id=event.id,
            pass_number=pass_number,
            position_number=position,
        ).with_for_update().one_or_none()
        expected_version = str(request.form.get("expected_version") or "").strip()
        if record and (
            not expected_version or version_conflict(record, expected_version)
        ):
            raise NeoSubZeroSprayError(
                "Gallons changed while you were editing. Review current values."
            )
        set_neosubzero_spray_gallons(
            locked_operation,
            mission,
            event,
            pass_number,
            position,
            request.form.get("gallons"),
            fluid_settings=neosubzero_fluid_settings(gateway),
            application_context=_application_context(locked_operation),
            user_id=current_user.id,
            record=record,
        )
        db.session.commit()
        flash("SPRAY GALLONS SAVED.", "success")
    except (NeoSubZeroSprayError, IntegrityError) as exc:
        db.session.rollback()
        flash(
            str(exc) if isinstance(exc, NeoSubZeroSprayError) else "Unable to save gallons.",
            "error",
        )
    return redirect(_subzero_return_url(board, request.form))


@bp.route("/application-context", methods=["POST"])
@gateway_node_required("subzero")
def application_context_mutate():
    board = str(request.form.get("board") or "").strip().casefold()
    permission = {
        "outbound": "neosubzero.outbound.edit",
        "coordinator": "neosubzero.coordinator.edit",
    }.get(board)
    if permission is None or not user_can(permission):
        return "Access denied.", 403
    operation = current_neosubzero_operation(get_current_gateway())
    if operation is None:
        flash("No current sort is available.", "error")
    else:
        try:
            session[_application_context_key(operation)] = {
                "active_precipitation": _context_text(
                    request.form.get("active_precipitation"), 120, "Active Precipitation"
                ),
                "ambient_temperature": _context_text(
                    request.form.get("ambient_temperature"), 32, "Ambient temperature"
                ),
                "dew_point": _context_text(request.form.get("dew_point"), 32, "Dew point"),
                "notes": _context_text(request.form.get("notes"), 2000, "Notes"),
            }
            session.modified = True
            flash("APPLICATION CONTEXT SAVED.", "success")
        except NeoSubZeroSprayError as exc:
            flash(str(exc), "error")
    return redirect(_subzero_return_url(board, request.form))


@bp.route("/spray-reason", methods=["POST"])
@gateway_node_required("subzero")
def spray_reason_mutate():
    board = str(request.form.get("board") or "").strip().casefold()
    permission = {
        "outbound": "neosubzero.outbound.edit",
        "coordinator": "neosubzero.coordinator.edit",
    }.get(board)
    if permission is None or not user_can(permission):
        return "Access denied.", 403
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    mission_id = request.form.get("mission_id", type=int)
    try:
        if operation is None:
            raise NeoSubZeroSprayError("No current sort is available.")
        mission = SortDateMission.query.filter_by(
            id=mission_id,
            sort_date_operation_id=operation.id,
            mission_type="departure",
        ).one_or_none()
        event = NeoSubZeroDepartureDeiceEvent.query.filter_by(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission_id,
        ).with_for_update().one_or_none()
        if mission is None or event is None:
            raise NeoSubZeroSprayError("Choose a current departure-deice event.")
        expected_version = str(request.form.get("expected_version") or "").strip()
        if not expected_version or version_conflict(event, expected_version):
            raise NeoSubZeroSprayError(
                "Departure deice changed while you were editing. Review current values."
            )
        set_departure_deice_reason(event, request.form.get("reason_for_application"))
        db.session.commit()
        flash("REASON FOR APPLICATION SAVED.", "success")
    except NeoSubZeroSprayError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(_subzero_return_url(board, request.form))


@bp.route("/deicer-mobile")
@gateway_node_required("subzero")
def deicer_mobile():
    if not user_can("neosubzero.deicer_mobile.view"):
        return "Access denied.", 403
    session[LAST_PAGE_KEY] = "neosubzero.deicer_mobile"
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    assignment = current_user_ucc_assignment(operation, current_user)
    if operation is not None and assignment is None:
        return "A current UCC Driver or Flyer assignment is required.", 403
    context = departure_deice_context(gateway, operation)
    rows = [
        row for row in context["rows"]
        if assignment is not None and row.get("ramp") == assignment.ramp
    ]
    selected_id = request.args.get("mission", type=int)
    if selected_id not in {row["mission_id"] for row in rows}:
        selected_id = rows[0]["mission_id"] if rows else None
    return render_template(
        "neonodes/neosubzero/deicer_mobile.html",
        gateway=gateway,
        operation=operation,
        assignment=assignment,
        rows=rows,
        selected_mission_id=selected_id,
        revision=departure_deice_revision(gateway, operation),
        refresh_status=subzero_refresh_status(gateway, operation, DEICER_REFRESH_KEY),
    )


@bp.route("/deicer-mobile/revision")
@gateway_node_required("subzero")
def deicer_mobile_revision_endpoint():
    operation = current_neosubzero_operation(get_current_gateway())
    if operation is not None and current_user_ucc_assignment(operation, current_user) is None:
        return jsonify({"ok": False, "error": "A current UCC assignment is required."}), 403
    return _departure_revision_response(
        "neosubzero.deicer_mobile.view", DEICER_REFRESH_KEY
    )


@bp.route("/deice-log")
@gateway_node_required("subzero")
def deice_log():
    if not user_can("neosubzero.deice_log.view"):
        return "Access denied.", 403
    session[LAST_PAGE_KEY] = "neosubzero.deice_log"
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    return render_template(
        "neonodes/neosubzero/deice_log.html",
        gateway=gateway,
        operation=operation,
        groups=neosubzero_deice_log(operation),
    )


@bp.route("/callouts", methods=["GET", "POST"])
@gateway_node_required("subzero")
def callouts():
    access = permission_access(
        "neosubzero.callouts.view",
        "neosubzero.callouts.edit",
    )
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosubzero.index"))
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    session[LAST_PAGE_KEY] = "neosubzero.callouts"
    if request.method == "POST":
        if not access["can_edit"]:
            return "Access denied.", 403
        try:
            if operation is None:
                raise NeoSubZeroStaffingError("No current sort is available.")
            person = StaffingPerson.query.filter_by(
                id=request.form.get("person_id", type=int),
                active=True,
            ).with_for_update().one_or_none()
            assignment = NeoSubZeroCalloutAssignment.query.filter_by(
                sort_date_operation_id=operation.id,
                person_id=getattr(person, "id", None),
            ).with_for_update().one_or_none()
            expected_version = str(
                request.form.get("expected_version") or ""
            ).strip()
            if assignment and (
                not expected_version
                or version_conflict(assignment, expected_version)
            ):
                raise NeoSubZeroStaffingError(
                    "Callout membership changed while you were editing. Review current values."
                )
            selected = request.form.get("action") == "add"
            if request.form.get("action") not in {"add", "remove"}:
                raise NeoSubZeroStaffingError("Choose a valid callout action.")
            set_neosubzero_callout_membership(
                operation,
                person,
                selected,
                user_id=current_user.id,
                assignment=assignment,
            )
            db.session.commit()
            flash(
                "CALLOUT ADDED." if selected else "CALLOUT REMOVED.",
                "success",
            )
        except (NeoSubZeroStaffingError, IntegrityError) as exc:
            db.session.rollback()
            flash(
                str(exc)
                if isinstance(exc, NeoSubZeroStaffingError)
                else "Unable to save callout membership.",
                "error",
            )
        return redirect(url_for("neosubzero.callouts"))
    return render_template(
        "neonodes/neosubzero/callouts.html",
        gateway=gateway,
        can_edit=access["can_edit"],
        **neosubzero_callout_context(operation),
    )


@bp.route("/qualifications", methods=["GET", "POST"])
@gateway_node_required("subzero")
def qualifications():
    access = permission_access(
        "neosubzero.qualifications.view",
        "neosubzero.qualifications.edit",
    )
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosubzero.index"))
    gateway = get_current_gateway()
    search = str(request.values.get("search") or "").strip()
    session[LAST_PAGE_KEY] = "neosubzero.qualifications"
    if request.method == "POST":
        if not access["can_edit"]:
            return "Access denied.", 403
        try:
            person = StaffingPerson.query.filter_by(
                id=request.form.get("person_id", type=int),
                active=True,
            ).with_for_update().one_or_none()
            qualification = StaffingPersonQualification.query.filter_by(
                person_id=getattr(person, "id", None),
                qualification_key=DEICE_QUALIFICATION_KEY,
            ).with_for_update().one_or_none()
            expected_version = str(
                request.form.get("expected_version") or ""
            ).strip()
            if qualification and (
                not expected_version
                or version_conflict(qualification, expected_version)
            ):
                raise NeoSubZeroStaffingError(
                    "Qualification changed while you were editing. Review current values."
                )
            action = request.form.get("action")
            if action not in {"qualify", "unqualify"}:
                raise NeoSubZeroStaffingError("Choose a valid qualification action.")
            qualified = action == "qualify"
            set_staffing_person_qualification(
                person,
                DEICE_QUALIFICATION_KEY,
                qualified,
                user_id=current_user.id,
                qualification=qualification,
            )
            db.session.commit()
            flash(
                "DEICE QUALIFICATION GRANTED."
                if qualified
                else "DEICE QUALIFICATION REMOVED.",
                "success",
            )
        except (NeoSubZeroStaffingError, IntegrityError) as exc:
            db.session.rollback()
            flash(
                str(exc)
                if isinstance(exc, NeoSubZeroStaffingError)
                else "Unable to save Deice qualification.",
                "error",
            )
        return redirect(url_for("neosubzero.qualifications", search=search))
    return render_template(
        "neonodes/neosubzero/qualifications.html",
        gateway=gateway,
        can_edit=access["can_edit"],
        search=search,
        rows=neosubzero_qualification_people(search),
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
        if request.form.get("action") == "toggle_configured" and operation:
            operation = SortDateOperation.query.filter_by(
                id=operation.id,
                gateway_code=gateway.code,
            ).with_for_update().one_or_none()
            if operation is None:
                raise NeoSubZeroDepartureDeiceError(
                    "The current sort changed. Reload departure deice."
                )
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
            if action == "save_fluids":
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
        fluid_settings=neosubzero_fluid_settings(gateway),
    )


def _can_edit_position(board, operation, ramp, position, *, allow_ucc=False):
    permission = {
        "outbound": "neosubzero.outbound.edit",
        "coordinator": "neosubzero.coordinator.edit",
    }.get(board)
    if board == "ucc" and allow_ucc:
        permission = "neosubzero.ucc.edit"
    if permission is not None:
        return user_can(permission)
    if board != "deicer" or not user_can("neosubzero.deicer_mobile.view"):
        return False
    assignment = current_user_ucc_assignment(operation, current_user)
    return bool(
        assignment
        and assignment.ramp == str(ramp or "").strip().title()
        and assignment.position_number == position
    )


def _departure_row_for_mission(gateway, operation, mission_id):
    return next(
        (
            row
            for row in departure_deice_context(gateway, operation)["rows"]
            if row["mission_id"] == mission_id
        ),
        None,
    )


def _application_context_key(operation):
    return f"neosubzero.application_context.{operation.id}"


def _application_context(operation):
    if operation is None:
        return {
            "active_precipitation": "",
            "ambient_temperature": "",
            "dew_point": "",
            "notes": "",
        }
    value = session.get(_application_context_key(operation)) or {}
    return {
        "active_precipitation": str(value.get("active_precipitation") or ""),
        "ambient_temperature": str(value.get("ambient_temperature") or ""),
        "dew_point": str(value.get("dew_point") or ""),
        "notes": str(value.get("notes") or ""),
    }


def _context_text(value, maximum, label):
    normalized = str(value or "").strip()
    if len(normalized) > maximum:
        raise NeoSubZeroSprayError(f"{label} must be {maximum} characters or fewer.")
    return normalized


def _subzero_return_url(board, values):
    mission_id = values.get("mission_id", type=int)
    ramp = str(values.get("ramp") or "").strip().title()
    if board == "coordinator":
        return url_for("neosubzero.coordinator", ramp=ramp or None, mission=mission_id)
    if board == "deicer":
        return url_for("neosubzero.deicer_mobile", mission=mission_id)
    if board == "ucc":
        return url_for("neosubzero.ucc")
    return url_for("neosubzero.outbound")


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
