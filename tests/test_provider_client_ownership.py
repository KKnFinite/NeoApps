import unittest
from unittest.mock import Mock, patch

from app.services import frost_vault as frost, neoscorpion_learning_vault as spear
from tests.test_frost_vault import MemoryR2


class ProviderClientOwnershipTest(unittest.TestCase):
    def test_nested_frost_scope_borrows_client_without_early_close(self):
        client = MemoryR2()
        client.close = Mock()
        with patch.object(spear, '_require_r2_config', return_value={'bucket': 'fixture'}), patch.object(spear, '_r2_client', return_value=client) as factory:
            with frost.transport_scope():
                key = frost.put_json('weather', 1, 'ownership', {'source': 'fixture'})
                with frost.transport_scope():
                    self.assertEqual(frost.read_json(key, 1), {'source': 'fixture'})
                client.close.assert_not_called()
            client.close.assert_called_once()
            factory.assert_called_once()

    def test_frost_cleanup_failure_preserves_verified_result_and_original_error(self):
        client = MemoryR2()
        client.close = Mock(side_effect=RuntimeError('private cleanup detail'))
        with patch.object(spear, '_require_r2_config', return_value={'bucket': 'fixture'}), patch.object(spear, '_r2_client', return_value=client):
            with self.assertLogs('app.services.frost_vault', level='WARNING') as logs:
                key = frost.put_json('weather', 1, 'close-error', {'source': 'fixture'})
            self.assertNotIn('private cleanup detail', str(logs.output))
            self.assertTrue(key.endswith('.json.gz'))
            with self.assertLogs('app.services.frost_vault', level='WARNING'):
                with self.assertRaises(spear.LearningVaultUnavailable):
                    frost.read_bytes(frost.namespace(1, 'weather') + 'missing', 1)
        self.assertIsNone(frost._transport_scope.get())

    def test_configuration_switch_closes_each_owned_client_once(self):
        first, second = Mock(), Mock()
        with patch.object(spear, '_r2_client', side_effect=[first, second]):
            with frost.transport_scope():
                self.assertIs(frost._client({'bucket': 'one'}), first)
                self.assertIs(frost._client({'bucket': 'two'}), second)
                first.close.assert_called_once()
                second.close.assert_not_called()
            second.close.assert_called_once()

    def test_failed_switch_does_not_close_previous_client_twice(self):
        first = Mock()
        with patch.object(spear, '_r2_client', side_effect=[first, RuntimeError('failed')]):
            with self.assertRaises(RuntimeError):
                with frost.transport_scope():
                    frost._client({'bucket': 'one'})
                    frost._client({'bucket': 'two'})
        first.close.assert_called_once()

    def test_no_unowned_frost_client_can_escape(self):
        with patch.object(spear, '_r2_client') as factory:
            with self.assertRaises(RuntimeError):
                frost._client({'bucket': 'one'})
            factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
