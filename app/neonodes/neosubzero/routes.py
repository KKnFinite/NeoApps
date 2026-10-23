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
    current_subzero_staffing_pool,
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
    move_ucc_unit,
    reorder_ucc_queue,
    validate_ucc_truck_unique,
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
    ('Deice Operations', 'neosubzero.operations', 'neosubzero.outbound.view', 'neosubzero.outbound.edit'),
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
        "DUCC",
        "neosubzero.ucc",
        "neosubzero.ucc.view",
        "neosubzero.ucc.edit",
    ),
    (
        "Deicer",
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
NEOSUBZERO_NAV_SECTIONS = (
    (None, ("neosubzero.ucc", "neosubzero.operations", "neosubzero.coordinator",
            "neosubzero.deicer_mobile", "neosubzero.deice_log", "neosubzero.settings")),
    ("UNDER CONSTRUCTION", ("neosubzero.callouts",)),
)


def neosubzero_navigation_sections():
    # Navigation visibility is separate from direct-route authorization.
    pages = {page[1]: page for page in NEOSUBZERO_PAGES}
    sections = []
    for heading, endpoints in NEOSUBZERO_NAV_SECTIONS:
        items = [(pages[endpoint][0], endpoint) for endpoint in endpoints
                 if _navigation_page_allowed(endpoint, pages[endpoint][2])]
        if items:
            sections.append((heading, items))
    return sections


def _navigation_page_allowed(endpoint, permission):
    return any(user_can(key) for key in ('neosubzero.pretreat.view', 'neosubzero.outbound.view', 'neosubzero.coordinator.view')) if endpoint == 'neosubzero.operations' else user_can(permission)


@bp.context_processor
def navigation():
    return {
        "neosubzero_menu_sections": neosubzero_navigation_sections,
        "neosubzero_menu_items": lambda: [
            (label, endpoint)
            for _heading, items in neosubzero_navigation_sections()
            for label, endpoint in items
        ]
    }


@bp.route("")
@bp.route("/")
@gateway_node_required("subzero")
def index():
    endpoint = session.get(LAST_PAGE_KEY)
    permitted_endpoints = [
        page[1] for page in NEOSUBZERO_PAGES if _navigation_page_allowed(page[1], page[2])
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
    from app.neonodes.neosubzero.operations_routes import render_operations
    return render_operations("pretreat")


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
    from app.neonodes.neosubzero.operations_routes import render_operations
    return render_operations("outbound")


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
    from app.services.neosubzero_ucc import ucc_staffing_version
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
        staffing_version=ucc_staffing_version(operation) if operation else '',
        staffing_pool=current_subzero_staffing_pool(operation) if operation else (),
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
            _validate_ucc_sort(locked_operation)
            ramp = str(request.form.get("ramp") or "").strip().title()
            active_ramps = {
                item["name"]
                for item in neosubzero_ucc_context(gateway, locked_operation)["ramps"]
            }
            if ramp not in active_ramps:
                raise NeoSubZeroUccError(
                    "Choose a current UCC ramp."
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
            if (assignment and (not expected_version or version_conflict(assignment, expected_version))) or (assignment is None and expected_version):
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
            if _ucc_json_request():
                return jsonify(ok=True, message="UCC staffing saved." if result["changed"] else "UCC staffing unchanged.")
            flash(
                "UCC STAFFING SAVED." if result["changed"] else "UCC STAFFING UNCHANGED.",
                "success" if result["changed"] else "info",
            )
        except (NeoSubZeroUccError, IntegrityError) as exc:
            db.session.rollback()
            if _ucc_json_request():
                return jsonify(ok=False, error=str(exc) if isinstance(exc, NeoSubZeroUccError) else "Unable to save UCC staffing. Review current values before retrying."), 409
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


def _ucc_json_request():
    return request.headers.get("X-UCC-Request") == "1"


def _validate_ucc_sort(operation):
    submitted = request.form.get("operation_id")
    if submitted is not None and str(operation.id) != submitted:
        raise NeoSubZeroUccError("The current sort changed. Your draft was not applied; reload current UCC.")


@bp.route("/ucc/<any(unit,queue):planning_action>", methods=["POST"])
@gateway_node_required("subzero")
def ucc_planning_mutate(planning_action):
    if not user_can("neosubzero.ucc.edit"):
        return jsonify(ok=False, error="Access denied."), 403
    gateway = get_current_gateway()
    operation = current_neosubzero_operation(gateway)
    try:
        if operation is None:
            raise NeoSubZeroUccError("No current sort is available.")
        operation = SortDateOperation.query.filter_by(id=operation.id, gateway_code=gateway.code).with_for_update().one()
        _validate_ucc_sort(operation)
        if not request.form.get("operation_id"):
            raise NeoSubZeroUccError("Reload current UCC before making this change.")
        if planning_action == "unit":
            if request.form.get("resolution") not in {"move", "swap"}:
                raise NeoSubZeroUccError("Choose MOVE TO EMPTY or SWAP UNITS.")
            source_ramp, source_position = request.form.get("ramp"), request.form.get("position_number")
            if request.form.get("source_slot"):
                source_ramp, separator, source_position = request.form["source_slot"].partition("|")
                if not separator:
                    raise NeoSubZeroUccError("Choose a valid source unit.")
            move_ucc_unit(
                operation, source_ramp, source_position,
                request.form.get("destination_ramp"), request.form.get("destination_position"),
                expected_version=request.form.get("expected_version"),
                swap=request.form.get("resolution") == "swap", user_id=current_user.id,
            )
            message = "UCC unit moved. Existing spray records are unchanged."
        else:
            reorder_ucc_queue(
                gateway, operation, request.form.get("ramp"),
                request.form.get("mission_id", type=int), request.form.get("direction"),
                expected_version=request.form.get("expected_version"), user_id=current_user.id,
            )
            message = "Departure queue corrected."
        db.session.commit()
        if _ucc_json_request():
            return jsonify(ok=True, message=message)
        flash(message, "success")
    except (NeoSubZeroUccError, IntegrityError) as exc:
        db.session.rollback()
        message = str(exc) if isinstance(exc, NeoSubZeroUccError) else "UCC changed concurrently. Review current values before retrying."
        if _ucc_json_request():
            return jsonify(ok=False, error=message), 409
        flash(message, "error")
    return redirect(url_for("neosubzero.ucc"))


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
    if board == 'deicer':
        return 'Drivers may enter only their documented pass gallons.', 403
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
        if request.form.get('operation_id',type=int)!=locked_operation.id:
            raise NeoSubZeroSprayError('The current sort changed. Reload current truck assignments.')
        if board == "ucc":
            try:
                _validate_ucc_sort(locked_operation)
            except NeoSubZeroUccError as exc:
                raise NeoSubZeroSprayError(str(exc)) from exc
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
        if (truck and (not expected_version or version_conflict(truck, expected_version))) or (truck is None and expected_version):
            raise NeoSubZeroSprayError(
                "Truck assignment changed while you were editing. Review current values."
            )
        if board == "ucc":
            try:
                validate_ucc_truck_unique(locked_operation, request.form.get("truck_number"), truck)
            except NeoSubZeroUccError as exc:
                raise NeoSubZeroSprayError(str(exc)) from exc
        set_neosubzero_ucc_truck(
            locked_operation,
            ramp,
            position,
            request.form.get("truck_number"),
            user_id=current_user.id,
            assignment=truck,
        )
        db.session.commit()
        if board == "ucc" and _ucc_json_request():
            return jsonify(ok=True, message="UCC truck saved.")
        flash("UCC TRUCK SAVED.", "success")
    except (NeoSubZeroSprayError, IntegrityError) as exc:
        db.session.rollback()
        if board == "ucc" and _ucc_json_request():
            return jsonify(ok=False, error=str(exc) if isinstance(exc, NeoSubZeroSprayError) else "Unable to save truck. Review current values."), 409
        flash(
            str(exc) if isinstance(exc, NeoSubZeroSprayError) else "Unable to save truck.",
            "error",
        )
    return redirect(_subzero_return_url(board, request.form))


@bp.route("/spray-gallons", methods=["POST"])
@gateway_node_required("subzero")
def spray_gallons_mutate():
    from app.neonodes.neosubzero.release2_routes import locked_operation
    from app.services.neosubzero_evidence import normalized_tail,NeoSubZeroEvidenceError,departure_event_for_tail
    from app.services.neosubzero_spray import driver_owns_participation
    board=str(request.form.get('board') or '').strip().casefold()
    try:
        gateway=get_current_gateway();operation=locked_operation(gateway)
        record_id=request.form.get('record_id',type=int)
        record=NeoSubZeroSprayRecord.query.filter_by(id=record_id,sort_date_operation_id=operation.id).with_for_update().one_or_none() if record_id else None
        if record is None:
            from app.neonodes.neosubzero.release2_routes import locked_mission
            mission=locked_mission(operation);parent=departure_event_for_tail(operation,mission,lock=True)
            record=NeoSubZeroSprayRecord.query.filter_by(departure_deice_event_id=getattr(parent,'id',None),
                pass_number=request.form.get('pass_number',type=int),position_number=request.form.get('position_number',type=int)).with_for_update().one_or_none() if parent else None
        if record is None: raise NeoSubZeroEvidenceError('Select an official recorded pass participant before entering gallons.')
        parent=record.pretreat_state or record.departure_deice_event
        mission=db.session.get(SortDateMission,record.sort_date_mission_id)
        physical_tail=normalized_tail(record.tail_number_snapshot or getattr(parent,'tail_number',None))
        if not physical_tail or normalized_tail(request.form.get('physical_tail'))!=physical_tail:
            raise NeoSubZeroEvidenceError('Physical-tail identity changed. Reload this participation record.')
        if board=='deicer':
            if not user_can('neosubzero.deicer_mobile.edit') or not driver_owns_participation(current_user,record): return 'Access denied.',403
        elif board not in {'outbound','coordinator','pretreat'} or not user_can('neosubzero.'+board+'.edit') or (board=='pretreat' and not record.pretreat_state_id): return 'Access denied.',403
        if not request.form.get('expected_version') or version_conflict(record,request.form.get('expected_version')):
            raise NeoSubZeroEvidenceError('Gallons or participation changed. Your draft is kept; reload current values.')
        set_neosubzero_spray_gallons(operation,mission,parent,record.pass_number,record.position_number,request.form.get('gallons'),
            record=record,user_id=current_user.id,correction_reason=request.form.get('correction_reason'))
        db.session.commit()
        if request.headers.get('X-Requested-With')=='XMLHttpRequest':
            return jsonify(ok=True,message='SPRAY GALLONS SAVED.')
        flash('SPRAY GALLONS SAVED.','success')
    except (ValueError,IntegrityError) as exc:
        db.session.rollback()
        message=str(exc) if isinstance(exc,ValueError) else 'Gallons conflicted. Reload current values.'
        if request.headers.get('X-Requested-With')=='XMLHttpRequest': return jsonify(ok=False,error=message),409
        flash(message,'error')
    if request.form.get('return_to')=='log': return redirect(url_for('neosubzero.deice_log'))
    return redirect(_subzero_return_url(board,request.form))


@bp.route("/application-context", methods=["POST"])
@gateway_node_required("subzero")
def application_context_mutate():
    from app.neonodes.neosubzero.release2_routes import locked_operation
    from app.services.neosubzero_evidence import save_application_context
    board=str(request.form.get('board') or '').strip().casefold()
    if board not in {'outbound','coordinator'} or not user_can('neosubzero.'+board+'.edit'): return 'Access denied.',403
    try:
        operation=locked_operation(get_current_gateway())
        save_application_context(operation,request.form,actor_id=current_user.id)
        db.session.commit();flash('APPLICATION CONTEXT SAVED.','success')
    except (ValueError,IntegrityError) as exc:
        db.session.rollback();flash(str(exc) if isinstance(exc,ValueError) else 'Application Context conflicted. Your draft is kept.','error')
    return redirect(_subzero_return_url(board,request.form))


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
        from app.neonodes.neosubzero.release2_routes import locked_operation, locked_mission
        from app.services.neosubzero_evidence import departure_event_for_tail, audit_change, evidence_values
        operation = locked_operation(gateway)
        mission = locked_mission(operation)
        event = departure_event_for_tail(operation, mission, lock=True)
        if mission is None or event is None:
            raise NeoSubZeroSprayError("Choose a current departure-deice event.")
        expected_version = str(request.form.get("expected_version") or "").strip()
        if not expected_version or version_conflict(event, expected_version):
            raise NeoSubZeroSprayError(
                "Departure deice changed while you were editing. Review current values."
            )
        before = evidence_values(event)
        set_departure_deice_reason(event, request.form.get("reason_for_application"))
        audit_change(operation, 'application_reason', actor_id=current_user.id, mission=mission,
                     event=event, before=before, after=evidence_values(event))
        db.session.commit()
        flash("REASON FOR APPLICATION SAVED.", "success")
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(str(exc) if isinstance(exc, ValueError) else 'Application reason conflicted. Reload current values.', "error")
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
    from app.services.neosubzero_spray import driver_owns_participation
    own_records=[record for record in NeoSubZeroSprayRecord.query.filter_by(sort_date_operation_id=operation.id).all()
                 if driver_owns_participation(current_user,record)] if operation else []
    if operation is not None and assignment is None and not own_records:
        return "A current UCC assignment or documented Driver participation is required.", 403
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
        can_edit=user_can("neosubzero.deicer_mobile.edit"),
        own_records=own_records,
        entity_version=entity_version,
        rows=rows,
        selected_mission_id=selected_id,
        revision=departure_deice_revision(gateway, operation),
        refresh_status=subzero_refresh_status(gateway, operation, DEICER_REFRESH_KEY),
    )


@bp.route("/deicer-mobile/revision")
@gateway_node_required("subzero")
def deicer_mobile_revision_endpoint():
    operation = current_neosubzero_operation(get_current_gateway())
    from app.services.neosubzero_spray import driver_owns_participation
    own=operation and any(driver_owns_participation(current_user,r) for r in NeoSubZeroSprayRecord.query.filter_by(sort_date_operation_id=operation.id).all())
    if operation is not None and current_user_ucc_assignment(operation, current_user) is None and not own:
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
    from app.services.neosubzero_evidence import evidence_values
    from app.models import NeoSubZeroAuditEntry
    return render_template(
        "neonodes/neosubzero/deice_log.html",
        gateway=gateway,
        operation=operation,
        groups=neosubzero_deice_log(operation),
        entity_version=entity_version,
        can_correct=user_can('neosubzero.coordinator.edit') or user_can('neosubzero.outbound.edit'),
        staffing_pool=current_subzero_staffing_pool(operation) if operation else (),
        audits=NeoSubZeroAuditEntry.query.filter_by(sort_date_operation_id=operation.id).order_by(NeoSubZeroAuditEntry.recorded_at_utc.desc()).all() if operation else (),
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
        from app.neonodes.neosubzero.release2_routes import locked_operation, locked_mission, require_version
        from app.services.neosubzero_evidence import departure_event_for_tail, NeoSubZeroEvidenceError
        operation = locked_operation(gateway)
        mission = locked_mission(operation)
        event = departure_event_for_tail(operation, mission, lock=True)
        if request.form.get('event_id', type=int) not in (None, getattr(event, 'id', None)):
            raise NeoSubZeroEvidenceError('The treatment event changed. Reload this physical tail.')
        require_version(event, request.form.get('expected_version'), 'Departure deice')
        event = mutate_departure_deice(
            operation,
            mission,
            request.form.get("action"),
            request.form,
            event=event,
            actor_id=current_user.id,
        )
        db.session.commit()
        flash("DEPARTURE DEICE SAVED.", "success")
        if board == "coordinator":
            coordinator_return_to_list = event.status == 'cleared'
            _remember_coordinator_mission(
                operation,
                None if coordinator_return_to_list else mission.id,
            )
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(
            str(exc)
            if isinstance(exc, ValueError)
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
        from app.neonodes.neosubzero.release2_routes import locked_operation, locked_mission, require_version
        operation = locked_operation(gateway)
        mission = locked_mission(operation)
        tail = str(mission.assigned_tail_number or '').strip().upper()
        state = NeoSubZeroPretreatState.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number=tail,
        ).with_for_update().one_or_none()
        require_version(state, request.form.get('expected_version'), 'Pretreat')
        mutate_pretreat(
            operation,
            mission,
            request.form.get("action"),
            request.form,
            state=state,
            actor_id=current_user.id,
        )
        db.session.commit()
        flash("PRETREAT SAVED.", "success")
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        message = (
            str(exc)
            if isinstance(exc, ValueError)
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
    if board != "deicer" or not user_can("neosubzero.deicer_mobile.edit"):
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
    from app.services.neosubzero_evidence import application_context
    return application_context(operation)


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
