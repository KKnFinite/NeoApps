"""One Chromium route crawl, using the existing isolated shell fixture."""
import json
import unittest
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from tests.browser import test_mobile_drawer as existing
from tests.browser.test_gateway_dock_audit import PREFIXES
from app.extensions import db
from app.models import NeoSektorOperationalSetting, SortDateOperation
from app.services.access_control import ensure_default_gateway_and_nodes


class GatewayTypographyTest(unittest.TestCase):
    def test_gateway_workhorse_crawl(self):
        kind = existing.MobileDrawerBrowserTest
        kind.setUpClass()
        browser = kind.pw.chromium.launch()
        evidence = Path('instance/browser-evidence/gateway-typography')
        evidence.mkdir(parents=True, exist_ok=True)
        results, failures = [], []
        try:
            with kind.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                db.session.add(NeoSektorOperationalSetting(gateway_id=gateway.id, gateway_code=gateway.code, integration_mode='neo_only'))
                db.session.add(SortDateOperation(gateway_id=gateway.id, gateway_code=gateway.code, sort_name='PM', sort_date=date.today()))
                db.session.commit()
            page = browser.new_page(viewport={'width':390,'height':844})
            kind().login(page)
            pending = sorted({r.rule for r in kind.app.url_map.iter_rules() if 'GET' in r.methods and not r.arguments and r.rule.startswith(PREFIXES)})
            seen, pages = set(), []
            while pending:
                path = pending.pop(0)
                if path in seen:
                    continue
                seen.add(path)
                response = page.request.get(kind.origin+path, max_redirects=0)
                if response.status in (301,302,303,307,308):
                    target = urlsplit(response.headers.get('location','')).path
                    if target.startswith(PREFIXES):
                        pending.append(target)
                elif response.status == 200 and 'text/html' in response.headers.get('content-type',''):
                    html = response.text()
                    if 'data-gateway-typography' in html:
                        pages.append(path)
                    class Links(HTMLParser):
                        def handle_starttag(self, tag, attrs):
                            target = urlsplit(dict(attrs).get('href','') if tag == 'a' else '')
                            if not target.netloc and target.path.startswith(PREFIXES):
                                pending.append(target.path)
                    Links().feed(html)
            for path in pages:
                kind().ready(page, path)
                result = page.evaluate('''()=>{
                    const family=e=>getComputedStyle(e).fontFamily.split(',')[0].replaceAll('"','');
                    const visible=e=>e.getBoundingClientRect().width&&e.getBoundingClientRect().height;
                    const texts=[...document.querySelectorAll('body *')].filter(e=>visible(e)&&[...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()));
                    const dock=[...document.querySelectorAll('.neo-mobile-bottom,.mobile-bottom-nav')].find(visible);
                    return {body:family(document.body),overflow:document.documentElement.scrollWidth>innerWidth+1,
                        fonts:[...new Set(texts.map(family))],
                        blockers:texts.filter(e=>!['NeoFontPlain','NeoFont','NeoFontLite','monospace'].includes(family(e))).map(e=>[e.tagName,e.className,family(e)]),
                        bottom:dock?.getBoundingClientRect().bottom,
                        plain:[...document.fonts].filter(f=>f.family==='NeoFontPlain').map(f=>[f.weight,f.status])};
                }''')
                result['path'] = path
                results.append(result)
                if result['body'] != 'NeoFontPlain' or result['overflow'] or result['blockers'] or abs(result['bottom']-844)>1:
                    failures.append(result)
                if path in ('/rfd','/neosektor/live-counts'):
                    page.screenshot(path=str(evidence/(path.strip('/').replace('/','-')+'-390.png')))
                if path in ('/rfd','/neosektor','/neosektor/live-counts'):
                    # Compare deliberate display declarations against the same
                    # page without the new workhorse stylesheet.
                    self.assertEqual(page.evaluate('''()=>{
                        const link=document.querySelector('link[href*="neofontplain.css"]');
                        const style=e=>{const s=getComputedStyle(e);return [s.fontFamily,s.fontSize,s.fontWeight,s.letterSpacing].join('|');};
                        const es=[...document.querySelectorAll('body *')].filter(e=>/^"?NeoFont(?:Lite)?[",]/.test(getComputedStyle(e).fontFamily));
                        const before=es.map(style);link.disabled=true;
                        const changes=es.flatMap((e,i)=>style(e)===before[i]?[]:[[e.tagName,e.className,before[i],style(e)]]);
                        link.disabled=false;return changes;
                    }'''), [], path)
                    selectors = { '/rfd':('.neo-mobile-product-name','NeoFontLite'),
                        '/neosektor':('.sektor-command-copy strong','NeoFontLite'),
                        '/neosektor/live-counts':('.operational-mobile-identity small','NeoFontLite') }
                    selector, family = selectors[path]
                    self.assertEqual(page.locator(selector).first.evaluate("e=>getComputedStyle(e).fontFamily.split(',')[0].replaceAll('\"','')"), family)
                if path == '/neosektor/live-counts':
                    self.assertEqual(sorted(result['plain']), [['400','loaded'],['600','loaded']])
                    self.assertLessEqual(page.locator('.neosektor-live-wave-row').evaluate('e=>e.getBoundingClientRect().top-e.previousElementSibling.getBoundingClientRect().bottom'),12)
                if path == '/rfd':
                    page.locator('[data-drawer-toggle]').click()
                    self.assertIn('NeoFontPlain',page.locator('.neo-drawer-links a').first.evaluate('e=>getComputedStyle(e).fontFamily'))
                    self.assertAlmostEqual(page.locator('.neo-mobile-bottom').evaluate('e=>e.getBoundingClientRect().bottom'),844,delta=1)
                    page.locator('[data-drawer-toggle]').click()
            anonymous = browser.new_page(viewport={'width':390,'height':844})
            for path in ('/portal','/login','/create-account','/forgot-password','/change-password','/neostaffing','/neobid','/admin/users'):
                target = anonymous if path in ('/login','/create-account','/forgot-password') else page
                response = target.goto(kind.origin+path)
                self.assertEqual(response.status,200,path)
                if target == anonymous:
                    self.assertEqual(urlsplit(target.url).path,path)
                self.assertEqual(target.locator('link[href*="neofontplain.css"]').count(),0,path)
                self.assertFalse(target.evaluate("[...document.fonts].some(f=>f.family==='NeoFontPlain')"),path)
                self.assertNotIn('NeoFontPlain',target.locator('body').evaluate('e=>getComputedStyle(e).fontFamily'),path)
            anonymous.close()
            page.set_viewport_size({'width':1920,'height':1080})
            kind().ready(page,'/neosektor')
            self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
            self.assertEqual(page.locator('.operational-node-identity small').evaluate('e=>getComputedStyle(e).letterSpacing'),'1.6px')
            self.assertTrue(page.locator('.sektor-command-copy strong, .operational-node-identity .neo-brand').evaluate_all('''es=>es.every(e=>{
                const r=document.createRange();r.selectNodeContents(e);const t=r.getBoundingClientRect(),b=(e.closest('header')||e.closest('a')).getBoundingClientRect();
                // Nested brand spans yield box/text rectangles with 1px font
                // overhang; compare line positions and actual link clearance,
                // not glyph ink height against the CSS line-height.
                const tops=[...r.getClientRects()].map(rect=>rect.top);
                return t.left>=b.left&&t.right<=b.right&&t.top>=b.top&&t.bottom<=b.bottom&&Math.max(...tops)-Math.min(...tops)<=1;
            })'''))
            page.screenshot(path=str(evidence/'sektor-desktop.png'))
            for prefix in ('/rfd','/motherbrain','/neosektor','/neoscorpion','/neoermac','/neorain','/neosubzero'):
                self.assertTrue(any(p.startswith(prefix) for p in pages),prefix)
            print(f'Chromium: {len(pages)} Gateway pages at 390x844; 8 exclusions; desktop 1920x1080; {len(failures)} failures')
            self.assertEqual(failures,[])
        finally:
            (evidence/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
            browser.close()
            kind.tearDownClass()
