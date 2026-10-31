"""Real spray/context payloads with a failure-capable private-storage fake."""
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from app.extensions import db
from app.models import NeoSubZeroArchiveDelivery as Delivery
from app.services import neosubzero_archive as archive, neoscorpion_learning_vault as vault
from tests import test_neosubzero_release3 as fixtures
from tests.test_archive_content_verification import MemoryR2
from tests import test_neoscorpion_learning_vault as config


class SubzeroVerifiedArchiveTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.Release3Test()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.client = MemoryR2()
        self.config = patch.object(vault, '_require_r2_config', return_value=vault._vault_config(config.CONFIG))
        self.transport = patch.object(vault, '_r2_client', return_value=self.client)
        self.config.start()
        self.transport.start()
        self.addCleanup(self.config.stop)
        self.addCleanup(self.transport.stop)

    def test_partial_pair_remains_pending_then_recovers_without_duplicate_spray_upload(self):
        self.fixture.flow()
        gateway_id = self.fixture.f.gateway.id
        original = self.client.put_object
        def put(**kwargs):
            if '/treatment-context/' in kwargs['Key']:
                raise RuntimeError('Context unavailable')
            return original(**kwargs)
        with patch.object(self.client, 'put_object', side_effect=put):
            self.assertEqual(archive.drain(gateway_id), 0)
        row = Delivery.query.one()
        self.assertIsNone(row.delivered_checksum)
        self.assertIsNone(row.verified_at)
        self.assertEqual(len(self.client.objects), 1)
        row.retry_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
        self.assertEqual(archive.drain(gateway_id), 1)
        self.assertEqual(len(self.client.objects), 2)
        self.assertEqual(self.client.puts, 2)
        self.assertIsNotNone(row.verified_at)
        self.assertEqual(archive.status(self.fixture.f.operation)['verified'], 1)

    def test_corrupt_existing_spray_is_never_overwritten_or_marked_delivered(self):
        self.fixture.flow()
        gateway_id = self.fixture.f.gateway.id
        self.assertEqual(archive.drain(gateway_id), 1)
        row = Delivery.query.one()
        spray_key = row.vault_key
        self.client.objects[spray_key] = b'corrupt despite valid metadata'
        row.delivered_checksum = None
        row.delivered_at = row.verified_at = None
        row.retry_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
        puts = self.client.puts
        self.assertEqual(archive.drain(gateway_id), 0)
        self.assertEqual(self.client.puts, puts)
        self.assertIsNone(row.delivered_checksum)
        self.assertIsNone(row.verified_at)
        self.assertTrue(row.last_error)

    def test_legacy_receipt_is_reported_separately_without_relabelling_or_reexport(self):
        self.fixture.flow()
        gateway_id = self.fixture.f.gateway.id
        archive.reconcile(gateway_id)
        row = Delivery.query.one()
        row.delivered_checksum = row.content_checksum
        row.delivered_at = datetime.utcnow()
        row.vault_key, row.learning_key = 'legacy/spray', 'legacy/context'
        db.session.commit()
        self.assertEqual(archive.drain(gateway_id), 0)
        result = archive.status(self.fixture.f.operation)
        self.assertEqual((result['verified'], result['legacy'], result['pending']), (0, 1, 0))
        self.assertIsNone(row.verified_at)
        self.assertEqual(self.client.puts, 0)
