"""Validate the protected release ref and provider-issued approvals. Read-only GitHub API."""
import argparse
import json
import os
import re
from urllib.request import Request, urlopen

REPOSITORY = "KKnFinite/NeoApps"
REPOSITORY_ID = 1247201616
OWNER_ID = 271867764
CHECK = {"context": "isolation", "integration_id": 15368}
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


def check_dispatch(env):
    require(env["GITHUB_REPOSITORY"] == REPOSITORY and env["GITHUB_EVENT_NAME"] == "workflow_dispatch"
            and env["GITHUB_WORKFLOW_REF"] == WORKFLOW and env["GITHUB_REF"] == "refs/heads/main"
            and env["GITHUB_RUN_ATTEMPT"] == "1" and env["GITHUB_RUN_ID"].isdigit(),
            "Use a new manual dispatch of the main release workflow")
    require(env["GITHUB_ACTOR_ID"] == str(OWNER_ID)
            and env["GITHUB_REPOSITORY_ID"] == str(REPOSITORY_ID)
            and env["GITHUB_REPOSITORY_OWNER_ID"] == str(OWNER_ID),
            "Only the pinned NeoApps owner can initiate this release")


def validate(env, read, approval_for=None):
    check_dispatch(env)
    sha = env["GITHUB_SHA"]
    previous = env["PREVIOUS_LIVE_SHA"]
    require(re.fullmatch("[0-9a-f]{40}", sha) and sha == env["APPROVED_SHA"]
            and re.fullmatch("[0-9a-f]{40}", previous), "Expected exact release and fallback SHAs")
    run_path = f"actions/runs/{env['GITHUB_RUN_ID']}"
    run = read(run_path)
    require(run["event"] == "workflow_dispatch" and run["run_attempt"] == 1
            and run["head_sha"] == sha and run["head_branch"] == "main"
            and run["path"] == ".github/workflows/database-release.yml"
            and run["repository"]["id"] == REPOSITORY_ID
            and run["actor"]["id"] == run["triggering_actor"]["id"] == OWNER_ID,
            "GitHub did not record a first-attempt owner dispatch of this revision")
    branch = read("branches/main")
    require(branch["protected"], "Main must be protected")
    if approval_for != "neoapps-live-verification":
        require(branch["commit"]["sha"] == sha, "Main has moved since dispatch")
    require(read(f"compare/{previous}...{sha}")["status"] in ("ahead", "identical"),
            "Previous live revision is outside the approved release history")
    rules = read("rules/branches/main?per_page=100")
    require(any(r["type"] == "pull_request" for r in rules)
            and {"deletion", "non_fast_forward"}.issubset(r["type"] for r in rules)
            and any(r["type"] == "required_status_checks" and any(
                check.get("context") == CHECK["context"] and check.get("integration_id") == CHECK["integration_id"]
                for check in r["parameters"]["required_status_checks"]) for r in rules),
            "Main requires a pull request, the GitHub rehearsal check, and deletion/force-push protection")
    merged = [pr for pr in read(f"commits/{sha}/pulls?per_page=100")
              if pr["merged_at"] and pr["merge_commit_sha"] == sha
              and pr["base"]["repo"]["full_name"] == REPOSITORY and pr["base"]["ref"] == "main"]
    require(len(merged) == 1, "The exact release must be the result of one merged main pull request")
    pr = read(f"pulls/{int(merged[0]['number'])}")
    require(pr["merged"] and pr["merge_commit_sha"] == sha and pr["merged_by"]["id"] == OWNER_ID,
            "The repository owner must merge the release pull request")
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
        reviewer_rules = [r for r in environment["protection_rules"] if r["type"] == "required_reviewers"]
        require(len(reviewer_rules) == 1 and reviewer_rules[0].get("prevent_self_review") is False
                and [(r["type"], r["reviewer"]["id"]) for r in reviewer_rules[0]["reviewers"]]
                == [("User", OWNER_ID)], "Configure the owner as the sole environment approver and allow self-review")
        if approval_for:
            history = read(run_path + "/approvals")
            reviews = [a for a in history if any(e["id"] == environment["id"] for e in a["environments"])]
            require(reviews and all(a["state"] == "approved" and a["user"]["id"] == OWNER_ID for a in reviews),
                    "Expected explicit owner approval for this run and environment")
            approved = reviews
            if approval_for == "neoapps-migration":
                approved = [a for a in reviews if a.get("comment", "").strip() == f"MIGRATE {sha}"]
                require(approved, "Confirm migration with MIGRATE and the exact release SHA")
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
    print("Confirmed by " + approver if approver else "Owner dispatch, protected main and release environments verified")
