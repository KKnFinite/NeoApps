"""Read Render release metadata and probe the public service without credentials."""
import argparse
import json
import os
import re
import sys
from urllib.error import HTTPError
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PUBLIC = "https://neoapps.onrender.com"
REPO = "https://github.com/KKnFinite/NeoApps"
TERMINAL = {"live", "deactivated", "build_failed", "pre_deploy_failed", "update_failed", "canceled"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, headers=None):
    try:
        result = build_opener(NoRedirect()).open(Request(url, headers=headers or {}), timeout=15)
    except HTTPError as error:
        result = error
    with result:
        return result.status, result.headers.get("Location", ""), result.read(256_001)


def metadata(path):
    status, _, data = request("https://api.render.com/v1/services/" + path, {
        "Authorization": "Bearer " + os.environ["RENDER_VERIFICATION_KEY"], "Accept": "application/json"})
    if status != 200 or len(data) > 256_000:
        raise ValueError("Render metadata unavailable")
    return json.loads(data)


def verify(service, sha, deployment=None, read=metadata, probe=request):
    if not re.fullmatch(r"srv-[a-z0-9]+", service) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid release target")
    if deployment is not None and not re.fullmatch(r"dep-[a-z0-9]+", deployment):
        raise ValueError("Invalid deployment ID")
    info = read(service)
    details = info["serviceDetails"]
    if (info["id"] != service or info["repo"] != REPO or info["branch"] != "main"
            or details["url"] != PUBLIC or info["autoDeployTrigger"] != "off" or info["autoDeploy"] != "no"
            or details["envSpecificDetails"]["preDeployCommand"] != "python scripts/bootstrap_database.py"):
        raise ValueError("Render source, pre-deploy command or automatic deploy policy differs")
    recent = [entry["deploy"] for entry in read(service + "/deploys?limit=20")]
    if any(row["status"] not in TERMINAL for row in recent):
        raise ValueError("Concurrent or unrecognized deployment")
    live = [row for row in recent if row["status"] == "live"]
    if len(live) != 1 or live[0]["commit"]["id"] != sha:
        raise ValueError("Expected a single matching LIVE revision in recent history")
    if deployment is not None and live[0]["id"] != deployment:
        raise ValueError("Render LIVE deployment ID differs from the accepted hook")
    status, _, health = probe(PUBLIC + "/healthz")
    if status != 200 or health.strip() != b"ok":
        raise ValueError("Unexpected health response")
    status, location, _ = probe(PUBLIC + "/accounts")
    login = urlsplit(urljoin(PUBLIC, location))
    if (status != 302 or (login.scheme, login.netloc, login.path) != ("https", "neoapps.onrender.com", "/login")
            or parse_qs(login.query).get("next") != ["/accounts"]):
        raise ValueError("Expected a local login redirect for signed-out access")
    return live[0]["id"]


if __name__ == "__main__":
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--live", action="store_true")
    final = args.parse_args().live
    try:
        revision = os.environ["GITHUB_SHA" if final else "PREVIOUS_LIVE_SHA"]
        observed = verify(os.environ["RENDER_TARGET_SERVICE"], revision, os.environ["DEPLOYMENT_ID"] if final else None)
    except (OSError, KeyError, TypeError, ValueError):
        sys.exit("Render metadata, release policy, revision, health or protected-route verification failed.")
    print(f"Render verified {observed} at {revision}, health and signed-out access.")
