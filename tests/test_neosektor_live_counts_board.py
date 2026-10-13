"""Focused Live Counts routing-source, section-order and Board View contracts."""
from pathlib import Path
import re
import unittest
from unittest.mock import Mock

from app.services import neosektor_live_counts as service
from tests import test_neosektor_routes as fixture_module
from tests.html_contracts import document, Element


class LiveCountsBoardTest(unittest.TestCase):
    def test_template_order_and_state_match_driver_routing(self):
        fixture = fixture_module.NeoSektorRoutesTest()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture._login_approved_user(role="operator")
        response = fixture.client.get('/neosektor/live-counts')
        self.assertEqual(response.status_code, 200)
        root = document(response)
        main = root.one(id='neosektor-live-counts-panel')
        sections = [child.attrs.get('class', '') for child in main.children if isinstance(child, Element)]
        order = ['neosektor-live-wave-row', 'sektor-live-routing', 'neosektor-live-ballmat-row', 'sektor-live-bays']
        positions = [next(i for i, cls in enumerate(sections) if name in cls.split()) for name in order]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(main.findall(cls='sektor-live-routing')), 1)
        state = fixture.client.get('/neosektor/live-counts/state').json['state']
        canonical = fixture.client.get('/neosektor/driver-routing/state').json['state']
        self.assertEqual(state['routing'], canonical['routing'])
        for key in ('first', 'second'):
            route = canonical['routing']['routes'][key]
            expected = 'ROUTING '+route['direction'].upper() if route['display_state']=='route' else route['display_message']
            self.assertEqual(main.one(**{'data-live-route': key}).text, expected)
        self.assertEqual(len(root.findall(**{'data-operational-board-toggle': None, 'aria-label': 'Exit Board View'})), 1)
        self.assertIn('20261012-canonical-board-v1', response.text)

    def test_context_reuses_canonical_routes_without_a_second_calculation_or_read(self):
        # The obsolete compact labels intentionally disagree: they must not
        # become Live Counts' source, even for lifecycle states/new messages.
        canonical = {'routes': {'first': {'display_state': 'pending', 'display_message': 'PENDING'},
                                'second': {'display_state': 'all_up', 'display_message': 'ALL UP'}}}
        state = dict.fromkeys(('summary', 'waves', 'sides', 'operational_settings', 'integration', 'refresh'), {})
        state.update(routing=canonical, ballmat_routing={'first': 'ROUTING WEST', 'second': '-'})
        bundle = Mock()
        bundle.driver_routing_state_payload.return_value = state
        live = service.live_counts_context(None, bundle=bundle)
        bundle.driver_routing_state_payload.assert_called_once_with()
        bundle.reset_mock()
        driver = service.driver_routing_context(None, bundle=bundle)
        bundle.driver_routing_state_payload.assert_called_once_with()
        self.assertIs(live['routing'], driver['state']['routing'])
        self.assertNotIn('ballmat_routing', live)

    def test_board_geometry_is_scoped_to_live_counts_and_preserves_exit(self):
        css = Path('app/static/css/neosektor_live_counts.css').read_text(encoding='utf-8')
        blocks = [(selector.strip(), dict(re.findall(r'([\w-]+)\s*:\s*([^;]+);', body)))
                  for selector, body in re.findall(r'([^{}]+)\{([^{}]+)\}', css)
                  if 'operational-board-view' in selector]
        self.assertTrue(blocks)
        for selector, _ in blocks:
            self.assertIn('body.blueprint-neosektor.neosektor-live-counts-page.operational-board-view', selector)
            self.assertIn('html.operational-board-view-boot body.blueprint-neosektor.neosektor-live-counts-page', selector)
        def rule(target):
            return next(properties for selector, properties in blocks if selector.endswith(target))
        for target in ('> .shell', '> .shell > .content', '#sektor-live-display', '#neosektor-live-counts-panel'):
            properties = rule(target)
            self.assertEqual(properties['width'].strip(), '100%')
            self.assertEqual(properties['max-width'].strip(), 'none')
            self.assertEqual(properties['margin'].strip(), '0')
            self.assertEqual(properties['height'].strip(), 'auto')
            self.assertEqual(properties['gap'].strip(), '0')
        self.assertEqual(rule('#neosektor-live-counts-panel')['min-height'].strip(), '0')
        self.assertEqual(rule('#neosektor-live-counts-panel')['overflow'].strip(), 'visible')
        self.assertEqual(rule('.operational-board-exit')['display'].strip(), 'grid')
        self.assertEqual(rule('.operational-board-exit')['position'].strip(), 'fixed')
        self.assertNotIn('var(', rule('> .shell > .content')['padding'])
