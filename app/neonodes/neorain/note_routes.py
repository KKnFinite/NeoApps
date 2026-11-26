"""Compatibility response for tabs opened before the Delay Info editor replaced Mission Notes."""
from flask import jsonify, request

from app.auth.decorators import gateway_node_required
from app.neonodes.neorain import bp
from app.services.permission_rules import user_can


@bp.post("/mission-notes")
@gateway_node_required("rain")
def mission_notes():
    board = request.form.get("board")
    if board not in {"inbound", "outbound"}:
        return jsonify(ok=False, error="Choose an inbound or outbound mission."), 400
    if not user_can(f"neorain.{board}.view") or not user_can(f"neorain.{board}.edit"):
        return jsonify(ok=False, error="Delay Info edit access denied."), 403
    return jsonify(ok=False, error="Copy your draft, reload the board and save it under Delay Info."), 409
