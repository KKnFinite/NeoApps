"""Bounded R2 failure/recovery proofs; no external storage or operational data."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import gzip
import io
import json
import unittest
from threading import Barrier, Lock
from unittest.mock import Mock, patch

from app.services import neoscorpion_learning_vault as vault
from app.services.neosubzero_reporting import learning_context
from tests import test_neoscorpion_learning_vault as fixtures


def provider_error(status):
    error = RuntimeError('synthetic provider failure')
    error.response = {'ResponseMetadata': {'HTTPStatusCode': status}}
    return error


class MemoryR2:
    def __init__(self):
        self.objects = {}
        self.metadata = {}
        self.puts = 0
        self.gets = 0
        self.streams = []
        self.close = Mock()
        self.mode = None
        self.lock = Lock()

    def get_object(self, **kwargs):
        self.gets += 1
        key = kwargs['Key']
        if self.mode == 'get_failure':
            raise RuntimeError('private provider details')
        if key not in self.objects:
            raise provider_error(404)
        if self.mode == 'verification_failure':
            raise RuntimeError('read-back temporarily unavailable')
        body = self.objects[key]
        if self.mode == 'corrupt':
            body = b'corrupt'
        stream = io.BytesIO(body)
        self.streams.append(stream)
        return dict(Body=stream, ContentLength=len(body), Metadata=self.metadata.get(key, {}))

    def put_object(self, **kwargs):
        self.puts += 1
        assert kwargs['IfNoneMatch'] == '*'
        if self.mode == 'put_failure':
            raise RuntimeError('private credentials must not escape')
        with self.lock:
            if kwargs['Key'] in self.objects:
                raise provider_error(412)
            self.objects[kwargs['Key']] = kwargs['Body']
            self.metadata[kwargs['Key']] = kwargs['Metadata']
        if self.mode == 'lost_response':
            raise RuntimeError('accepted but acknowledgement lost')


class ArchiveContentVerificationTest(unittest.TestCase):
    def setUp(self):
        self.client = MemoryR2()
        self.provider = patch.object(vault, '_r2_client', return_value=self.client)
        self.provider.start()
        self.addCleanup(self.provider.stop)
        self.record = fixtures.SpearVaultR2Test()._learning_record()

    def save(self):
        return vault.export_learning_record(self.record, fixtures.CONFIG)

    def test_put_and_existing_objects_require_independent_get_and_close_owned_resources(self):
        first = self.save()
        self.assertTrue(first['verified'])
        self.assertEqual(self.client.gets, 2)
        self.assertEqual(self.client.puts, 1)
        self.assertTrue(all(stream.closed for stream in self.client.streams))
        self.client.close.assert_called_once()
        second = self.save()
        self.assertTrue(second['verified'])
        self.assertTrue(second['already_saved'])
        self.assertEqual(self.client.puts, 1)

    def test_upload_failure_and_lost_ack_are_pending_then_recover_without_overwrite(self):
        for mode in ('put_failure', 'lost_response', 'verification_failure'):
            with self.subTest(mode=mode):
                self.client.objects.clear()
                self.client.mode = mode
                with self.assertRaises(vault.LearningVaultUnavailable):
                    self.save()
                self.client.mode = None
                puts = self.client.puts
                recovered = self.save()
                self.assertTrue(recovered['verified'])
                self.assertEqual(self.client.puts, puts + (mode == 'put_failure'))

    def test_corrupt_existing_object_with_matching_metadata_is_not_overwritten(self):
        saved = self.save()
        original = self.client.objects[saved['key']]
        for body in (b'invalid', original[:-2], original + b'trailing', original + original):
            with self.subTest(size=len(body)):
                self.client.objects[saved['key']] = body
                with self.assertRaises(vault.LearningVaultUnavailable):
                    self.save()
                self.assertEqual(self.client.puts, 1)
                self.assertEqual(self.client.objects[saved['key']], body)
        self.assertTrue(all(stream.closed for stream in self.client.streams))

    def test_schema_identity_and_payload_mismatch_rejected_even_with_expected_metadata(self):
        saved = self.save()
        original = json.loads(gzip.decompress(self.client.objects[saved['key']]))
        for field, value in (('schema_version', 'spear-learning/future'), ('record_type', 'invented')):
            bad = {**original, field: value}
            self.client.objects[saved['key']] = gzip.compress(vault._canonical_json(bad))
            with self.assertRaises(vault.LearningVaultUnavailable):
                self.save()
        bad = deepcopy(original)
        bad['mission']['id'] += 1
        self.client.objects[saved['key']] = gzip.compress(vault._canonical_json(bad))
        with self.assertRaises(vault.LearningVaultUnavailable):
            self.save()
        self.assertEqual(self.client.puts, 1)

    def test_compressed_and_decompressed_limits_reject_bombs_without_unbounded_reads(self):
        saved = self.save()
        for body in (b'x' * (vault.MAX_REVIEW_BYTES + 1),
                     gzip.compress(b'x' * (vault.MAX_DECOMPRESSED_REVIEW_BYTES + 1))):
            self.client.objects[saved['key']] = body
            with self.assertRaises(vault.LearningVaultUnavailable):
                self.save()
        self.assertTrue(all(stream.closed for stream in self.client.streams))

    def test_nonfinite_duplicate_json_and_metadata_mismatch_rejected(self):
        saved = self.save()
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'\xff'):
            self.client.objects[saved['key']] = gzip.compress(raw)
            with self.assertRaises(vault.LearningVaultUnavailable):
                self.save()
        self.client.objects.clear()
        self.save()
        self.client.metadata[saved['key']]['checksum'] = '0' * 64
        with self.assertRaises(vault.LearningVaultUnavailable):
            self.save()

    def test_no_absence_inference_after_get_failure_and_safe_messages(self):
        self.client.mode = 'get_failure'
        with self.assertRaises(vault.LearningVaultUnavailable) as error:
            self.save()
        self.assertNotIn('private', str(error.exception))
        self.assertEqual(self.client.puts, 0)
        self.client.close.assert_called_once()

    def test_conditional_create_race_preserves_one_immutable_object(self):
        first_read = Barrier(2)
        original = self.client.get_object
        def get(**kwargs):
            if kwargs['Key'] not in self.client.objects:
                first_read.wait(timeout=10)
                raise provider_error(404)
            return original(**kwargs)
        with patch.object(self.client, 'get_object', side_effect=get):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: self.save(), range(2)))
        self.assertTrue(all(result['verified'] for result in results))
        self.assertEqual(sorted(result['already_saved'] for result in results), [False, True])
        self.assertEqual(len(self.client.objects), 1)
        self.assertEqual(self.client.puts, 2)  # One create and one rejected conditional PUT.

    def test_installed_sdk_supports_atomic_conditional_put(self):
        from botocore.session import Session
        shape = Session().get_service_model('s3').operation_model('PutObject').input_shape
        self.assertIn('IfNoneMatch', shape.members)

    def test_decision_and_subzero_application_context_share_verification_contract(self):
        reason = dict(schema_version='spear-decision/v1', record_type='reason', id='1' * 32,
            gateway_id=4, operation_id=9, mission_id=41, assignment_id=21, cycle_number=1,
            captured_at='2026-09-03T05:30:00Z', training_eligible=False,
            payload=dict(decision_id='2' * 32, actor_id=12, reason=None, note='synthetic fixture'))
        spray = dict(schema_version='subzero-spray/v1', record_type='actual_fluid_application',
            id='departure:1:pass:1', gateway_id=4, operation_id=9, tail='N412UP',
            start_utc='2026-09-03T05:30:00Z')
        functions = (lambda: vault.export_decision_record(reason, fixtures.CONFIG),
            lambda: vault.export_subzero_record(spray, _config=fixtures.CONFIG),
            lambda: vault.export_subzero_record(learning_context(spray), learning=True, _config=fixtures.CONFIG))
        for save in functions:
            result = save()
            self.assertTrue(result['verified'])
            self.assertTrue(save()['already_saved'])
            self.client.objects[result['key']] = gzip.compress(b'{}')
            puts = self.client.puts
            with self.assertRaises(vault.LearningVaultUnavailable):
                save()
            self.assertEqual(self.client.puts, puts)
