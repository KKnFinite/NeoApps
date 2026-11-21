"""Mission Notes on the current inbound and outbound boards."""
from flask import jsonify, render_template, request
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import SortDateMission, SortDateOperation
from app.neonodes.neorain import bp, routes
from app.services.access_control import get_current_gateway
from app.services.neorain_mission_notes import MissionNoteConflict, mission_note_payload, save_mission_note
from app.services.permission_rules import user_can


@bp.post("/mission-notes")
@gateway_node_required("rain")
def mission_notes():
    board = request.form.get("board")
    if board not in {"inbound", "outbound"}:
        return jsonify(ok=False, error="Choose an inbound or outbound mission."), 400
    if not user_can(f"neorain.{board}.view") or not user_can(f"neorain.{board}.edit"):
        return jsonify(ok=False, error="Mission Notes edit access denied."), 403
    gateway = get_current_gateway()
    operation = routes.current_neorain_outbound_operation(gateway)
    if not operation or operation.id != request.form.get("operation_id", type=int):
        return jsonify(ok=False, error="The current Sort changed. Reload to continue."), 409

    direction = "arrival" if board == "inbound" else "departure"
    try:
        operation = SortDateOperation.query.filter_by(id=operation.id).populate_existing().with_for_update().one()
        if operation.archived_at_utc or operation.locked_at_utc:
            return jsonify(ok=False, error="This Sort is read-only."), 409
        mission = SortDateMission.query.filter_by(
            id=request.form.get("mission_id", type=int), sort_date_operation_id=operation.id,
            gateway_code=gateway.code, mission_type=direction,
        ).with_for_update().one_or_none()
        if mission is None:
            return jsonify(ok=False, error="Mission is outside the current Sort or board."), 404
        save_mission_note(mission, current_user.id, request.form.get("action"),
                          note_id=request.form.get("note_id", type=int),
                          version=request.form.get("version", type=int), text=request.form.get("text"))
        db.session.commit()
        row = {"mission_id": mission.id, "mission_notes": mission_note_payload(mission)}
        html = render_template("neonodes/neorain/_mission_note_entries.html", row=row,
                               operation=operation, board=board, can_edit=True)
    except ValueError as error:
        db.session.rollback()
        return jsonify(ok=False, error=str(error)), 409 if isinstance(error, MissionNoteConflict) else 400
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(ok=False, error="Unable to save the mission note. Your draft is retained."), 503
    return jsonify(ok=True, html=html)
