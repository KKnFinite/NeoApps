from tests.css_contracts import stylesheet_source
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
        css = stylesheet_source()

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
        css = stylesheet_source()

        self.assertIn("neorain-settings-panel neorain-crew-admin", template)
        self.assertIn(".neorain-crew-admin :is(input", css)
        self.assertIn("background: #07151f", css)

    def test_shift_flow_unassigned_list_has_a_bounded_scroll_area(self):
        from tests import test_staffing_live_ux as fixtures
        from tests.html_contracts import document
        from tests.presentation_contracts import asset_source
        host=fixtures.LiveUxTest();host.setUp();self.addCleanup(host.tearDown)
        from app.models import PortalAppAccess
        from app.extensions import db
        host.user.role='grandmaster'
        db.session.add(PortalAppAccess(user_id=host.user.id,app_code='neostaffing',status='approved',role='master',is_active=True));db.session.commit()
        response=host.client.get('/neostaffing/shift-flow');self.assertEqual(response.status_code,200)
        scroll=document(response).one(**{'data-roster-scroll':None})
        self.assertEqual(scroll.attrs['role'],'region');self.assertEqual(scroll.attrs['tabindex'],'0')
        self.assertIn('NEEDS ASSIGNMENT',scroll.text)
        css=asset_source(self,response,'css/neostaffing_shift_map.css',tag='link')
        self.assertIn('max-height:min(74vh,720px)',css);self.assertIn('overflow:auto',css)


if __name__ == "__main__":
    unittest.main()
