"""Validate the protected release ref and provider-issued approvals. Read-only GitHub API."""
import argparse
import json
import os
import re
from urllib.request import Request, urlopen

REPOSITORY = "KKnFinite/NeoApps"
ENVIRONMENTS = ("neoapps-migration", "neoapps-render-release", "neoapps-live-verification")
WORKFLOW = f"{REPOSITORY}/.github/workflows/database-release.yml@refs/heads/main"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def github(path):
    request = Request(f"https://api.github.com/repos/{REPOSITORY}/{path}",
                      headers={"Accept": "application/vnd.github+json",
                               "Authorization": "Bearer " + os.environ["GH_TOKEN"]})
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def validate(env, read, approval_for=None):
    sha = env["GITHUB_SHA"]
    previous = env["PREVIOUS_LIVE_SHA"]
    require(env["GITHUB_REPOSITORY"] == REPOSITORY and env["GITHUB_EVENT_NAME"] == "workflow_dispatch"
            and env["GITHUB_WORKFLOW_REF"] == WORKFLOW and env["GITHUB_REF"] == "refs/heads/main"
            and env["GITHUB_RUN_ATTEMPT"] == "1" and env["GITHUB_RUN_ID"].isdigit(),
            "Use a new manual dispatch of the main release workflow")
    require(re.fullmatch("[0-9a-f]{40}", sha) and sha == env["APPROVED_SHA"]
            and re.fullmatch("[0-9a-f]{40}", previous), "Expected exact release and fallback SHAs")
    branch = read("branches/main")
    require(branch["protected"], "Main must be protected")
    if approval_for != "neoapps-live-verification":
        require(branch["commit"]["sha"] == sha, "Main has moved since dispatch")
    require(read(f"compare/{previous}...{sha}")["status"] in ("ahead", "identical"),
            "Previous live revision is outside the approved release history")
    rules = read("rules/branches/main?per_page=100")
    require(any(r["type"] == "pull_request" and r["parameters"]["required_approving_review_count"] >= 1
                and r["parameters"]["dismiss_stale_reviews_on_push"]
                and r["parameters"]["require_code_owner_review"] for r in rules),
            "Active main ruleset must require fresh, code-owner-reviewed changes")
    for name in (ENVIRONMENTS if not approval_for else [approval_for]):
        environment = read(f"environments/{name}")
        require(environment.get("can_admins_bypass") is not True, "Disable environment approval bypass")
        policy = environment["deployment_branch_policy"]
        require(policy and policy["custom_branch_policies"] and not policy["protected_branches"],
                "Require only the named main branch in each release environment")
        response = read(f"environments/{name}/deployment-branch-policies?per_page=100")
        require(response["total_count"] == 1 and
                [(r["name"], r["type"]) for r in response["branch_policies"]] == [("main", "branch")],
                "Expected exactly one deployment branch and no deployment tags")
        reviewers = {r["reviewer"]["id"] for rule in environment["protection_rules"]
                     if rule["type"] == "required_reviewers" for r in rule["reviewers"] if r["type"] == "User"}
        require(reviewers, "An explicitly authorized named reviewer is required")
        if approval_for:
            history = read(f"actions/runs/{env['GITHUB_RUN_ID']}/approvals")
            reviews = [a for a in history if any(e["id"] == environment["id"] for e in a["environments"])]
            require(reviews and all(a["state"] == "approved" for a in reviews)
                    and any(a["user"]["id"] in reviewers for a in reviews),
                    "No matching approval on this workflow run")
            approved = [a for a in reviews if a["user"]["id"] in reviewers]
            if approval_for == "neoapps-live-verification":
                deploy = env["DEPLOYMENT_ID"]
                require(re.fullmatch("dep-[a-z0-9]+", deploy), "Expected recorded deployment ID")
                receipt = f"LIVE {sha} {deploy} AUTH PASS WORKERS PASS"
                approved = [a for a in approved if a.get("comment", "").strip() == receipt]
                require(approved, "Record the observed live SHA, deployment, authorization and worker results")
            return approved[0]["user"]["login"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-environment", choices=ENVIRONMENTS)
    approver = validate(os.environ, github, parser.parse_args().approved_environment)
    print("Approved by " + approver if approver else "Protected main and release environments verified")
