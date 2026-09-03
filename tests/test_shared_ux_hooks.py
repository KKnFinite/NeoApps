from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SharedUxHooksTest(unittest.TestCase):
    def test_same_page_post_scroll_state_is_bounded_and_destination_scoped(self):
        template = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")

        self.assertIn('const key = "neoapps.same-page-scroll.v1"', template)
        self.assertIn("saved?.page === page", template)
        self.assertIn("Date.now() - saved.savedAt < 120000", template)
        self.assertIn("if (method === \"GET\") return", template)
        self.assertIn("if (!event.defaultPrevented)", template)
        self.assertIn("sessionStorage.removeItem(key)", template)

    def test_mobile_share_uses_a_local_root_portal_qr(self):
        template = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")
        routes = (ROOT / "app/auth/routes.py").read_text(encoding="utf-8")
        css = (ROOT / "app/static/css/base.css").read_text(encoding="utf-8")

        self.assertIn("data-mobile-share-open", template)
        self.assertIn("Scan to request access or sign in", template)
        self.assertIn("url_for('auth.neoapps_share_qr')", template)
        self.assertIn("url_for('neonodes.index', _external=True)", template)
        self.assertIn('@bp.route("/share/neoapps-qr.svg")', routes)
        self.assertIn("QrCodeWidget(target)", routes)
        self.assertIn(".mobile-share-dialog {\n    display: none;", css)
        self.assertIn("@media (max-width: 760px)", css)

    def test_crew_admin_controls_use_scoped_dark_operational_styling(self):
        template = (ROOT / "app/templates/neonodes/neorain/inbound.html").read_text(
            encoding="utf-8"
        )
        css = (ROOT / "app/static/css/base.css").read_text(encoding="utf-8")

        self.assertIn("neorain-settings-panel neorain-crew-admin", template)
        self.assertIn(".neorain-crew-admin :is(input", css)
        self.assertIn("background: #07151f", css)

    def test_shift_flow_unassigned_list_has_a_bounded_scroll_area(self):
        template = (ROOT / "app/templates/neostaffing/shift_flow.html").read_text(
            encoding="utf-8"
        )
        css = (ROOT / "app/static/css/base.css").read_text(encoding="utf-8")

        self.assertIn("UNASSIGNED / NEEDS ATTENTION", template)
        self.assertIn(".neostaffing-shift-flow-needs-attention ul { max-height:", css)
        self.assertIn("overflow-y: auto", css)


if __name__ == "__main__":
    unittest.main()
