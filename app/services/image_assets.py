"""Content-versioned public images. No user data, database access or HTML cache."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from flask import request


@lru_cache(maxsize=512)
def _digest(path, size, modified_ns):
    # The stat identity invalidates this bounded process-local hash memo when
    # artwork changes. Hash in chunks instead of retaining large source images.
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def image_digest(static_folder, filename):
    if not isinstance(filename, str) or not filename.startswith("images/"):
        return None
    root = Path(static_folder).resolve()
    path = (root / filename).resolve()
    if not path.is_relative_to(root / "images") or path.suffix.lower() not in {".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg", ".ico"}:
        return None
    try:
        stat = path.stat()
        return _digest(str(path), stat.st_size, stat.st_mtime_ns)
    except (OSError, ValueError):
        return None


def register_image_assets(app):
    manifest = Path(app.static_folder) / "images/optimized/manifest.json"
    rows = json.loads(manifest.read_text(encoding="utf-8"))["images"]
    replacements = {row["source"]: row["file"] for row in rows if row.get("purpose") != "character-menu"}

    @app.url_defaults
    def version_image_url(endpoint, values):
        if endpoint != "static":
            return
        filename = values.get("filename")
        filename = replacements.get(filename, filename)
        digest = image_digest(app.static_folder, filename)
        if digest:
            values.update(filename=filename, v=digest)

    @app.after_request
    def cache_content_versioned_image(response):
        if request.endpoint != "static" or request.method not in {"GET", "HEAD"} or response.status_code not in {200, 304}:
            return response
        digest = image_digest(app.static_folder, (request.view_args or {}).get("filename"))
        if digest and request.args.get("v") == digest:
            response.set_etag(digest)
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            response.make_conditional(request)
        return response
