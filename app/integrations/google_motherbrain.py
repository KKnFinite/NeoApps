"""Authenticated preview-only bridge for the locked Google MotherBrain workbook."""

from __future__ import annotations

import hmac
import json

from flask import Blueprint, current_app, jsonify, request

from app.extensions import db
from app.services.csrf import csrf_exempt
from app.services.google_motherbrain_import import (
    GoogleMotherBrainOperationError,
    GoogleMotherBrainPayloadError,
    build_google_motherbrain_preview,
    resolve_google_motherbrain_operation,
    validate_google_motherbrain_envelope,
)


bp = Blueprint(
    "google_motherbrain_integration",
    __name__,
    url_prefix="/integrations/google-motherbrain",
)


@bp.after_request
def prevent_integration_response_caching(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/current-sort/preview")
@csrf_exempt
def current_sort_preview():
    try:
        if not current_app.config.get("GOOGLE_MOTHERBRAIN_IMPORT_ENABLED", False):
            return _error_response(404, "not_found", "Not found.")

        if not _integration_token_is_valid():
            return _error_response(401, "unauthorized", "Unauthorized.")

        if request.mimetype != "application/json":
            return _error_response(
                415,
                "unsupported_media_type",
                "Content-Type must be application/json.",
            )

        maximum_bytes = int(
            current_app.config.get("GOOGLE_MOTHERBRAIN_MAX_REQUEST_BYTES", 524288)
        )
        if request.content_length is not None and request.content_length > maximum_bytes:
            return _error_response(413, "payload_too_large", "Request body is too large.")

        raw_body = request.get_data(cache=True)
        if len(raw_body) > maximum_bytes:
            return _error_response(413, "payload_too_large", "Request body is too large.")
        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error_response(400, "malformed_json", "Request body is not valid JSON.")

        validated = validate_google_motherbrain_envelope(
            payload,
            current_app.config.get("GOOGLE_MOTHERBRAIN_SPREADSHEET_ID"),
        )
        operation = resolve_google_motherbrain_operation(validated)
        preview = build_google_motherbrain_preview(validated, operation)
        return jsonify(preview), 200
    except GoogleMotherBrainPayloadError as exc:
        return _error_response(400, exc.code, exc.message)
    except GoogleMotherBrainOperationError as exc:
        return _error_response(exc.status_code, exc.code, exc.message)
    except Exception:
        current_app.logger.exception("Google MotherBrain preview failed safely.")
        return _error_response(
            500,
            "preview_failed",
            "The preview could not be generated.",
        )
    finally:
        db.session.rollback()


def _integration_token_is_valid():
    configured = current_app.config.get("GOOGLE_MOTHERBRAIN_IMPORT_TOKEN")
    submitted = request.headers.get("X-Neo-Integration-Token")
    if not isinstance(configured, str) or not configured:
        return False
    if not isinstance(submitted, str) or not submitted:
        return False
    return hmac.compare_digest(submitted, configured)


def _error_response(status_code, code, message):
    return (
        jsonify(
            {
                "ok": False,
                "preview_only": True,
                "error": {
                    "code": code,
                    "message": message,
                },
            }
        ),
        status_code,
    )
