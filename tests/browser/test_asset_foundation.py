"""Asset/cascade and interaction checks on actual isolated application pages.

Optional NEO_ASSET_BASELINE points to a prior measurements.json for strict
before/after computed-style comparison. Evidence stays under ignored instance/.
"""
import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlparse

from tests.browser import test_mobile_drawer as existing
from app.services.page_assets import CSS_SEQUENCE, page_stylesheets

SELECTORS = ['body', '.shell', '.content', '.topbar', '[data-operational-sidebar]',
             '.motherbrain-desktop-side-brand', '.motherbrain-sidebar-brand-title',
             '.operational-app-logo', '.operational-topbar-context', '.operational-account-menu',
             '.neo-mobile-bottom', '.neo-mobile-product-name', '[data-mobile-drawer]',
             'h1', 'h2', 'input', 'select', 'button', '.action-button', 'table', 'th', 'td',
             '.portal-app-card', '.gateway-node-card', 'picture', 'form', '.flash',
             '.neostaffing-app-card', '.motherbrain-dashboard-card']
PROPERTIES = ['display', 'position', 'width', 'height', 'minWidth', 'maxWidth', 'minHeight', 'maxHeight',
              'fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'color', 'backgroundColor', 'backgroundImage',
              'borderTop', 'borderRadius', 'padding', 'margin', 'gap', 'gridTemplateColumns', 'gridTemplateRows',
              'alignItems', 'justifyContent', 'flexWrap', 'overflowX', 'overflowY', 'whiteSpace', 'transform', 'zIndex']
PAGES = {'/login': 'auth', '/portal': 'auth', '/rfd': 'neomotherbrain',
         '/motherbrain': 'neomotherbrain', '/neostaffing': 'neostaffing',
         '/neoermac': 'neoermac', '/neosektor': 'neosektor',
         '/neoscorpion/fuel-dispatch': 'neoscorpion', '/neorain': 'neorain', '/neosubzero': 'neosubzero'}
SIZES = [(320, 700), (390, 760), (390, 844), (1366, 768), (1600, 900), (1920, 1080)]


class AssetFoundationBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing.MobileDrawerBrowserTest.setUpClass()
        cls.fixture = existing.MobileDrawerBrowserTest()
        cls.evidence = Path(os.environ.get('NEO_ASSET_EVIDENCE', 'instance/asset-foundation-2026-09-06/final'))
        cls.evidence.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        existing.MobileDrawerBrowserTest.tearDownClass()

    def test_conditional_assets_and_computed_styles(self):
        fixture = self.fixture
        results = []
        baseline_path = os.environ.get('NEO_ASSET_BASELINE')
        baseline = { (r['engine'],r['path'],r['width'],r['height']):r for r in
                    json.loads(Path(baseline_path).read_text(encoding='utf-8')) } if baseline_path else {}
        sequence_paths = {'/static/css/' + r['file'] for r in CSS_SEQUENCE}
        try:
            for engine in ('chromium', 'webkit'):
                browser = getattr(fixture.pw, engine).launch()
                context = browser.new_context()
                context.route(lambda url: not url.startswith(fixture.origin), lambda route: route.abort())
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                try:
                    fixture.login(page)
                    for width, height in SIZES:
                        page.set_viewport_size({'width': width, 'height': height})
                        for path, blueprint in PAGES.items():
                            with self.subTest(engine=engine, path=path, width=width, height=height):
                                errors.clear()
                                fixture.ready(page, path)
                                data = page.evaluate('''({selectors,properties}) => {
                                    const styles={};
                                    for(const selector of selectors) styles[selector]=[...document.querySelectorAll(selector)].slice(0,5).map(el=>{
                                        const s=getComputedStyle(el);return Object.fromEntries(properties.map(p=>[p,s[p]]));
                                    });
                                    return {styles, css:[...document.querySelectorAll('link[rel=stylesheet]')].map(x=>new URL(x.href).pathname),
                                        js:[...document.scripts].filter(x=>x.src).map(x=>new URL(x.src).pathname),
                                        inlineBytes:[...document.scripts].filter(x=>!x.src).reduce((n,x)=>n+new TextEncoder().encode(x.textContent).length,0),
                                        overflow:document.documentElement.scrollWidth>innerWidth+1};
                                }''', {'selectors': SELECTORS, 'properties': PROPERTIES})
                                data.update(engine=engine, path=path, width=width, height=height)
                                for kind in ('css','js'):
                                    data[kind+'Bytes'] = sum(Path('app'+url).stat().st_size for url in data[kind])
                                results.append(data)
                                self.assertFalse(data['overflow'])
                                self.assertEqual(errors, [])
                                self.assertEqual([s for s in data['css'] if s in sequence_paths],
                                                 ['/static/'+s for s in page_stylesheets(blueprint)])
                                self.assertEqual(len(data['js']),len(set(data['js'])))
                                self.assertEqual('/static/js/live_updates.js' in data['js'],
                                                 path not in ('/login','/portal','/rfd','/neostaffing'))
                                self.assertEqual('/static/js/staffing_people.js' in data['js'], path=='/neostaffing')
                                if path=='/motherbrain' and width>900:
                                    self.assertTrue(page.locator('.operational-sidebar-logo').evaluate('''e=>{
                                        const b=e.getBoundingClientRect(), r=e.querySelector('img').getBoundingClientRect();
                                        return !e.textContent.trim() && r.width>=100 && r.left>=b.left && r.right<=b.right+1;
                                    }'''))
                                before=baseline.get((engine,path,width,height))
                                if before:
                                    for selector, styles in data['styles'].items():
                                        # Only the reproduced long-title layout is intentionally changed.
                                        if path=='/motherbrain' and width>900 and selector in (
                                                '[data-operational-sidebar]', '.motherbrain-desktop-side-brand', '.motherbrain-sidebar-brand-title'):
                                            continue
                                        self.assertEqual(styles, before['styles'][selector], selector)
                                if width==390 or (path in ('/login','/portal','/motherbrain') and width>900):
                                    page.screenshot(path=str(self.evidence/f'{engine}-{path.strip("/").replace("/","-")}-{width}-{height}.png'))
                finally:
                    context.close()
                    browser.close()
        finally:
            (self.evidence/'measurements.json').write_text(json.dumps(results,indent=2),encoding='utf-8')

    def test_real_login_pending_preserves_csrf_and_successful_submitter(self):
        for engine in ('chromium','webkit'):
            with self.subTest(engine=engine):
                browser=getattr(self.fixture.pw,engine).launch()
                context=browser.new_context(viewport={'width':390,'height':760})
                page=context.new_page()
                posts=[]
                def observe_post(route):
                    if route.request.method=='POST': posts.append(route.request.post_data)
                    route.continue_()
                try:
                    self.fixture.ready(page,'/login')
                    page.route('**/login',observe_post)
                    page.locator('[name=email]').fill('drawer-admin@example.test')
                    page.locator('[name=password]').fill('BrowserFixture123!')
                    form=page.locator('form[data-interaction-form]')
                    # Capture the real pending presentation separately from native
                    # document navigation (Chromium suspends screenshots mid-navigation).
                    form.evaluate('e=>{window.pendingPreview=window.NeoInteraction.begin(e);}')
                    page.screenshot(path=str(self.evidence/f'{engine}-login-pending.png'))
                    page.evaluate('window.pendingPreview.reset()')
                    with page.expect_request(lambda r: r.method=='POST' and urlparse(r.url).path=='/login') as sent:
                        state=form.locator('button[type=submit]').evaluate('''e=>{
                            e.name='intent';e.value='enter';e.form.requestSubmit(e);
                            e.form.requestSubmit(e);
                            return {state:e.form.dataset.interactionState, busy:e.form.getAttribute('aria-busy'),
                                disabled:e.disabled, ariaDisabled:e.getAttribute('aria-disabled')};
                        }''')
                    self.assertEqual(state,{'state':'pending','busy':'true','disabled':False,'ariaDisabled':'true'})
                    self.assertIn('csrf_token=',sent.value.post_data)
                    self.assertIn('intent=enter',sent.value.post_data)
                    page.wait_for_url(lambda url: urlparse(url).path!='/login')
                    self.assertEqual(urlparse(page.url).path,'/portal')
                    self.assertEqual(len(posts),1)
                finally:
                    context.close()
                    browser.close()

    def test_anonymous_login_assets_and_viewports(self):
        results=[]
        for engine in ('chromium','webkit'):
            browser=getattr(self.fixture.pw,engine).launch()
            context=browser.new_context()
            page=context.new_page()
            try:
                for width,height in SIZES:
                    with self.subTest(engine=engine,width=width,height=height):
                        page.set_viewport_size({'width':width,'height':height})
                        self.fixture.ready(page,'/login')
                        data=page.evaluate('''()=>({
                            css:[...document.querySelectorAll('link[rel=stylesheet]')].map(e=>new URL(e.href).pathname),
                            js:[...document.scripts].filter(e=>e.src).map(e=>new URL(e.src).pathname),
                            fit:document.documentElement.scrollWidth<=innerWidth+1 && document.documentElement.scrollHeight<=innerHeight+1
                        })''')
                        self.assertTrue(data['fit'])
                        self.assertEqual(data['js'],['/static/js/interaction_states.js'])
                        self.assertNotIn('/static/css/mobile_drawer.css',data['css'])
                        results.append(dict(data,engine=engine,width=width,height=height,path='/login'))
                        if width in (390,1920):
                            page.screenshot(path=str(self.evidence/f'{engine}-login-anonymous-{width}-{height}.png'))
            finally:
                context.close()
                browser.close()
        (self.evidence/'anonymous-login.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
