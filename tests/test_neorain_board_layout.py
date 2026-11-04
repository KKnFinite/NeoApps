"""Static contracts for both NeoRain board layouts."""
import unittest
from pathlib import Path


TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates" / "neonodes" / "neorain"


class NeoRainBoardLayoutTest(unittest.TestCase):
    def rendered(self):
        from tests.neorain_presentation_fixture import boards
        return boards(self)[1]

    def test_aircraft_only_summaries_and_independent_collapsed_crew_admin(self):
        inbound = (TEMPLATES / "inbound.html").read_text()
        outbound = (TEMPLATES / "outbound.html").read_text()
        for template in (inbound, outbound):
            self.assertNotIn("staffing_summary.hub", template)
            self.assertNotIn("staffing_summary.ramp", template)
            self.assertIn("aircraft statistics", template)
        self.assertIn('<details class="neorain-settings-panel neorain-crew-admin"', inbound)
        self.assertIn('data-neorain-crew-collapse', inbound)
        self.assertIn("localStorage.setItem(key", inbound)
        self.assertIn('name="expected_version"', inbound)
        self.assertIn('data-neorain-board-collapse', inbound)

    def test_summary_grid_and_board_exit_remain_consistent(self):
        from tests.html_contracts import document
        from tests.presentation_contracts import asset_source
        for response in self.rendered().values():
            root=document(response)
            self.assertEqual(len(root.findall(**{'data-neorain-summary-total':None})),1)
            self.assertTrue(root.findall('button',**{'data-operational-board-toggle':None}))
            self.assertTrue(root.findall('button',**{'data-operational-board-toggle':None,'aria-label':'Exit Board View'}))
            css=asset_source(self,response,'css/neorain_csp_board_layout_shared.css',tag='link')
            for geometry in ('repeat(3, minmax(0, 1fr))','repeat(6, minmax(0, 1fr))','repeat(2, minmax(0, 1fr))'):
                self.assertIn(geometry,css)
            self.assertIn('body.blueprint-neorain.operational-board-view .operational-board-exit',css)

    def test_white_neutral_text_preserves_semantic_status_colors(self):
        from tests.html_contracts import document
        from tests.presentation_contracts import asset_source
        response=self.rendered()['outbound']
        common=asset_source(self,response,'css/neorain_csp_board_layout_shared.css',tag='link')
        late=asset_source(self,response,'css/neorain_csp_delay_panel_shared.css',tag='link')
        self.assertIn('color: #fff',common)
        self.assertIn('.neorain-mobile-column-head span',common)
        self.assertIn('input::placeholder',common)
        self.assertIn('color: #f2a3aa',late)
        self.assertTrue(document(response).findall(**{'data-rain-override-field':'included'}))

    def test_outbound_columns_fit_the_viewport_and_elmac_follows_center_fuel(self):
        import re
        from tests.html_contracts import document
        from tests.presentation_contracts import asset_source
        response=self.rendered()['outbound'];root=document(response)
        table=root.one('table',cls='neorain-outbound-table')
        cols=table.findall('col');self.assertEqual(len(cols),21)
        css=asset_source(self,response,'css/csp_compatibility.css',tag='link')
        widths=[]
        for col in cols:
            self.assertNotIn('style',col.attrs)
            cls=col.attrs['class']
            width=re.search(r'\.'+re.escape(cls)+r'\s*\{\s*width:\s*([0-9.]+)%',css)
            self.assertIsNotNone(width,cls);widths.append(float(width.group(1)))
        self.assertAlmostEqual(sum(widths),100)
        self.assertEqual([n.attrs['data-neorain-outbound-column'] for n in cols][11:15],['neo-fuel','center-fuel','elmac','ramp-lc'])
        headers=[n.text for n in table.one('thead').findall('th')]
        self.assertLess(headers.index('CENTER FUEL'),headers.index('eLMAC'));self.assertLess(headers.index('eLMAC'),headers.index('RAMP LC'))
        mobile=root.one(**{'data-neorain-mobile-board':'outbound'})
        labels=[n.text for n in mobile.findall('dt')]
        self.assertLess(labels.index('CENTER FUEL'),labels.index('eLMAC'));self.assertLess(labels.index('eLMAC'),labels.index('RAMP LC'))
        self.assertTrue(table.findall(**{'data-neorain-delay-toggle':None}))

    def test_outbound_fuel_labels_not_repeated_and_both_columns_centered(self):
        from tests.html_contracts import document
        from tests.presentation_contracts import asset_source
        response=self.rendered()['outbound'];root=document(response)
        table=root.one('table',cls='neorain-outbound-table')
        headers=[n.text for n in table.one('thead').findall('th')]
        self.assertEqual(headers.count('NEO FUEL'),1);self.assertEqual(headers.count('CENTER FUEL'),1)
        self.assertFalse(table.findall(cls='neorain-fuel-source'))
        self.assertTrue(root.one(**{'data-neorain-mobile-board':'outbound'}).findall('dt'))
        css=asset_source(self,response,'css/neorain_csp_board_layout_shared.css',tag='link')
        self.assertIn('td[data-label="Neo Fuel"], td[data-label="Center Fuel"]',css)
        self.assertIn('text-align: center;',css)

    def test_inbound_mobile_status_does_not_truncate_and_headers_align(self):
        from tests.html_contracts import document
        from tests.presentation_contracts import asset_source
        response=self.rendered()['inbound'];root=document(response)
        mobile=root.one(**{'data-neorain-mobile-board':'inbound'})
        self.assertEqual([n.text for n in mobile.findall(role='columnheader')],['FLIGHT','TAIL','ORIGIN','PARKING','ETA','BLOCK-IN','+/-'])
        self.assertIn('UPS0911',mobile.text)
        css=asset_source(self,response,'css/neorain_csp_delay_panel_shared.css',tag='link')
        for declaration in ('--neorain-inbound-columns:','grid-template-columns: var(--neorain-inbound-columns)',
            'white-space: normal;','overflow-wrap: anywhere;','text-overflow: clip;'):
            self.assertIn(declaration,css)
        self.assertTrue(root.findall(**{'data-neorain-display':'status'}))

    def test_mobile_inbound_and_outbound_columns_center_and_keep_full_flight_numbers(self):
        from tests.html_contracts import document
        from tests.presentation_contracts import asset_source
        for name,response in self.rendered().items():
            mobile=document(response).one(**{'data-neorain-mobile-board':name})
            self.assertIn('UPS0911' if name=='inbound' else 'UPS0910',mobile.text)
            self.assertTrue(mobile.findall(**{'data-neorain-mobile-row-open':None}))
            self.assertEqual(len(mobile.findall(role='columnheader')),7 if name=='inbound' else 8)
            css=asset_source(self,response,'css/neorain_csp_delay_panel_shared.css',tag='link')
            self.assertIn(f'grid-template-columns: var(--neorain-{name}-columns);',css)
            self.assertIn('place-items: center;',css)
            self.assertIn('text-overflow: clip;',css)

    def test_inbound_outbound_share_full_width_board_layout(self):
        from tests.html_contracts import document
        from tests.presentation_contracts import asset_source
        for name,response in self.rendered().items():
            with self.subTest(board=name):
                root=document(response)
                self.assertTrue(root.findall(**{'data-neorain-board-page':name}))
                self.assertTrue(root.findall(**{'data-neorain-board-collapse':None}))
                self.assertTrue(root.findall(**{'data-neorain-board-support':None}))
                css=asset_source(self,response,'css/neorain_csp_board_layout_shared.css',tag='link')
                for declaration in ('table-layout: fixed','overflow-x: hidden','position: sticky','height: 100dvh'):
                    self.assertIn(declaration,css)
                scripts='\n'.join(n.text for n in root.findall('script') if not n.attrs.get('src'))
                self.assertIn('sessionStorage.setItem(boardKey',scripts)
                self.assertIn('sessionStorage.setItem(scrollKey',scripts)

if __name__ == "__main__":
    unittest.main()
