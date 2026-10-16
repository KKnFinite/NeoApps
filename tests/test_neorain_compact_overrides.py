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

    def test_boards_render_values_icons_and_accessible_names_without_source_labels(self):
        for board in ('inbound', 'outbound'):
            response = self.fixture.client.get('/neorain/' + board)
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertNotIn('<small data-rain-override-source', html)
            self.assertNotIn('>Use Google</button>', html)
            readouts = re.findall(r'<span data-rain-override-readout>(.*?)</span>', html)
            self.assertTrue(readouts)
            self.assertTrue(all(re.fullmatch(r'\d{2}:\d{2}|\u2014', value) for value in readouts))
            self.assertIn('data-rain-override-editor hidden', html)
            self.assertIn('aria-expanded="false"', html)
            self.assertIn('aria-label="Reset ', html)
            self.assertIn('20261015-compact-overrides-v2', html)
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
            self.assertNotIn('type="button" data-rain-override-open', html)
            self.assertNotIn('type="button" data-rain-override-reset', html)

    def test_watcher_keeps_readouts_without_editing_controls(self):
        watcher = self.fixture.fixture._user('compact_watcher', 'watcher', rain_role='watcher')
        self.fixture.fixture._login(watcher)
        for board in ('inbound', 'outbound'):
            response = self.fixture.client.get('/neorain/' + board)
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn('<span data-rain-override-readout>', html)
            self.assertNotIn('type="button" data-rain-override-open', html)
            self.assertNotIn('type="button" data-rain-override-reset', html)
