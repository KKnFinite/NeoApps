"""Real browser HTTP cache checks. Routing interception is deliberately absent:
Playwright request routing disables the browser cache being measured here.
"""
import json
import threading
import unittest
from collections import Counter
from pathlib import Path

from flask import request, url_for
from tests.browser import test_mobile_drawer as fixtures


class ImageDeliveryBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.MobileDrawerBrowserTest.setUpClass()
        cls.f = fixtures.MobileDrawerBrowserTest()
        cls.counts = Counter()
        cls.lock = threading.Lock()
        cls.evidence = Path('instance/image-bandwidth-20261105/cache')
        cls.evidence.mkdir(parents=True, exist_ok=True)
        app = cls.f.app

        @app.get('/__isolated_image_cache_probe')
        def probe():
            paths = ('images/optimized/newlogo_scorpion_menu.png',
                     'images/logos/newlogo_scorpion.png',
                     'images/hero/neoapps_login_mobile.png')
            if request.args.get('original'):
                # Literal original URLs reproduce pre-optimization delivery;
                # url_for now intentionally selects the derivative.
                paths = ('images/logos/newlogo_scorpion.png',
                         'images/logos/newlogo_scorpion.png',
                         'images/hero/neoapps_login_mobile.png')
            response = app.response_class(''.join(
                '<img alt="" src="'+('/static/'+p if request.args.get('original') else url_for('static', filename=p))+'">' for p in paths))
            response.headers['Cache-Control'] = 'no-store'
            return response

        @app.after_request
        def count_images(response):
            if request.path.startswith('/static/images/'):
                with cls.lock:
                    cls.counts[(request.path,response.status_code)] += 1
            return response

    @classmethod
    def tearDownClass(cls):
        fixtures.MobileDrawerBrowserTest.tearDownClass()

    def test_cold_navigation_repeat_and_reload_do_not_retransfer_images(self):
        results=[]
        for engine in ('chromium','webkit'):
            browser=getattr(self.f.pw,engine).launch()
            context=browser.new_context(service_workers='block')
            page=context.new_page()
            try:
                page.goto(self.f.origin+'/__isolated_image_cache_probe?original=1')
                page.evaluate('async()=>{await Promise.all([...document.images].map(i=>i.decode()))}')
                original=page.evaluate('''()=>performance.getEntriesByType('resource')
                    .filter(e=>e.name.includes('/static/images/')).map(e=>({
                    url:e.name,transferBytes:e.transferSize,bodyBytes:e.encodedBodySize}))''')
                self.assertEqual(sorted(r['bodyBytes'] for r in original),[2242897,2504774])
                results.append(dict(engine=engine,step='original-cold',resources=original))
                for step in ('cold','navigation','reload'):
                    with self.lock:
                        before=self.counts.copy()
                    if step=='reload':
                        response=page.reload()
                    else:
                        response=page.goto(self.f.origin+'/__isolated_image_cache_probe?step='+step)
                    self.assertEqual(response.status,200)
                    page.evaluate('async()=>{await Promise.all([...document.images].map(i=>i.decode()))}')
                    resources=page.evaluate('''()=>performance.getEntriesByType('resource')
                        .filter(e=>e.name.includes('/static/images/')).map(e=>({
                        url:e.name,transferBytes:e.transferSize,bodyBytes:e.encodedBodySize}))''')
                    with self.lock:
                        requests=sum((self.counts-before).values())
                    self.assertEqual(len(resources),3)
                    if step=='cold':
                        self.assertEqual(requests,3)
                        self.assertTrue(all(r['bodyBytes']>0 for r in resources))
                    else:
                        self.assertEqual(requests,0,resources)
                        self.assertTrue(all(r['transferBytes']==0 for r in resources),resources)
                    results.append(dict(engine=engine,step=step,serverImageRequests=requests,resources=resources))
            finally:
                context.close();browser.close()
        (self.evidence/'network.json').write_text(json.dumps(results,indent=2))

    def test_real_menu_mobile_desktop_rendering_and_pwa_navigation(self):
        results=[]
        for engine in ('chromium','webkit'):
            browser=getattr(self.f.pw,engine).launch()
            context=browser.new_context()
            page=context.new_page()
            try:
                self.f.login(page)
                for width,height in ((390,844),(1366,768)):
                    page.set_viewport_size(dict(width=width,height=height))
                    self.f.ready(page,'/rfd')
                    if width<901:
                        page.locator('[data-drawer-nodes]').click()
                    else:
                        page.locator('[data-character-switcher]').filter(visible=True).first.locator('summary').click()
                    icon=page.locator('img[src*="newlogo_scorpion_menu.png"]').filter(visible=True).first
                    icon.wait_for(state='visible')
                    self.assertTrue(icon.is_visible())
                    result=icon.evaluate('''e=>{let b=e.getBoundingClientRect();return {
                        width:b.width,height:b.height,naturalWidth:e.naturalWidth,
                        overflow:document.documentElement.scrollWidth>innerWidth+1}}''')
                    self.assertEqual(result['naturalWidth'],64)
                    self.assertLessEqual(result['width'],28)
                    self.assertEqual(result['width'],result['height'])
                    self.assertFalse(result['overflow'])
                    page.screenshot(path=str(self.evidence/f'{engine}-menu-{width}.png'))
                    results.append(dict(engine=engine,viewportWidth=width,**result))
                    self.f.ready(page,'/portal')
                    self.assertEqual(page.locator('img[src*="neoapps_portal_desktop.webp"],source[srcset*="neoapps_login_mobile.webp"]').count(),2)
                    self.assertNotIn('immutable',page.request.get(self.f.origin+'/portal').headers.get('cache-control',''))
                # Existing registration succeeds with the image cache in use.
                page.wait_for_function('async()=>!!(await navigator.serviceWorker.getRegistration())')
            finally:
                context.close();browser.close()
        (self.evidence/'rendering.json').write_text(json.dumps(results,indent=2))

    def test_anonymous_login_artwork_and_forms_keep_mobile_desktop_layout(self):
        for engine in ('chromium','webkit'):
            browser=getattr(self.f.pw,engine).launch()
            context=browser.new_context()
            page=context.new_page()
            try:
                for width,height in ((390,844),(1366,768)):
                    page.set_viewport_size(dict(width=width,height=height))
                    self.f.ready(page,'/login')
                    hero=page.locator('picture.portal-login-hero img')
                    current=hero.evaluate('e=>e.currentSrc')
                    self.assertIn('neoapps_login_mobile.webp' if width<901 else 'neoapps_login_desktop.webp',current)
                    self.assertTrue(page.locator('form[action="/login"] input[name="password"]').is_visible())
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                    page.screenshot(path=str(self.evidence/f'{engine}-anonymous-login-{width}.png'))
            finally:
                context.close();browser.close()
