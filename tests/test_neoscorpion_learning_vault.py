from datetime import date, datetime
import gzip
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.neoscorpion_learning_vault import (
    LearningVaultUnavailable,
    archive_calibration_review,
    learning_vault_status,
    list_calibration_reviews,
    read_calibration_review,
    test_learning_vault_connection,
)


CONFIG = {
    "SPEAR_VAULT_PROVIDER": "r2", "SPEAR_VAULT_BUCKET": "spear-vault",
    "SPEAR_VAULT_REGION": "auto", "SPEAR_VAULT_ENDPOINT": "https://r2.example",
    "SPEAR_VAULT_ACCESS_KEY_ID": "secret-id", "SPEAR_VAULT_SECRET_ACCESS_KEY": "secret-key",
}


class _Client:
    def __init__(self): self.objects = {}
    def put_object(self, **kwargs): self.objects[kwargs["Key"]] = kwargs["Body"]
    def head_object(self, **kwargs):
        if kwargs["Key"] not in self.objects: raise RuntimeError("missing")
        return {}
    def delete_object(self, **kwargs): self.objects.pop(kwargs["Key"], None)
    def list_objects_v2(self, **kwargs):
        return {"Contents": [{"Key": key, "LastModified": datetime(2026, 9, 3), "Size": len(value)} for key, value in self.objects.items()]}
    def get_object(self, **kwargs): return {"Body": io.BytesIO(self.objects[kwargs["Key"]])}


class SpearVaultR2Test(unittest.TestCase):
    def setUp(self):
        self.client = _Client()
        self.gateway = SimpleNamespace(id=4, code="RFD")
        self.operation = SimpleNamespace(id=9, sort_date=date(2026, 9, 3))
        self.user = SimpleNamespace(id=12)
        self.review = {"schema_version": "v1", "capture_mode": "live_calibration_review", "training_eligible": False, "calibrations": []}

    def test_safe_status_never_exposes_credentials(self):
        status = learning_vault_status(CONFIG)
        self.assertTrue(status.configured)
        self.assertEqual(status.label, "SPEAR VAULT · CONNECTED")
        self.assertNotIn("secret", repr(status).lower())

    @patch("app.services.neoscorpion_learning_vault._r2_client")
    def test_connection_puts_checks_and_deletes_health_object(self, client):
        client.return_value = self.client
        self.assertTrue(test_learning_vault_connection(CONFIG))
        self.assertEqual(self.client.objects, {})

    @patch("app.services.neoscorpion_learning_vault._r2_client")
    def test_manual_archive_is_gzip_private_and_idempotent_with_learning_off(self, client):
        client.return_value = self.client
        saved = archive_calibration_review(self.review, gateway=self.gateway, operation=self.operation, user=self.user, learning_capture_enabled=False, _config=CONFIG)
        duplicate = archive_calibration_review(self.review, gateway=self.gateway, operation=self.operation, user=self.user, learning_capture_enabled=False, _config=CONFIG)
        self.assertFalse(saved["already_saved"])
        self.assertTrue(duplicate["already_saved"])
        payload = json.loads(gzip.decompress(self.client.objects[saved["key"]]))
        self.assertFalse(payload["training_eligible"])
        self.assertFalse(payload["learning_capture_enabled"])

    @patch("app.services.neoscorpion_learning_vault._r2_client")
    def test_bounded_list_and_validated_read(self, client):
        client.return_value = self.client
        saved = archive_calibration_review(self.review, gateway=self.gateway, operation=self.operation, user=self.user, learning_capture_enabled=False, _config=CONFIG)
        self.assertEqual(len(list_calibration_reviews(_config=CONFIG)), 1)
        self.assertEqual(read_calibration_review(saved["key"], CONFIG)["capture_mode"], "manual_calibration_review")
        with self.assertRaisesRegex(ValueError, "Invalid"):
            read_calibration_review("../../secret", CONFIG)

    @patch("app.services.neoscorpion_learning_vault._r2_client", side_effect=RuntimeError("secret endpoint failed"))
    def test_provider_failure_is_sanitized(self, _client):
        with self.assertRaises(LearningVaultUnavailable) as error:
            test_learning_vault_connection(CONFIG)
        self.assertNotIn("secret", str(error.exception).lower())


if __name__ == "__main__": unittest.main()
