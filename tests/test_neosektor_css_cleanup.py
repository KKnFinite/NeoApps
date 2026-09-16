"""Guard the retired dashboard CSS without treating dynamic classes as unused."""

import json
from pathlib import Path
import re
import unittest


class NeoSektorCssCleanupTest(unittest.TestCase):
    def test_tunnel_desktop_reference_geometry_keeps_flat_skin_and_shell(self):
        css_root = Path(__file__).resolve().parents[1] / 'app/static/css'
        css = (css_root / 'neosektor_tunnel_mobile.css').read_text()
        desktop = css.split('@media (min-width:901px) {', 1)[1].split('@media (max-width:900px)', 1)[0]
        self.assertIn('border:0; border-radius:0; box-shadow:none; background:transparent;', desktop)
        for selector in ('.tunnel-panel', '.tunnel-metric', '.tunnel-readonly-card', '.tunnel-bay-card'):
            self.assertIn(selector, desktop)
        self.assertIn('#neosektor-tunnel-panel .tunnel-wrap > .neosektor-menu-link { display:none; }', desktop)
        def rule(selector):
            # Read the last, effective page-scoped declaration rather than
            # accepting the obsolete board rules in the shared CSS bundle.
            matches = re.findall(re.escape('#neosektor-tunnel-panel ' + selector) + r'\s*\{([^}]+)\}', desktop)
            self.assertTrue(matches, selector)
            return matches[-1]
        for selector, order in [('.tunnel-wave-grid', 1), ('.tunnel-counts-panel', 2),
                                ('.tunnel-bay-panel', 3), ('.tunnel-operations-card', 4),
                                ('.tunnel-offset-panel', 5)]:
            self.assertIn(f'order:{order}', rule(selector))
        self.assertIn('display:flex; flex-direction:column; height:auto', rule('.tunnel-wrap'))
        for selector in ('.tunnel-wave-grid', '.tunnel-ballmat-grid'):
            self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))', rule(selector))
        self.assertIn('grid-template-rows:auto repeat(3,auto)', rule('.tunnel-ballmat-column'))
        self.assertIn('grid-template-columns:80px minmax(0,1fr)', rule('.tunnel-ballmat-card'))
        self.assertIn('grid-template-columns:repeat(5,minmax(0,1fr))', rule('.tunnel-bay-grid'))
        self.assertIn('grid-template-columns:minmax(0,1fr)', rule('.tunnel-bay-card'))
        self.assertIn('grid-template-rows:auto auto', rule('.tunnel-route-overrides'))
        self.assertIn('grid-template-columns:88px minmax(0,1fr)', rule('.tunnel-route-override'))
        self.assertIn('grid-template-columns:repeat(3,minmax(0,1fr))', rule('.tunnel-settings-grid'))
        self.assertIn(':is(.tunnel-desktop-workspace,.tunnel-desktop-left,.tunnel-ballmat-wave-workspace) { display:contents; }', desktop)
        self.assertIn('height:auto; min-height:100vh; overflow:visible;', desktop)
        self.assertNotIn('side-nav', desktop)
        self.assertNotIn('grid-template-areas', desktop)
        # Keep the shared header/sidebar inset; no replacement shell.
        geometry = (css_root / '16-neosektor.css').read_text()
        self.assertIn('calc(var(--motherbrain-side-nav-width) + 24px)', geometry)

    def test_display_bundles_keep_page_rules_and_mobile_overrides_separate(self):
        css_root = Path(__file__).resolve().parents[1] / "app/static/css"
        driver = (css_root / "neosektor_driver_routing.css").read_text()
        discharge = (css_root / "neosektor_discharge.css").read_text()
        self.assertFalse((css_root / "neosektor_display.css").exists())
        self.assertNotIn("#sektor-discharge", driver)
        self.assertNotIn("#sektor-tv", discharge)
        self.assertNotIn("data-driver-routing", discharge)
        for css in (driver, discharge):
            self.assertEqual(css.count("@media (max-width:900px) {"), 1)
        # TV readability has its own viewport-gated block, not Discharge rules.
        self.assertEqual(discharge.count("@media"), 1)
        self.assertEqual(driver.count("@media"), 2)
        tv = driver.split("@media (min-width:601px) and (min-height:601px) {", 1)[1]
        self.assertIn("#sektor-tv {", tv)
        self.assertIn("--tv-target-size:clamp(29px,min(4.8vw,7.2vh),77px)", tv)
        self.assertLess(driver.index("#sektor-tv [data-driver-bay-name]"), driver.index("@media"))
        self.assertGreater(driver.index("html:not(#sektor-tv)"), driver.index("@media"))
        self.assertLess(discharge.index("#sektor-discharge .neosektor-discharge-row"), discharge.index("@media"))
        self.assertGreater(discharge.index("body:has(#sektor-discharge)"), discharge.index("@media"))

    def test_retired_dashboard_classes_are_absent_and_live_menu_is_preserved(self):
        root = Path(__file__).resolve().parents[1]
        retired = (
            "neosektor-shell", "neosektor-hero", "neosektor-kicker",
            "neosektor-menu", "neosektor-menu-link-secondary",
            "neosektor-overview-panel", "neosektor-overview-grid",
            "neosektor-mode-pill", "neosektor-operator-title",
        )
        pattern = re.compile(r"(?<![\w-])(?:" + "|".join(retired) + r")(?![\w-])")
        # Include shared templates, inline JS, external JS and Python-generated
        # markup; do not infer absence from one dashboard template alone.
        for source in (root / "app").rglob("*"):
            if source.suffix in {".html", ".js", ".py"}:
                self.assertIsNone(pattern.search(source.read_text(encoding="utf-8")), source)

        css = (root / "app/static/css/12-neosektor.css").read_text(encoding="utf-8")
        self.assertIsNone(pattern.search(css))
        self.assertIn(".neosektor-menu-link {", css)
        self.assertIn(".neosektor-menu-link:hover,", css)
        self.assertIn(".neosektor-menu-link:focus-visible {", css)
        self.assertIn(".neosektor-status-overflowing {", css)  # Generated by Ballmat JS.
        sequence = json.loads((root / "app/static/css/sequence.json").read_text())
        files = [item["file"] for item in sequence["stylesheets"]]
        self.assertEqual(files[12:17], [
            "12-neosektor.css", "13-shared.css", "14-neoscorpion.css",
            "15-shared.css", "16-neosektor.css",
        ])
