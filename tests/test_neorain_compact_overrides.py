"""Server rendering shares compact controls on both flight boards."""
import re
import unittest

from app.extensions import db
from tests import test_neorain_overrides as fixtures


class CompactRainTemplateTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RainOverrideTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_boards_render_inline_inputs_and_one_shared_reset_without_source_labels(self):
        for board in ('inbound', 'outbound'):
            response = self.fixture.client.get('/neorain/' + board)
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertNotIn('<small data-rain-override-source', html)
            self.assertNotIn('>Use Google</button>', html)
            inputs = re.findall(r'<input[^>]*data-rain-override-time[^>]*>', html)
            self.assertTrue(inputs)
            for control in inputs:
                self.assertIn('maxlength="5"', control)
                self.assertIn('inputmode="numeric"', control)
                self.assertIn('(HHMM)', control)
                self.assertRegex(control, r'value="(?:[0-9]{2}:[0-9]{2})?"')
            self.assertNotIn('data-rain-override-editor', html)
            self.assertNotIn('data-rain-override-open', html)
            self.assertNotIn('data-rain-override-reset', html)
            self.assertEqual(html.count('type="button" data-rain-reset-selected'), 1)
            self.assertIn('20261016-rain-ui-v5', html)
        self.assertIn('aria-label="Include for UPS948"', html)
        self.assertNotIn('>Include</label>', html)
        self.assertNotIn('>No Return</label>', html)

    def test_cancelled_mission_has_no_override_edit_or_reset_controls(self):
        self.fixture.arrival.arrival_status = 'cancelled'
        self.fixture.departure.departure_status = 'cancelled'
        db.session.commit()
        for board in ('inbound', 'outbound'):
            html = self.fixture.client.get('/neorain/' + board).get_data(as_text=True)
            # Match HTML attributes, excluding the CSS/JS selector definitions.
            self.assertNotRegex(html, r'<input[^>]*data-rain-override-time')

    def test_watcher_keeps_readouts_without_editing_controls(self):
        watcher = self.fixture.fixture._user('compact_watcher', 'watcher', rain_role='watcher')
        self.fixture.fixture._login(watcher)
        for board in ('inbound', 'outbound'):
            response = self.fixture.client.get('/neorain/' + board)
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn('<span data-rain-override-readout>', html)
            self.assertNotRegex(html, r'<input[^>]*data-rain-override-time')
