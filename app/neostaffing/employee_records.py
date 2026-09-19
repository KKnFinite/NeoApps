from io import BytesIO
import json

from flask import abort, render_template, request, redirect, url_for, send_file
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.neostaffing import bp
from app.extensions import db
from app.services import neostaffing_employee_records as records
from app.services.operator_errors import safe_mutation_error


@bp.before_request
def bound_record_upload():
    if request.endpoint and request.endpoint.startswith("neostaffing.employee_record"):
        request.max_content_length = 200_000


@bp.after_request
def private_record_responses(response):
    if request.endpoint and request.endpoint.startswith("neostaffing.employee_record"):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.route("/employee-records", methods=["GET", "POST"])
@login_required
def employee_records():
    try:
        if request.method == "POST":
            records.configure(current_user, int(request.form["unit_id"]), request.form.get("enabled") == "on", request.form.get("version"))
            db.session.commit()
            return redirect(url_for("neostaffing.employee_records"))
        people, units, settings = records.directory(current_user, request.args.get("search"), max(1, request.args.get("page", 1, type=int)))
    except (ValueError, KeyError, SQLAlchemyError) as error:
        db.session.rollback()
        return safe_mutation_error(error, "open or configure Employee Records"), 403
    return render_template("neostaffing/employee_records.html", people=people, units=units, settings=settings,
        master=records.user_can_access_app(current_user, "neostaffing", minimum_role="master"))


@bp.route("/employee-records/person/<int:person_id>", methods=["GET", "POST"])
@login_required
def employee_record_person(person_id):
    try:
        if request.method == "POST":
            record = records.create(current_user, person_id, request.form.get("kind"), request.form.get("body"), request.form.get("resolution_id"))
            db.session.commit()
            return redirect(url_for("neostaffing.employee_record_detail", record_id=record.id))
        person, hierarchy, ids = records.access(current_user, person_id)
        # Grandmaster historical fallback is read-only, not a new write grant.
        try:
            records.access(current_user, person_id, require_write=True)
            editable = True
        except ValueError:
            editable = False
        history = records.Record.query.filter_by(person_id=person.id).order_by(records.Record.created_at.desc(), records.Record.id).paginate(
            page=max(1, request.args.get("page", 1, type=int)), per_page=25, error_out=False)
        on = records.enabled(hierarchy, ids)
    except (ValueError, KeyError, SQLAlchemyError) as error:
        db.session.rollback()
        return safe_mutation_error(error, "open or create Employee Record"), 403
    return render_template("neostaffing/employee_record_person.html", person=person, history=history, enabled=on,
        editable=editable, kinds=records.KINDS)


@bp.route("/employee-records/<record_id>", methods=["GET", "POST"])
@login_required
def employee_record_detail(record_id):
    record = db.session.get(records.Record, record_id)
    if not record:
        abort(404)
    try:
        person, _, _ = records.access(current_user, record.person_id)
        if request.method == "POST":
            command = request.form.get("command")
            if command == "edit":
                records.edit(current_user, record.id, request.form.get("version"), request.form.get("kind"), request.form.get("body"))
            elif command == "finalize":
                upload = request.files.get("signature")
                raw = upload.stream.read(records.storage.MAX_BYTES + 1) if upload else None
                records.finalize(current_user, record.id, request.form.get("version"), request.form.get("method"), request.form.get("reviewed"), raw)
            elif command == "addendum":
                records.addendum(current_user, record.id, request.form.get("body"), request.form.get("sequence"))
            else:
                raise ValueError("Unknown record action.")
            db.session.commit()
            return redirect(url_for("neostaffing.employee_record_detail", record_id=record.id))
        try:
            records.access(current_user, record.person_id, require_write=True)
            editable = True
        except ValueError:
            editable = False
        events = records.Event.query.filter_by(record_id=record.id).order_by(records.Event.sequence.desc()).paginate(
            page=max(1, request.args.get("page", 1, type=int)), per_page=25, error_out=False)
        sequence = db.session.scalar(records.select(records.func.max(records.Event.sequence)).where(records.Event.record_id == record.id))
    except (ValueError, KeyError, SQLAlchemyError) as error:
        db.session.rollback()
        return safe_mutation_error(error, "save Employee Record; reload before retrying"), 409 if request.method == "POST" else 403
    return render_template("neostaffing/employee_record_detail.html", person=person, record=record, events=events,
        context=json.loads(record.context_json), editable=editable, kinds=records.KINDS, sequence=sequence,
        event_text={event.id: (json.loads(event.body)["body"] if event.kind in ("created", "edited") else event.body) for event in events.items},
        acknowledgment=records.ACKNOWLEDGMENT)


@bp.get("/employee-records/<record_id>/signature")
@login_required
def employee_record_signature(record_id):
    record = db.session.get(records.Record, record_id)
    if not record or not record.signature_key:
        abort(404)
    try:
        records.access(current_user, record.person_id)
        raw = records.storage.read(record)
    except ValueError:
        abort(403)
    return send_file(BytesIO(raw), mimetype="image/png", max_age=0)
