"""Trigger one SHA-pinned deployment using a service-specific Render hook."""
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def deploy(hook, service, revision, post):
    url = urlsplit(hook)
    params = parse_qs(url.query, strict_parsing=True)
    if (not re.fullmatch(r"srv-[a-z0-9]+", service) or not re.fullmatch(r"[0-9a-f]{40}", revision)
            or url.scheme != "https" or url.netloc != "api.render.com"
            or url.path != "/deploy/" + service or url.fragment or set(params) != {"key"}
            or len(params["key"]) != 1):
        raise ValueError("Invalid service-specific deploy hook or release revision")
    query = urlencode({"key": params["key"][0], "ref": revision})
    target = urlunsplit((url.scheme, url.netloc, url.path, query, ""))
    try:
        with post(Request(target, method="POST", data=b""), timeout=20) as response:
            if response.status != 200:
                raise ValueError("Deploy queued or unconfirmed. Inspect Render before any further action")
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError):
        raise ValueError("Render did not confirm the request. Inspect deploy history before retrying") from None
    deploy_id = payload.get("deploy", {}).get("id") if isinstance(payload.get("deploy"), dict) else payload.get("id")
    if not deploy_id or not re.fullmatch(r"dep-[a-z0-9]+", deploy_id):
        raise ValueError("Deploy ID unavailable. Inspect Render before retrying")
    return deploy_id


if __name__ == "__main__":
    try:
        deployment = deploy(os.environ["RENDER_DEPLOY_HOOK"], os.environ["RENDER_TARGET_SERVICE"],
                            os.environ["GITHUB_SHA"], build_opener(NoRedirect()).open)
    except (ValueError, KeyError, TypeError):
        sys.exit("Deploy not confirmed. Inspect Render history for the approved SHA before retrying.")
    print("Render accepted deployment " + deployment)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as outputs:
        print("deployment=" + deployment, file=outputs)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as report:
        report.write(f"Render accepted `{deployment}` for `{os.environ['GITHUB_SHA']}`. "
                     "Verify LIVE revision, health, protected routes and normally due worker receipts in Render.\n")
