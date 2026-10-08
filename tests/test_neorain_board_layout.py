"""Static contracts for both NeoRain board layouts."""
import unittest
from pathlib import Path


TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates" / "neonodes" / "neorain"


class NeoRainBoardLayoutTest(unittest.TestCase):
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
