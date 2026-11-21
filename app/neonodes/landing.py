"""Viewport-aware node entry. Only node roots call this helper."""
from flask import redirect, render_template, request, url_for


def node_landing(desktop_endpoint, *, splash=None, mobile_url=None, **context):
    # The gateway launcher passes only the selected operation to the new screen.
    operation_id = request.args.get("operation_id", type=int)
    desktop_url = url_for(desktop_endpoint, operation_id=operation_id) if desktop_endpoint else None
    if splash:
        return render_template(
            splash, desktop_landing_url=desktop_url, splash_operation_id=operation_id, **context,
        )
    if not desktop_url or desktop_url == mobile_url or not request.accept_mimetypes.accept_html:
        return redirect(mobile_url)
    return render_template("neonodes/responsive_entry.html", desktop_landing_url=desktop_url,
                           mobile_landing_url=mobile_url)
