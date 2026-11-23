"""Credential-free failure and ordering checks for the hosted release entry points."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.release import github_gate, migrate, render_deploy

SHA = "a" * 40
PREVIOUS = "b" * 40
DEPLOY = "dep-fixture123"


class ReleaseChecks(unittest.TestCase):
    def setUp(self):
        self.env = dict(GITHUB_SHA=SHA, APPROVED_SHA=SHA, PREVIOUS_LIVE_SHA=PREVIOUS, GITHUB_RUN_ID="10",
                        GITHUB_RUN_ATTEMPT="1", GITHUB_EVENT_NAME="workflow_dispatch",
                        GITHUB_WORKFLOW_REF=github_gate.WORKFLOW, GITHUB_REF="refs/heads/main",
                        GITHUB_REPOSITORY=github_gate.REPOSITORY, DEPLOYMENT_ID=DEPLOY)
        self.endpoints = {"branches/main": {"protected": True, "commit": {"sha": SHA}},
                          f"compare/{PREVIOUS}...{SHA}": {"status": "ahead"},
                          "rules/branches/main?per_page=100": [{"type": "pull_request", "parameters": {
                              "required_approving_review_count": 1, "dismiss_stale_reviews_on_push": True,
                              "require_code_owner_review": True}}]}
        for idx, name in enumerate(github_gate.ENVIRONMENTS):
            self.endpoints[f"environments/{name}"] = {
                "id": idx+1, "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
                "protection_rules": [{"type": "required_reviewers", "reviewers": [{"type": "User", "reviewer": {"id": 9}}]}]}
            self.endpoints[f"environments/{name}/deployment-branch-policies?per_page=100"] = {
                "total_count": 1, "branch_policies": [{"name": "main", "type": "branch"}]}
        self.reviews = [{"state": "approved", "user": {"id": 9, "login": "authorized"},
                         "environments": [{"id": 1}]}]
        self.endpoints["actions/runs/10/approvals"] = self.reviews

    def gate(self, name=None):
        return github_gate.validate(self.env, self.endpoints.__getitem__, name)

    def test_manual_main_and_named_approval(self):
        self.gate()
        self.assertEqual(self.gate("neoapps-migration"), "authorized")

    def test_untrusted_contexts_denied_before_inspection(self):
        for bad in ({"GITHUB_EVENT_NAME":"pull_request"}, {"GITHUB_EVENT_NAME":"pull_request_target"},
                    {"GITHUB_REF":"refs/heads/feature"}, {"GITHUB_RUN_ATTEMPT":"2"},
                    {"GITHUB_REPOSITORY":"other/NeoApps"}, {"APPROVED_SHA":PREVIOUS},
                    {"PREVIOUS_LIVE_SHA":"unverified"}, {"GITHUB_WORKFLOW_REF":"other.yml"}):
            with self.subTest(keys=list(bad)), self.assertRaises(ValueError):
                github_gate.validate(dict(self.env, **bad), lambda _: self.fail("unexpected API request"))

    def test_main_advance_or_missing_review_rules(self):
        self.endpoints["branches/main"]["commit"]["sha"] = PREVIOUS
        with self.assertRaises(ValueError):
            self.gate()
        self.endpoints["branches/main"]["commit"]["sha"] = SHA
        self.endpoints["rules/branches/main?per_page=100"] = []
        with self.assertRaises(ValueError):
            self.gate()

    def test_wildcard_branches_tags_and_extra_rules_denied(self):
        policy = self.endpoints["environments/neoapps-migration/deployment-branch-policies?per_page=100"]
        for rules in ([{"name":"main", "type":"tag"}], [{"name":"*", "type":"branch"}],
                      [{"name":"main", "type":"branch"}, {"name":"dev", "type":"branch"}]):
            policy.update(total_count=len(rules), branch_policies=rules)
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                self.gate()

    def test_wrong_reviewer_environment_rejection_and_missing_approval(self):
        for changes in ({"state":"rejected"}, {"user":{"id":1}}, {"environments":[{"id":2}]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.endpoints["actions/runs/10/approvals"] = [dict(self.reviews[0], **changes)]
                self.gate("neoapps-migration")
        self.endpoints["actions/runs/10/approvals"] = []
        with self.assertRaises(ValueError):
            self.gate("neoapps-migration")

    def test_live_verification_bound_to_deployment_revision_and_observations(self):
        self.reviews[0].update(environments=[{"id":3}], comment="ok")
        with self.assertRaises(ValueError):
            self.gate("neoapps-live-verification")
        self.reviews[0]["comment"] = f"LIVE {SHA} {DEPLOY} AUTH PASS WORKERS PASS"
        self.assertEqual(self.gate("neoapps-live-verification"), "authorized")

    def test_migration_subprocess_receives_only_necessary_inputs(self):
        env = dict(self.env, HOME="fixture", DATABASE_URL="postgresql://migration@fixture/db?sslmode=verify-full", RELEASE_MIGRATOR="migration",
                   RELEASE_STANDBY="fallback", RELEASE_RUNTIME="runtime", RELEASE_DATABASE="db", RELEASE_SCHEMA="public",
                   BOOTSTRAP_ADMIN_USERNAME="fixture", BOOTSTRAP_ADMIN_EMAIL="fixture@example.invalid",
                   GH_TOKEN="fixture-token", RENDER_DEPLOY_HOOK="fixture-hook", BOOTSTRAP_ADMIN_PASSWORD="unused")
        execute = Mock()
        migrate.run(env, execute)
        child = execute.call_args.kwargs["env"]
        self.assertEqual(child["NEOAPPS_EXISTING_BOOTSTRAP_ONLY"], "1")
        self.assertTrue({"GH_TOKEN", "RENDER_DEPLOY_HOOK", "BOOTSTRAP_ADMIN_PASSWORD"}.isdisjoint(child))
        self.assertNotIn(env["DATABASE_URL"], execute.call_args.args[0])
        for bad in ("postgresql://migration@fixture/db", "postgresql://runtime@fixture/db?sslmode=verify-full"):
            with self.assertRaises(ValueError):
                migrate.run(dict(env, DATABASE_URL=bad), execute)
        self.assertEqual(execute.call_count, 1)

    def test_render_pins_sha_disallows_redirects_and_does_not_retry_uncertain_requests(self):
        hook = "https://api.render.com/deploy/srv-test?key=fixture"
        def response(status, body):
            result = io.BytesIO(json.dumps(body).encode())
            result.status = status
            return result
        post = Mock(return_value=response(200, {"deploy":{"id":DEPLOY}}))
        self.assertEqual(render_deploy.deploy(hook, "srv-test", SHA, post), DEPLOY)
        request = post.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(parse_qs(urlsplit(request.full_url).query)["ref"], [SHA])
        for result in (response(202, {}), response(200, {}), response(302, {})):
            post = Mock(return_value=result)
            with self.assertRaises(ValueError):
                render_deploy.deploy(hook, "srv-test", SHA, post)
            post.assert_called_once()
        for bad in (hook.replace("api.render.com", "other.invalid"), hook + "&ref=main", hook.replace("srv-test", "srv-other")):
            with self.assertRaises(ValueError):
                render_deploy.deploy(bad, "srv-test", SHA, self.fail)
        self.assertIsNone(render_deploy.NoRedirect().redirect_request(None,None,302,"",{},"https://other.invalid"))
