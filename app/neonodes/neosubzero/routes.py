from flask import flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import NeoSubZeroPretreatState, SortDateMission
from app.neonodes.neosubzero import bp
from app.neonodes.neosubzero.services import (
    PRETREAT_REFRESH_KEY, SURFACE_LABELS, NeoSubZeroPretreatError,
    current_neosubzero_operation, mutate_pretreat, pretreat_context,
    pretreat_refresh_status, pretreat_revision,
)
from app.services.access_control import get_current_gateway
from app.services.live_collaboration import entity_version, version_conflict
from app.services.live_screen_refresh import (
    LIVE_SCREEN_REFRESH_ALLOWED_SECONDS,
    live_screen_refresh_value,
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
            result = save_live_screen_refresh_override(
                gateway,
                PRETREAT_REFRESH_KEY,
                request.form.get("refresh_interval_seconds"),
                allowed_screen_keys=(PRETREAT_REFRESH_KEY,),
            )
            if result.changed:
                db.session.commit()
            else:
                db.session.rollback()
            flash("REFRESH SETTING SAVED.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("neosubzero.settings"))
    return render_template(
        "neonodes/neosubzero/settings.html",
        can_edit=access["can_edit"],
        refresh_setting=live_screen_refresh_value(gateway, PRETREAT_REFRESH_KEY),
        choices=LIVE_SCREEN_REFRESH_ALLOWED_SECONDS,
    )
