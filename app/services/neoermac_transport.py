"""Read-only presentation fingerprints; never used as authorization or write tokens."""
import hashlib
import json

from flask import current_app


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def lineup_common(state, client_version=None):
    common = {key: state[key] for key in ("destination_choices", "pull_times")}
    version = fingerprint(common)
    if client_version == version:
        for key in common:
            del state[key]
    return version


def outbound_snapshot(context):
    """Render both existing row layouts once, keyed by canonical mission identity."""
    macros = current_app.jinja_env.get_template("neonodes/neoermac/_outbound_rows.html").module
    rendered = {}
    order = []
    safe = True
    for row in context["rows"]:
        key = f"m{row['mission_id']}" if row.get("mission_id") is not None else f"missing:{row['destination']}"
        if key in rendered:
            safe = False
        row["transport_key"] = key
        order.append(key)
        rendered[key] = {"desktop": macros.desktop(row), "mobile": macros.mobile(row)}
    operation = context.get("operation")
    manifest = {
        "scope": f"outbound-v1:{operation.gateway_id}:{operation.id}" if operation else "outbound-v1:none",
        "rows": {key: fingerprint(value) for key, value in rendered.items()},
    }
    return {"manifest": manifest, "order": order, "rows": rendered, "safe": safe}


def outbound_response(context, snapshot, client_manifest):
    # Bound untrusted metadata parsing. Absent/old/invalid clients get the
    # existing full fragment; operation changes and empty layouts also reset.
    previous = None
    if client_manifest and len(client_manifest) <= 16000:
        try:
            previous = json.loads(client_manifest)
        except (ValueError, TypeError):
            pass
    valid = (
        snapshot["safe"] and bool(snapshot["order"])
        and isinstance(previous, dict)
        and previous.get("scope") == snapshot["manifest"]["scope"]
        and isinstance(previous.get("rows"), dict) and bool(previous["rows"])
        and all(isinstance(k, str) and isinstance(v, str) for k, v in previous["rows"].items())
    )
    if valid:
        changed = {key: value for key, value in snapshot["rows"].items()
                   if previous["rows"].get(key) != snapshot["manifest"]["rows"][key]}
        return {"row_delta": {"order": snapshot["order"], "rows": changed}, "row_manifest": snapshot["manifest"]}
    html = current_app.jinja_env.get_template("neonodes/neoermac/_view_outbound_content.html").render(
        rows=context["rows"], **({"row_html": snapshot["rows"]} if snapshot["safe"] else {}),
    )
    return {"content_html": html, "row_manifest": snapshot["manifest"] if snapshot["safe"] else None}
