"""Real-page mobile drawer regression tests. No production records or auth bypass.
Run: python -m unittest tests.browser.test_mobile_drawer -v
Install dev browsers: pip install -r tests/requirements-browser.txt
                      python -m playwright install chromium webkit
"""
import logging
import os
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

from app import create_app
from app.extensions import db
from app.models import PortalAppAccess, User
from app.services.access_control import ensure_default_gateway_and_nodes, backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class MobileDrawerBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="neo-drawer-test-")
        cls.evidence = Path(os.environ.get("NEO_BROWSER_EVIDENCE", "instance/browser-evidence/mobile-drawer")).resolve()
        cls.evidence.mkdir(parents=True, exist_ok=True)
        config = type("BrowserConfig", (), {
            "SECRET_KEY": "local-browser-fixture-only",
            "TESTING": True, "CSRF_PROTECT_TESTING": True,
            "SESSION_COOKIE_SECURE": False,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(Path(cls.temp.name, "fixture.db")).replace("\\", "/"),
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "GOOGLE_MOTHERBRAIN_IMPORT_ENABLED": False,
            "GOOGLE_MOTHERBRAIN_READER_ENABLED": False,
            "GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON": None,
            "GOOGLE_SERVICE_ACCOUNT_JSON": None,
            "FLIGHT_API_ENABLED": False, "LIVE_SCREEN_REFRESH_INTERVAL_MS": 0,
            "NEOAPPS_MEMORY_DIAGNOSTICS_ENABLED": False,
        })
        cls.external = patch("requests.sessions.Session.request", side_effect=AssertionError("External integrations disabled in browser fixture"))
        cls.external.start()
        cls.app = create_app(config)
        with cls.app.app_context():
            db.create_all()
            ensure_default_gateway_and_nodes()
            ensure_default_permission_rules()
            for name, role in (("drawer-admin", "grandmaster"), ("drawer-watcher", "watcher")):
                user = User(username=name, email=name + "@example.test", role=role, email_verified_at=datetime.now(),
                            first_name="Alexandria", last_name="Long Operational Dispatcher Name")
                set_user_password(user, "BrowserFixture123!")
                db.session.add(user)
                db.session.flush()
                backfill_default_gateway_node_roles(user, role=role)
                db.session.add(PortalAppAccess(user_id=user.id, app_code="neostaffing",
                                              status="approved", role=role, is_active=True))
            db.session.commit()
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        cls.server = make_server("127.0.0.1", 0, cls.app, threaded=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"
        cls.pw = sync_playwright().start()

    @classmethod
    def tearDownClass(cls):
        cls.pw.stop()
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        cls.external.stop()
        cls.temp.cleanup()

    def login(self, page, username="drawer-admin"):
        page.goto(self.origin + "/login")
        page.locator('input[name="email"]').fill(username + "@example.test")
        page.locator('input[name="password"]').fill("BrowserFixture123!")
        page.locator('button[type="submit"]').first.click()
        page.wait_for_url(lambda url: "/login" not in url)

    def ready(self, page, path):
        response = page.goto(self.origin + path)
        self.assertEqual(response.status, 200)
        page.evaluate("""async () => {
            await document.fonts.load('16px NeoFont');
            await document.fonts.ready;
            await Promise.all(Array.from(document.images).map(img => {
                img.loading = 'eager';
                return img.decode().catch(() => {});
            }));
        }""")
        self.assertTrue(page.evaluate("document.fonts.check('16px NeoFont')"))

    def geometry(self, page):
        page.locator("[data-mobile-drawer]").evaluate("async (el) => { await Promise.all(el.getAnimations().map(a => a.finished)); }")
        result = page.evaluate("""() => {
            const panel = document.querySelector('[data-mobile-drawer]');
            const nav = document.querySelector('.neo-mobile-bottom').getBoundingClientRect();
            const title = panel.querySelector('.neo-drawer-identity strong');
            const range = document.createRange(); range.selectNodeContents(title);
            const text = range.getBoundingClientRect(), bounds = title.getBoundingClientRect();
            return {
                panelOverflow:panel.scrollWidth > panel.clientWidth + 1,
                pageOverflow:document.documentElement.scrollWidth > innerWidth + 1,
                nameFits:text.left >= bounds.left - 1 && text.right <= bounds.right + 1,
                oneLine:text.height <= parseFloat(getComputedStyle(title).lineHeight) + 1,
                font:getComputedStyle(title).fontFamily,
                navVisible:nav.bottom <= innerHeight && nav.left >= 0 && nav.right <= innerWidth,
                backgroundInactive:document.querySelector('.shell').inert,
                navInactive:!!document.querySelector('[data-drawer-toggle]').closest('[inert]'),
                closeUncovered:(() => { const b=panel.querySelector('[data-drawer-close]'), r=b.getBoundingClientRect(); return b.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)); })(),
            };
        }""")
        self.assertFalse(result["panelOverflow"], result)
        self.assertFalse(result["pageOverflow"], result)
        self.assertTrue(result["nameFits"], result)
        self.assertTrue(result["oneLine"], result)
        self.assertIn("NeoFont", result["font"])
        self.assertTrue(result["navVisible"], result)
        self.assertTrue(result["backgroundInactive"], result)
        self.assertFalse(result["navInactive"], result)
        self.assertTrue(result["closeUncovered"], result)

    def exercise(self, page, engine, path, width, height):
        self.ready(page, path)
        self.assertEqual(page.locator("[data-mobile-drawer]").count(), 1)
        expect(page.locator("[data-mobile-drawer]")).to_be_hidden()
        self.assertEqual(page.locator("[data-mobile-shell-menu-panel], [data-gateway-mobile-drawer], [data-operational-mobile-drawer]").count(), 0)
        toggle = page.locator("[data-drawer-toggle]")
        product = page.locator('.neo-mobile-product-name, [data-mobile-topbar] .mobile-topbar-brand').filter(visible=True).first
        if path == '/portal':
            expect(page.locator('[data-mobile-topbar] .mobile-topbar-node-icon')).to_be_visible()
            self.assertEqual(product.count(), 0)
        else:
            self.assertTrue(product.evaluate("""el => {
            const r=document.createRange(); r.selectNodeContents(el);
            const t=r.getBoundingClientRect(), b=el.getBoundingClientRect();
            return getComputedStyle(el).fontFamily.includes('NeoFont') && t.width <= b.width + 1 &&
                t.right <= innerWidth - 8 && t.height <= parseFloat(getComputedStyle(el).lineHeight) + 1;
            }"""), "Complete one-line NeoFont header")
        alerts = page.locator('[data-operational-mobile-header] summary, [data-gateway-mobile-header] summary, [data-mobile-topbar] [data-motherbrain-alert-toggle]').filter(visible=True)
        if alerts.count():
            bounds = alerts.first.bounding_box()
            self.assertLessEqual(bounds['x'] + bounds['width'], width - 3, 'Alerts remain within header')
        # Last real Gateway card must scroll above BOTH dock and decorative fade.
        if path == "/rfd":
            page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
            page.wait_for_function("scrollY >= document.documentElement.scrollHeight - innerHeight - 1")
            if width == 320:
                self.assertGreater(page.evaluate('scrollY'), 0)
            last = page.locator('.gateway-node-grid > :last-child').bounding_box()
            dock = page.locator('.neo-mobile-bottom').bounding_box()
            self.assertLessEqual(last['y'] + last['height'], dock['y'] - 20 + 1)
            page.screenshot(path=str(self.evidence / f"{engine}-gateway-scrolled-{width}.png"))
        if width == 390:
            # Only use the real page's scrollable space; no artificial mock content.
            room = page.evaluate("document.documentElement.scrollHeight - innerHeight")
            if room > 100:
                page.evaluate("scrollTo(0, 0)")
                page.wait_for_function("scrollY === 0")
                page.evaluate("scrollTo(0, 90)")
                page.wait_for_function("document.body.className.includes('mobile-header-hidden')")
                page.evaluate("scrollTo(0, 40)")
                page.wait_for_function("!document.body.className.includes('mobile-header-hidden')")
        page.evaluate("scrollTo(0, 130)")
        saved = page.evaluate("scrollY")
        toggle.click()
        panel = page.locator("[data-mobile-drawer]")
        expect(panel).to_be_visible()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(toggle).to_contain_text("Close")
        expect(page.locator("[data-mobile-navigation]")).to_have_attribute("aria-modal", "true")
        self.geometry(page)
        # Tab wraps across top close, menu content and the persistent bottom CLOSE.
        toggle.focus()
        page.keyboard.press("Tab")
        expect(page.locator("[data-drawer-close]")).to_be_focused()
        page.keyboard.press("Shift+Tab")
        expect(toggle).to_be_focused()
        # Last action is reachable above bottom controls, including short landscapes.
        logout = panel.locator('form[action="/logout"] button')
        logout.scroll_into_view_if_needed()
        self.assertLessEqual(logout.bounding_box()["y"] + logout.bounding_box()["height"],
                             page.locator(".neo-mobile-bottom").bounding_box()["y"])
        if width in (320, 390, 740):
            panel.evaluate("(el) => el.scrollTop = 0")
            suffix = "" if width == 390 else f"-{width}"
            page.screenshot(path=str(self.evidence / f"{engine}-{path.strip('/').replace('/', '-')}-open{suffix}.png"))
        toggle.click()
        expect(panel).to_be_hidden()
        expect(toggle).to_be_focused()
        self.assertAlmostEqual(page.evaluate("scrollY"), saved, delta=1)
        self.assertFalse(page.locator(".shell").evaluate("(el) => el.inert"))
        for dismissal in ("top", "escape", "backdrop", "swipe"):
            toggle.click()
            if dismissal == "top":
                page.locator("[data-drawer-close]").click()
            elif dismissal == "escape":
                page.keyboard.press("Escape")
            elif dismissal == "backdrop":
                page.locator("[data-drawer-backdrop]").click(position={"x":2, "y":80})
            else:
                target = panel.locator(".neo-drawer-identity")
                target.dispatch_event("pointerdown", {"pointerType":"touch","isPrimary":True,"pointerId":7,"clientX":100,"clientY":150})
                target.dispatch_event("pointerup", {"pointerType":"touch","isPrimary":True,"pointerId":7,"clientX":210,"clientY":153})
            expect(panel).to_be_hidden()
        toggle.click()
        target = panel.locator(".neo-drawer-identity")
        for event, x, y in (("pointerdown",100,150),("pointermove",105,190),("pointerup",210,230)):
            target.dispatch_event(event, {"pointerType":"touch","isPrimary":True,"pointerId":8,"clientX":x,"clientY":y})
        expect(panel).to_be_visible()
        # Exercise native scrolling as well as the directional gesture classifier.
        panel.evaluate("async (el) => { await Promise.all(el.getAnimations().map(a => a.finished)); }")
        panel.evaluate("(el) => el.scrollTop = 0")
        if panel.evaluate("(el) => el.scrollHeight > el.clientHeight + 20"):
            bounds = panel.bounding_box()
            page.mouse.move(bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
            page.mouse.wheel(0, 100)
            page.wait_for_function("document.querySelector('[data-mobile-drawer]').scrollTop > 0")
            expect(panel).to_be_visible()
        page.keyboard.press("Escape")
        nodes = page.locator("[data-drawer-nodes]")
        nodes.click()
        expect(panel.locator('[data-drawer-view="nodes"]')).to_be_visible()
        expect(panel.locator('[data-drawer-view="menu"]')).to_be_hidden()
        expect(nodes).to_have_attribute('aria-expanded', 'true')
        expect(toggle).to_have_attribute('aria-expanded', 'false')
        expect(nodes).to_contain_text('Close')
        self.assertEqual(panel.locator('[data-character-switcher], [data-drawer-view="nodes"] form').count(), 0)
        self.assertGreater(panel.locator('.neo-drawer-node-link').count(), 0)
        locked = page.locator('body').get_attribute('style')
        toggle.click()
        expect(panel.locator('[data-drawer-view="nodes"]')).to_be_hidden()
        expect(panel.locator('[data-drawer-view="menu"]')).to_be_visible()
        expect(nodes).to_have_attribute('aria-expanded', 'false')
        expect(toggle).to_have_attribute('aria-expanded', 'true')
        self.assertEqual(page.locator('body').get_attribute('style'), locked)
        expect(page.locator('[data-drawer-close]')).to_be_focused()
        nodes.click()
        self.assertEqual(page.locator('body').get_attribute('style'), locked)
        panel.evaluate("async el => { await Promise.all(el.getAnimations().map(a => a.finished)); }")
        self.assertTrue(panel.locator('[data-drawer-view="menu"]').evaluate('el => el.inert'))
        self.assertTrue(panel.locator('.neo-drawer-node-links').evaluate('el => el.scrollWidth <= el.clientWidth'))
        page.screenshot(path=str(self.evidence / f"{engine}-{path.strip('/')}-nodes-{width}.png"))
        nodes.click()
        expect(panel).to_be_hidden()
        expect(nodes).to_be_focused()
        self.assertAlmostEqual(page.evaluate('scrollY'), saved, delta=1)
        nodes.click()
        page.keyboard.press("Escape")
        expect(nodes).to_be_focused()
        if width == 390:
            page.screenshot(path=str(self.evidence / f"{engine}-{path.strip('/').replace('/', '-')}-closed.png"))
        # Clean up if the page transitions to desktop with drawer open.
        toggle.click()
        page.set_viewport_size({"width":1920,"height":1080})
        expect(panel).to_be_hidden()
        expect(toggle).to_have_attribute("aria-expanded", "false")
        self.assertFalse(page.locator(".shell").evaluate("(el) => el.inert"))
        self.assertNotEqual(page.locator("body").evaluate("(el) => el.style.position"), "fixed")
        page.set_viewport_size({"width":width,"height":height})

    def run_engine(self, engine):
        browser = getattr(self.pw, engine).launch()
        context = browser.new_context(viewport={"width":390,"height":844}, has_touch=True)
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.origin) or route.request.url.startswith("data:") else route.abort())
        page = context.new_page()
        page.set_default_timeout(8000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            self.login(page)
            for width, height in ((320,700),(390,844)):
                page.set_viewport_size({"width":width,"height":height})
                for path in ("/portal", "/rfd", "/motherbrain"):
                    with self.subTest(engine=engine, width=width, path=path):
                        self.exercise(page, engine, path, width, height)
            # Explicit safe-area contract emulation (not a physical iPhone assertion).
            page.set_viewport_size({"width":390,"height":844})
            self.ready(page, "/motherbrain")
            page.add_style_tag(content=":root { --neo-safe-top:47px; --neo-safe-bottom:34px; --neo-safe-left:7px; --neo-safe-right:7px; }")
            page.locator("[data-drawer-toggle]").click()
            self.geometry(page)
            self.assertGreaterEqual(page.locator("[data-drawer-close]").bounding_box()["y"], 47)
            page.screenshot(path=str(self.evidence / f"{engine}-safe-area-open.png"))
            self.assertEqual(page.locator("[data-mobile-drawer] [data-operational-board-toggle]").count(), 0)
            page.keyboard.press("Escape")
            # Navigation from the modal returns a fully usable page.
            page.locator("[data-drawer-toggle]").click()
            page.locator(".neo-mobile-bottom a").click()
            page.wait_for_load_state("load")
            expect(page.locator("[data-mobile-drawer]")).to_be_hidden()
            self.assertFalse(page.locator(".shell").evaluate("(el) => el.inert"))
            # Reduced motion retains dismissal without an animated slide.
            page.emulate_media(reduced_motion="reduce")
            page.locator("[data-drawer-toggle]").click()
            self.assertEqual(page.locator("[data-mobile-drawer]").evaluate("(el) => getComputedStyle(el).animationName"), "none")
            page.keyboard.press("Escape")
            page.emulate_media(reduced_motion="no-preference")
            desktop = browser.new_context(viewport={"width":1920,"height":1080})
            desktop.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.origin) else route.abort())
            desktop_page = desktop.new_page()
            desktop_page.set_default_timeout(8000)
            self.login(desktop_page)
            baseline_ref = os.environ.get("NEO_COMPARE_BASELINE")
            if baseline_ref:
                baseline_css = subprocess.check_output(["git", "show", baseline_ref + ":app/static/css/base.css"], text=True)
                self.ready(desktop_page, "/neoscorpion")
                measure = """() => ['[data-operational-sidebar]','[data-operational-topbar]'].map(s => {
                    const e=document.querySelector(s), c=getComputedStyle(e);
                    return {rect:e.getBoundingClientRect().toJSON(),transform:c.transform,display:c.display};
                })"""
                current_desktop = desktop_page.evaluate(measure)
                desktop.route("**/static/css/base.css?*", lambda route: route.fulfill(body=baseline_css, content_type="text/css"))
                self.ready(desktop_page, "/neoscorpion")
                self.assertEqual(current_desktop, desktop_page.evaluate(measure), "Desktop geometry changed from baseline")
                desktop.unroute("**/static/css/base.css?*")
            self.ready(desktop_page, "/motherbrain")
            expect(desktop_page.locator("[data-operational-sidebar]")).to_be_visible()
            desktop_page.screenshot(path=str(self.evidence / f"{engine}-desktop-before-collapse.png"))
            desktop_page.locator("[data-operational-sidebar-toggle]").click()
            self.assertIn("operational-sidebar-collapsed", desktop_page.locator("body").get_attribute("class"))
            desktop_page.screenshot(path=str(self.evidence / f"{engine}-desktop-sidebar.png"))
            self.ready(desktop_page, "/neostaffing")
            self.assertEqual(desktop_page.locator("[data-mobile-navigation]").count(), 0)
            desktop_page.set_viewport_size({"width":390,"height":844})
            expect(desktop_page.locator("[data-mobile-bottom-nav]")).to_be_visible()
            desktop.close()
            # Restricted real account and server permission filtering.
            context.clear_cookies()
            self.login(page, "drawer-watcher")
            self.ready(page, "/portal")
            page.locator("[data-drawer-toggle]").click()
            self.assertEqual(page.locator('[data-mobile-drawer] a[href="/portal/manage"]').count(), 0)
            page.keyboard.press("Escape")
            self.ready(page, "/motherbrain")
            page.locator("[data-drawer-toggle]").click()
            self.assertEqual(page.locator('[data-mobile-drawer] a[href="/motherbrain/system-settings"]').count(), 0)
            self.assertEqual(errors, [])
        finally:
            context.close()
            browser.close()

    def test_chromium(self):
        self.run_engine("chromium")

    def test_portal_launcher(self):
        for engine, sizes in (('chromium', ((320,700),(390,844),(1920,1080))), ('webkit', ((390,844),))):
            browser = getattr(self.pw, engine).launch()
            context = browser.new_context()
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(self.origin) else route.abort())
            page = context.new_page()
            page.set_default_timeout(8000)
            try:
                self.login(page)
                for width, height in sizes:
                    with self.subTest(engine=engine,width=width):
                        page.set_viewport_size({'width':width,'height':height})
                        self.ready(page, '/portal')
                        header = page.locator('[data-mobile-topbar]' if width <= 900 else '.topbar')
                        brand = header.locator('.mobile-topbar-node-icon-link' if width <= 900 else '.brand').first
                        self.assertEqual(brand.inner_text().strip(), '')
                        expect(brand).to_have_attribute('href', '/portal')
                        expect(brand.locator('img')).to_be_visible()
                        self.assertEqual(page.locator('[data-portal-app]').count(), 2)
                        self.assertEqual(page.locator('[data-portal-app="neobid"]').count(), 0)
                        result = page.evaluate('''() => {
                            const cards=[...document.querySelectorAll('.portal-launch-card')];
                            const hero=document.querySelector('.portal-launcher-hero img'), h=hero.getBoundingClientRect();
                            return {overflow:document.documentElement.scrollWidth>innerWidth,
                                intact:Math.abs(h.width/h.height-hero.naturalWidth/hero.naturalHeight)<.01,
                                names:cards.every(c=>{const e=c.querySelector('h2'),r=document.createRange();r.selectNodeContents(e);const t=r.getBoundingClientRect(),b=c.getBoundingClientRect();return getComputedStyle(e).fontFamily.includes('NeoFont') && t.right<b.right-8 && t.height<=parseFloat(getComputedStyle(e).lineHeight)+1;}),
                                layout:innerWidth>900 ? Math.abs(cards[0].offsetTop-cards[1].offsetTop)<1 : cards[1].offsetTop>cards[0].offsetTop};
                        }''')
                        self.assertEqual(result, {'overflow':False,'intact':True,'names':True,'layout':True})
                        page.screenshot(path=str(self.evidence / f'{engine}-portal-launcher-{width}.png'))
                        if width <= 900:
                            page.evaluate('scrollTo(0,document.documentElement.scrollHeight)')
                            page.wait_for_function('scrollY >= document.documentElement.scrollHeight-innerHeight-1')
                            action=page.locator('.portal-launch-action').last.bounding_box()
                            dock=page.locator('.neo-mobile-bottom').bounding_box()
                            self.assertLessEqual(action['y']+action['height'],dock['y']-20)
                            page.screenshot(path=str(self.evidence / f'{engine}-portal-launcher-bottom-{width}.png'))
                            page.locator('[data-drawer-nodes]').click()
                            expect(page.locator('[data-drawer-view="nodes"]')).to_be_visible()
                            page.locator('[data-drawer-toggle]').click()
                            expect(page.locator('[data-drawer-view="menu"]')).to_be_visible()
                            expect(page.locator('[data-drawer-view="nodes"]')).to_be_hidden()
                            self.assertEqual(page.locator('[data-mobile-drawer] [data-operational-board-toggle]').count(),0)
                            page.keyboard.press('Escape')
            finally:
                context.close()
                browser.close()

    def test_login_hero(self):
        for engine, sizes in (('chromium', ((320,700),(390,844),(1920,1080))), ('webkit', ((390,844),))):
            browser = getattr(self.pw, engine).launch()
            context = browser.new_context()
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(self.origin) else route.abort())
            page = context.new_page()
            page.set_default_timeout(8000)
            try:
                for width, height in sizes:
                    page.set_viewport_size({'width':width,'height':height})
                    self.ready(page, '/login')
                    hero = page.locator('.portal-login-hero img')
                    self.assertTrue(hero.evaluate('e => e.complete && e.naturalWidth>0 && e.currentSrc.includes("hero_neopapps")'))
                    box = hero.bounding_box()
                    self.assertAlmostEqual(box['width']/box['height'], 1672/941, delta=.01)
                    form = page.locator('.command-login-form')
                    self.assertGreaterEqual(form.bounding_box()['y'], box['y']+box['height'])
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth'))
                    expect(page.locator('#dashboard-email')).to_have_attribute('autocomplete','email')
                    expect(page.locator('#dashboard-password')).to_have_attribute('autocomplete','current-password')
                    page.evaluate('scrollTo(0,0)')
                    page.screenshot(path=str(self.evidence / f'{engine}-login-hero-{width}.png'), full_page=True)
                    page.get_by_role('link', name='Forgot Password').scroll_into_view_if_needed()
                    expect(page.get_by_role('link', name='Forgot Password')).to_be_in_viewport()
                self.login(page)
                self.assertIn('/portal', page.url)
            finally:
                context.close()
                browser.close()

    def test_gateway_hero(self):
        for engine, sizes in (('chromium', ((320,700),(390,844),(1920,1080))), ('webkit', ((390,844),))):
            browser = getattr(self.pw, engine).launch()
            context = browser.new_context()
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(self.origin) else route.abort())
            page = context.new_page()
            page.set_default_timeout(8000)
            try:
                self.login(page)
                for width, height in sizes:
                    with self.subTest(engine=engine,width=width):
                        page.set_viewport_size({'width':width,'height':height})
                        self.ready(page, '/rfd')
                        self.assertEqual(page.locator('.gateway-dashboard-hero img').count(),1)
                        self.assertTrue(page.locator('.gateway-dashboard-hero img').evaluate("e => e.complete && e.naturalWidth>0 && e.currentSrc.includes('hero_gateway') && getComputedStyle(e).objectFit==='contain'"))
                        self.assertTrue(page.evaluate('''() => [...document.querySelectorAll('.gateway-node-name')].filter(e=>e.getClientRects().length).every(e=>{
                            const range=document.createRange(); range.selectNodeContents(e);
                            const r=range.getBoundingClientRect(), c=e.closest('.gateway-node-card').getBoundingClientRect();
                            return r.left>=c.left && r.right<=c.right-2 && r.height<=parseFloat(getComputedStyle(e).fontSize)*1.6;
                        })'''))
                        self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth'))
                        self.assertEqual(page.locator('.gateway-node-grid > *').count(),6)
                        hero=page.locator('.gateway-dashboard-hero').bounding_box()
                        if width<=900:
                            self.assertAlmostEqual(hero['width']/hero['height'],3,delta=.01)
                            controls=page.locator('.gateway-page-mobile-context')
                            expect(controls).to_be_visible()
                            self.assertLessEqual(hero['y']+hero['height'],controls.bounding_box()['y'])
                            expect(controls.locator('a').first).to_have_attribute('href','/rfd')
                        else:
                            self.assertLessEqual(hero['height'],300)
                            expect(page.locator('[data-gateway-shell-header]')).to_be_visible()
                        page.screenshot(path=str(self.evidence / f'{engine}-gateway-hero-{width}.png'))
                        if width<=900:
                            page.evaluate('scrollTo(0,document.documentElement.scrollHeight)')
                            page.wait_for_function('scrollY >= document.documentElement.scrollHeight-innerHeight-1')
                            last=page.locator('.gateway-node-grid > :last-child').bounding_box()
                            dock=page.locator('.neo-mobile-bottom').bounding_box()
                            self.assertLessEqual(last['y']+last['height'],dock['y']-20+1)
                            page.screenshot(path=str(self.evidence / f'{engine}-gateway-hero-bottom-{width}.png'))
            finally:
                context.close()
                browser.close()

    def test_desktop_only_board_view(self):
        browser = self.pw.chromium.launch()
        context = browser.new_context(viewport={"width":390,"height":844})
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.origin) else route.abort())
        page = context.new_page()
        key = 'neoapps.operational-shell.board.v1:/motherbrain'
        active = lambda: page.locator('body').evaluate("el => el.classList.contains('operational-board-view')")
        try:
            self.login(page)
            page.evaluate('(key) => localStorage.setItem(key, "on")', key)
            self.ready(page, '/motherbrain')
            self.assertFalse(active())
            self.assertEqual(page.evaluate('(key) => localStorage.getItem(key)', key), 'on')
            self.assertEqual(page.locator('[data-mobile-drawer] [data-operational-board-toggle]').count(), 0)
            expect(page.locator('.operational-board-exit')).to_be_hidden()
            page.locator('[data-drawer-toggle]').click()
            expect(page.locator('[data-drawer-view="menu"]')).to_be_visible()
            page.locator('[data-drawer-nodes]').click()
            expect(page.locator('[data-drawer-view="nodes"]')).to_be_visible()
            page.locator('[data-drawer-nodes]').click()
            expect(page.locator('[data-mobile-drawer]')).to_be_hidden()
            page.set_viewport_size({'width':1920,'height':1080})
            page.wait_for_function("document.body.classList.contains('operational-board-view')")
            expect(page.locator('.operational-board-exit')).to_be_visible()
            page.locator('.operational-board-exit').click()
            self.assertFalse(active())
            self.assertEqual(page.evaluate('(key) => localStorage.getItem(key)', key), 'off')
            page.locator('[data-operational-sidebar] [data-operational-board-toggle]').click()
            self.assertTrue(active())
            page.set_viewport_size({'width':900,'height':844})
            page.wait_for_function("!document.body.classList.contains('operational-board-view')")
            expect(page.locator('.operational-board-exit')).to_be_hidden()
            expect(page.locator('.neo-mobile-bottom')).to_be_visible()
            self.assertEqual(page.evaluate('(key) => localStorage.getItem(key)', key), 'on')
            # Even a programmatic click on a hidden desktop control is inert on mobile.
            page.locator('.operational-board-exit').dispatch_event('click')
            self.assertFalse(active())
            self.ready(page, '/motherbrain')
            self.assertFalse(active())
            page.set_viewport_size({'width':1920,'height':1080})
            page.wait_for_function("document.body.classList.contains('operational-board-view')")
        finally:
            context.close()
            browser.close()

    def test_webkit(self):
        self.run_engine("webkit")
