from datetime import date, datetime
from decimal import Decimal
import gzip
import io
import json
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.neoscorpion_learning_vault import (
    LearningVaultUnavailable,
    archive_calibration_review,
    learning_vault_status,
    list_calibration_reviews,
    read_calibration_review,
    test_learning_vault_connection as check_vault_connection,
)
from app.services import neoscorpion_learning_vault as vault
from app.services.neoscorpion_spear_calibration import calibration_review_payload


CONFIG = {
    "SPEAR_VAULT_PROVIDER": "r2", "SPEAR_VAULT_BUCKET": "spear-vault",
    "SPEAR_VAULT_REGION": "auto", "SPEAR_VAULT_ENDPOINT": "https://r2.example",
    "SPEAR_VAULT_ACCESS_KEY_ID": "secret-id", "SPEAR_VAULT_SECRET_ACCESS_KEY": "secret-key",
}


class _Client:
    def __init__(self): self.objects = {}
    def put_object(self, **kwargs): self.objects[kwargs["Key"]] = kwargs["Body"]
    def head_object(self, **kwargs):
        if kwargs["Key"] not in self.objects:
            error = RuntimeError("missing")
            error.response = {"ResponseMetadata": {"HTTPStatusCode": 404}}
            raise error
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
        self.assertTrue(check_vault_connection(CONFIG))
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
            check_vault_connection(CONFIG)
        self.assertNotIn("secret", str(error.exception).lower())

    def _save(self, review=None):
        with patch.object(vault, "_r2_client", return_value=self.client):
            return archive_calibration_review(review or self.review, gateway=self.gateway,
                operation=self.operation, user=self.user, learning_capture_enabled=False, _config=CONFIG)

    def _read(self, key):
        with patch.object(vault, "_r2_client", return_value=self.client):
            return read_calibration_review(key, CONFIG)

    def test_current_and_historical_checksum_semantics(self):
        for review in (self.review, calibration_review_payload(self.operation, {})):
            with self.subTest(fields=list(review)):
                saved = self._save(review)
                payload = self._read(saved["key"])
                expected = hashlib.sha256(vault._canonical_json(review)).hexdigest()
                self.assertEqual(payload["checksum"], expected)
                self.assertNotEqual(hashlib.sha256(vault._canonical_json(payload)).hexdigest(), expected)
                self.assertFalse(payload["learning_capture_enabled"])

    def test_large_canonical_aggregate_fixture_has_substantial_budget_headroom(self):
        item = SimpleNamespace(metric="pump_rate", scope_key="B757", configured=Decimal('120'),
            observed=None, effective=Decimal('120'), samples=0, excluded_samples=0, confidence="COLLECTING", active=False)
        review = calibration_review_payload(self.operation, {i: item for i in range(4096)})
        self.assertLess(len(vault._canonical_json(review)), 1_000_000)
        saved = self._save(review)
        self.assertEqual(len(self._read(saved["key"])["calibrations"]), 4096)
        self.assertEqual(vault.MAX_DECOMPRESSED_REVIEW_BYTES, 4_000_000)

    def test_compressed_limit_and_provider_stream_are_bounded_and_closed(self):
        saved = self._save()
        body = io.BytesIO(b'x' * (vault.MAX_REVIEW_BYTES + 200))
        with patch.object(self.client, "get_object", return_value={"Body": body}), patch.object(vault, "_r2_client", return_value=self.client):
            with self.assertRaisesRegex(ValueError, "too large"):
                read_calibration_review(saved["key"], CONFIG)
        self.assertTrue(body.closed)

    def test_bomb_cannot_allocate_past_output_budget(self):
        saved = self._save()
        bomb = gzip.compress(b' ' * (vault.MAX_DECOMPRESSED_REVIEW_BYTES * 8))
        self.assertLess(len(bomb), vault.MAX_REVIEW_BYTES)
        self.client.objects[saved["key"]] = bomb
        real_factory = vault.zlib.decompressobj
        calls = []
        class BoundedInflater:
            def __init__(self, *args): self.real = real_factory(*args)
            def decompress(self, data, max_length):
                calls.append(max_length)
                result = self.real.decompress(data, max_length)
                assert len(result) <= vault.MAX_DECOMPRESSED_REVIEW_BYTES + 1
                return result
            def __getattr__(self, name): return getattr(self.real, name)
        with patch.object(vault.zlib, "decompressobj", BoundedInflater):
            with self.assertRaisesRegex(ValueError, "too large"):
                self._read(saved["key"])
        self.assertEqual(calls, [vault.MAX_DECOMPRESSED_REVIEW_BYTES + 1])

    def test_corrupt_truncated_concatenated_and_trailing_gzip_fail_closed(self):
        saved = self._save()
        valid = self.client.objects[saved["key"]]
        broken_crc = valid[:-8] + bytes([valid[-8] ^ 1]) + valid[-7:]
        for body in (b'not-gzip-provider-secret', valid[:-1], broken_crc, valid + gzip.compress(b'{}'), valid + b'trailing'):
            with self.subTest(size=len(body)):
                self.client.objects[saved["key"]] = body
                with self.assertRaisesRegex(ValueError, '^Stored SPEAR Vault review is malformed.$'):
                    self._read(saved["key"])

    def test_decompressed_budget_accepts_exact_limit_and_rejects_next_byte(self):
        saved = self._save()
        raw = gzip.decompress(self.client.objects[saved["key"]])
        padded = raw + b' ' * (vault.MAX_DECOMPRESSED_REVIEW_BYTES - len(raw))
        self.client.objects[saved["key"]] = gzip.compress(padded)
        self.assertEqual(self._read(saved["key"])["checksum"], saved["checksum"])
        self.client.objects[saved["key"]] = gzip.compress(padded + b' ')
        with self.assertRaisesRegex(ValueError, "too large"):
            self._read(saved["key"])

    def test_validly_shaped_calibration_tampering_fails_checksum(self):
        item = SimpleNamespace(metric="pump_rate", scope_key="B757", configured=Decimal('120'),
            observed=None, effective=Decimal('120'), samples=0, excluded_samples=0, confidence="COLLECTING", active=False)
        saved = self._save(calibration_review_payload(self.operation, {"pump": item}))
        payload = self._read(saved["key"])
        payload["calibrations"][0]["effective"] = "999.0"
        self.client.objects[saved["key"]] = gzip.compress(vault._canonical_json(payload))
        with self.assertRaisesRegex(ValueError, '^Stored SPEAR Vault review is malformed.$'):
            self._read(saved["key"])

    def test_invalid_utf8_json_duplicates_and_nonfinite_are_sanitized(self):
        saved = self._save()
        for raw in (b'\xff', b'{"secret":"provider-body"', b'{"a":1,"a":2}', b'{"a":NaN}', b'[' * 1500):
            with self.subTest(raw=raw[:20]):
                self.client.objects[saved["key"]] = gzip.compress(raw)
                with self.assertRaisesRegex(ValueError, '^Stored SPEAR Vault review is malformed.$'):
                    self._read(saved["key"])

    def test_structure_and_tampering_rejected(self):
        saved = self._save(calibration_review_payload(self.operation, {}))
        valid = self._read(saved["key"])
        mutations = [[], dict(valid, checksum='0'*64), dict(valid, schema_version='v99'),
            dict(valid, training_eligible=True), dict(valid, capture_mode='automatic'),
            dict(valid, operation_id=1234), dict(valid, calibrations={}),
            dict(valid, calibrations=[{"metric": "pump_rate"}]), dict(valid, saved_at='bad'),
            dict(valid, gateway={"id": 99, "code": "RFD"}), dict(valid, secret='unexpected')]
        for payload in mutations:
            with self.subTest(payload_type=type(payload).__name__):
                self.client.objects[saved["key"]] = gzip.compress(vault._canonical_json(payload))
                with self.assertRaisesRegex(ValueError, '^Stored SPEAR Vault review is malformed.$'):
                    self._read(saved["key"])

    def test_object_metadata_checksum_mismatch_rejected(self):
        saved = self._save()
        with patch.object(self.client, "get_object", return_value={"Body": io.BytesIO(self.client.objects[saved["key"]]), "Metadata": {"checksum": "0"*64}}):
            with self.assertRaisesRegex(ValueError, "malformed"):
                self._read(saved["key"])

    def test_head_failure_does_not_attempt_write_or_leak_provider_error(self):
        with patch.object(self.client, "head_object", side_effect=ValueError('SECRET SQL endpoint')), patch.object(self.client, "put_object") as put:
            with self.assertRaisesRegex(LearningVaultUnavailable, '^SPEAR Vault connection failed') as error:
                self._save()
            self.assertNotIn('SECRET', str(error.exception))
            put.assert_not_called()

    def test_provider_failures_on_read_list_create_and_delete_are_sanitized(self):
        saved = self._save()
        for method, action in (("get_object", lambda: self._read(saved["key"])),
                ("list_objects_v2", lambda: list_calibration_reviews(_config=CONFIG)),
                ("delete_object", lambda: check_vault_connection(CONFIG))):
            with self.subTest(method=method), patch.object(vault, "_r2_client", return_value=self.client), patch.object(self.client, method, side_effect=ValueError('SECRET bucket endpoint')):
                with self.assertRaisesRegex(LearningVaultUnavailable, '^SPEAR Vault connection failed') as error:
                    action()
                self.assertNotIn('SECRET', str(error.exception))
        with patch.object(vault, '_r2_client', side_effect=ValueError('SECRET client')):
            with self.assertRaises(LearningVaultUnavailable):
                archive_calibration_review(self.review, gateway=self.gateway, operation=self.operation, user=self.user, learning_capture_enabled=False, _config=CONFIG)

    def test_actual_botocore_config_has_timeouts_and_one_total_attempt(self):
        with patch('boto3.client') as create:
            vault._r2_client(vault._vault_config(CONFIG))
        config = create.call_args.kwargs['config']
        self.assertEqual(config.connect_timeout, 5)
        self.assertEqual(config.read_timeout, 10)
        self.assertEqual(config.retries, {'mode': 'standard', 'total_max_attempts': 1})
        self.assertEqual(config.signature_version, 's3v4')
        self.assertEqual(create.call_args.kwargs['region_name'], 'auto')


if __name__ == "__main__": unittest.main()
