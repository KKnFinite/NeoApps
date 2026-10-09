"""Static contracts for both NeoRain board layouts."""
import unittest
from pathlib import Path


TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates" / "neonodes" / "neorain"


class NeoRainBoardLayoutTest(unittest.TestCase):
    def test_summary_grid_and_board_exit_remain_consistent(self):
        common = (TEMPLATES / "_board_layout_shared.html").read_text()
        self.assertIn("grid-template-columns: repeat(5, minmax(0, 1fr))", common)
        self.assertIn("grid-template-columns: repeat(6, minmax(0, 1fr))", common)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", common)
        self.assertIn("font-size: 1.035rem", common)
        self.assertIn("font-size: .9rem", common)
        self.assertIn("font-size: 1.125rem", common)
        self.assertIn("padding: 4px 4px 4px 40px", common)
        self.assertIn("body.blueprint-neorain.operational-board-view .operational-board-exit", common)
        self.assertIn("left: 8px;\n        right: auto;", common)
        self.assertNotIn("left: auto;\n        right: 8px;", common)

    def test_white_neutral_text_preserves_semantic_status_colors(self):
        common = (TEMPLATES / "_board_layout_shared.html").read_text()
        late = (TEMPLATES / "_delay_panel_shared.html").read_text()
        self.assertIn("color: #fff", common)
        self.assertIn(".neorain-mobile-column-head span", common)
        self.assertIn(".neorain-crew-admin-ramp-option", common)
        self.assertIn(".neorain-delay-existing-copy", common)
        self.assertIn(".neorain-late-inclusion-control[data-neorain-included=\"false\"]", common)
        self.assertIn("input::placeholder", common)
        self.assertIn("color: #f2a3aa", late)

    def test_outbound_columns_fit_the_viewport_and_elmac_follows_center_fuel(self):
        import re

        desktop = (TEMPLATES / "_outbound_content.html").read_text()
        mobile = (TEMPLATES / "_outbound_mobile_content.html").read_text()
        styles = (TEMPLATES / "_board_layout_shared.html").read_text()
        delay_styles = (TEMPLATES / "_delay_panel_shared.html").read_text()
        base_styles = (
            TEMPLATES.parents[2] / "static" / "css" / "22-neorain.css"
        ).read_text()

        cols = re.findall(
            r'<col data-neorain-outbound-column="([^"]+)" style="width: ([0-9.]+)%">',
            desktop,
        )
        self.assertEqual(len(cols), 21)
        self.assertAlmostEqual(sum(float(width) for _name, width in cols), 100)
        self.assertEqual(
            [name for name, _width in cols][11:15],
            ["neo-fuel", "center-fuel", "elmac", "ramp-lc"],
        )
        self.assertLess(desktop.index('data-label="Center Fuel"'), desktop.index('data-label="eLMAC"'))
        self.assertLess(desktop.index('data-label="eLMAC"'), desktop.index("milestone_cell(row, 'ramp_load_complete'"))
        self.assertLess(mobile.index("<dt>CENTER FUEL</dt>"), mobile.index("<dt>eLMAC</dt>"))
        self.assertLess(mobile.index("<dt>eLMAC</dt>"), mobile.index("<dt>RAMP LC</dt>"))
        self.assertIn("neorain-outbound-table--flight-board", desktop)
        self.assertIn(">DELAY ({{ row.delay_info|length }})", desktop)
        self.assertIn("table-layout: fixed", styles)
        self.assertIn("max-width: 100%;", styles)
        self.assertIn("padding: 7px 3px", styles)
        self.assertIn('overflow-x: hidden;', styles)
        self.assertIn("@media (min-width: 901px) and (max-width: 1440px)", styles)
        self.assertIn("@media (max-width: 1440px)", base_styles)
        self.assertIn("@media (max-width: 1440px)", delay_styles)
        self.assertIn("neorain-mobile-board", base_styles)

    def test_inbound_outbound_share_full_width_board_layout(self):
        common = (TEMPLATES / "_board_layout_shared.html").read_text()
        self.assertIn("table-layout: fixed", common)
        self.assertIn("overflow-x: hidden", common)
        self.assertIn("position: sticky", common)
        self.assertIn("height: 100dvh", common)
        self.assertIn("sessionStorage.setItem(boardKey", common)
        self.assertIn("sessionStorage.setItem(scrollKey", common)
        for name in ("inbound", "outbound"):
            with self.subTest(board=name):
                template = (TEMPLATES / f"{name}.html").read_text()
                self.assertIn(f'data-neorain-board-page="{name}"', template)
                self.assertIn("data-neorain-board-collapse", template)
                self.assertIn("data-neorain-board-support", template)
                self.assertIn('_board_layout_shared.html', template)

if __name__ == "__main__":
    unittest.main()
