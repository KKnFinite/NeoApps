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

    def test_explicit_clear_suppresses_std_fallback_on_full_and_live_render(self):
        for board, mission, field in (('inbound', self.fixture.arrival, 'sta'),
                                      ('outbound', self.fixture.departure, 'etd')):
            self.assertEqual(self.fixture.edit(mission, field, '0125').status_code, 200)
            self.assertEqual(self.fixture.edit(mission, field, action='clear').status_code, 200)
            html = self.fixture.client.get('/neorain/' + board).get_data(as_text=True)
            inputs = re.findall(r'<input[^>]*aria-label="[^\"]+ \(HHMM\)"[^>]*data-rain-override-time[^>]*>', html)
            targets = [control for control in inputs if ('STA for' if field == 'sta' else 'ETD for') in control]
            self.assertTrue(targets, field)
            self.assertTrue(all('value=""' in control for control in targets), targets)
            self.assertEqual(self.fixture.edit(mission, field, action='reset').status_code, 200)
            html = self.fixture.client.get('/neorain/' + board).get_data(as_text=True)
            self.assertIn('value="01:00"' if field == 'sta' else 'value="07:00"', html)

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
            self.assertIn('20261016-clear-v1', html)
        self.assertIn('aria-label="Include for UPS948"', html)
        self.assertNotIn('>Include</label>', html)
        self.assertNotIn('>No Return</label>', html)

    def test_inbound_times_rely_on_header_not_repeated_visible_labels(self):
        html = self.fixture.client.get('/neorain/inbound').get_data(as_text=True)
        self.assertIn('<th class="neorain-arrival-clocks-heading">STA / ETA</th>', html)
        clocks = re.search(r'<div class="neorain-arrival-clocks">(.*?)</time></div>', html, re.S).group(1)
        self.assertNotRegex(clocks, r'>\s*(?:STA|ETA)\s*<')
        self.assertIn('aria-label="STA for UPS1487 (HHMM)"', clocks)
        self.assertIn('aria-label="ETA for UPS1487"', clocks)
        self.assertIn('class="neorain-clock-divider" aria-hidden="true">/</span>', clocks)

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
