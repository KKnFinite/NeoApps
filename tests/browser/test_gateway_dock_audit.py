"""Reachable Gateway HTML dock audit using local synthetic data only."""
import json
import os
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from tests.browser import test_mobile_drawer as existing
from app.extensions import db
from app.models import NeoSektorOperationalSetting, SortDateOperation
from app.services.access_control import ensure_default_gateway_and_nodes

PREFIXES = ('/rfd', '/motherbrain', '/admin', '/nodes', '/neoermac', '/neosektor', '/neoscorpion', '/neorain', '/neosubzero', '/neoreptile')
MEASURE = '''()=>{
 const dock=[...document.querySelectorAll('.neo-mobile-bottom,.mobile-bottom-nav')].find(e=>e.getBoundingClientRect().height);
 if(!dock)return null;
 const r=dock.getBoundingClientRect(),s=getComputedStyle(dock),header=document.querySelector('.operational-mobile-header,.gateway-mobile-header,[data-mobile-topbar]');
 return {bottom:r.bottom,top:r.top,height:r.height,left:r.left,right:r.right,position:s.position,margin:s.marginBottom,transform:s.transform,translate:s.translate,padding:s.paddingBottom,
 overflow:document.documentElement.scrollWidth>innerWidth+1,header:header?header.getBoundingClientRect().height:0,
 clearance:parseFloat(getComputedStyle(document.querySelector('.shell > .content')).paddingBottom)};
}'''


class GatewayDockAuditTest(unittest.TestCase):
    def test_reachable_gateway_mobile_docks(self):
        kind = existing.MobileDrawerBrowserTest
        kind.setUpClass()
        evidence = Path('instance/browser-evidence/gateway-dock-audit')
        evidence.mkdir(parents=True, exist_ok=True)
        results, inventory, failures = [], [], []
        baseline = os.environ.get('NEO_DOCK_BASELINE') == '1'
        try:
            with kind.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                db.session.add(NeoSektorOperationalSetting(gateway_id=gateway.id, gateway_code=gateway.code, integration_mode='neo_only'))
                db.session.add(SortDateOperation(gateway_id=gateway.id, gateway_code=gateway.code, sort_name='PM', sort_date=date.today()))
                db.session.commit()
            routes = sorted({r.rule for r in kind.app.url_map.iter_rules() if 'GET' in r.methods and not r.arguments and r.rule.startswith(PREFIXES)})
            browser = kind.pw.chromium.launch()
            page = browser.new_page(viewport={'width':390,'height':844})
            fixture = kind()
            fixture.login(page)
            pending, seen, pages = list(routes), set(), []
            # The registered literal routes plus actual linked detail pages cover
            # the fixture's reachable graph. Never invent object IDs or POST.
            while pending:
                path = pending.pop(0)
                if path in seen:
                    continue
                seen.add(path)
                response = page.request.get(kind.origin+path, max_redirects=0)
                row = {'path':path,'status':response.status}
                if response.status in (301,302,303,307,308):
                    target = urlsplit(response.headers.get('location','')).path
                    row['redirect'] = target
                    if target.startswith(PREFIXES) and target not in seen:
                        pending.append(target)
                elif response.status == 200 and 'text/html' in response.headers.get('content-type',''):
                    html = response.text()
                    row['dock_markup'] = 'class="neo-mobile-bottom' in html or 'class="mobile-bottom-nav ' in html
                    if row['dock_markup']:
                        pages.append(path)
                    from html.parser import HTMLParser
                    class Links(HTMLParser):
                        def handle_starttag(self, tag, attrs):
                            href = dict(attrs).get('href','') if tag == 'a' else ''
                            target = urlsplit(href)
                            if not target.netloc and target.path.startswith(PREFIXES) and target.path not in seen:
                                pending.append(target.path)
                    Links().feed(html)
                inventory.append(row)
            browser.close()
            self.assertIn('/neosektor', pages)
            if os.environ.get('NEO_DOCK_PATHS'):
                pages = [p for p in pages if p in os.environ['NEO_DOCK_PATHS'].split(',')]
            for engine in ('chromium','webkit'):
                browser = getattr(kind.pw,engine).launch()
                try:
                    page = browser.new_page(viewport={'width':390,'height':844})
                    fixture.login(page)
                    for path in pages:
                        page.goto(kind.origin+path, wait_until='domcontentloaded')
                        page.evaluate('async()=>{await document.fonts.ready;}')
                        for safe in (0,34):
                            page.evaluate('(v)=>document.documentElement.style.setProperty("--neo-safe-bottom",v+"px")', safe)
                            initial = page.evaluate(MEASURE)
                            entry = {'engine':engine,'path':path,'safe':safe,'closed':initial}
                            if not initial:
                                failures.append(f'{engine} {path}: no visible dock')
                                results.append(entry)
                                continue
                            for action in ('menu','close','nodes','switch','close'):
                                selector = '[data-drawer-nodes]' if action=='nodes' else '[data-drawer-toggle]'
                                if page.locator(selector).count():
                                    page.locator(selector).click()
                                    entry[action+str(len(entry))] = page.evaluate(MEASURE)
                                    panel = page.locator('[data-mobile-drawer]')
                                    if panel.is_visible():
                                        box = panel.bounding_box()
                                        if box['y'] < -1 or box['y']+box['height'] > initial['top']+1:
                                            failures.append(f'{engine} {path}: drawer clearance')
                            if page.locator('.mobile-bottom-nav').count():
                                for trigger in page.locator('[data-mobile-popover-trigger]').all():
                                    trigger.click()
                                    panel = page.locator('[data-mobile-popover-anchor="'+trigger.get_attribute('data-mobile-popover-trigger')+'"]')
                                    self.assertEqual(panel.get_attribute('aria-hidden'), 'false')
                                    # Wait for the existing CSS open animation, not a guessed sleep.
                                    page.wait_for_function('(e)=>!e.classList.contains("is-opening")', arg=panel.element_handle())
                                    box = panel.bounding_box()
                                    if box['y']+box['height'] > initial['top']+1 or box['x'] < -1 or box['x']+box['width'] > 391:
                                        failures.append(f'{engine} {path}: legacy popover bounds {box}')
                                    entry['popover'+str(len(entry))] = page.evaluate(MEASURE)
                                    trigger.click()
                            for state, value in entry.items():
                                if not isinstance(value,dict):
                                    continue
                                if abs(value['bottom']-844)>1 or abs(value['height']-(54+safe))>1 or abs(value['left'])>1 or abs(value['right']-390)>1 or value['position'] != 'fixed' or value['transform'] != 'none' or value['translate'] != 'none' or value['margin'] != '0px':
                                    failures.append(f'{engine} {path} safe={safe} {state}: {value}')
                                if value['overflow']:
                                    failures.append(f'{engine} {path} safe={safe}: horizontal overflow')
                                if value['clearance'] < 74+safe-1:
                                    failures.append(f'{engine} {path}: content clearance {value["clearance"]}')
                                if value['header'] != initial['header']:
                                    failures.append(f'{engine} {path}: header height changed')
                            results.append(entry)
                        if path in ('/rfd','/neosektor','/neorain/inbound','/motherbrain/permissions','/neosubzero/settings','/neoscorpion/settings/spear/calibration'):
                            page.wait_for_function('()=>!document.querySelector(".mobile-bottom-popover.is-closing")')
                            page.screenshot(path=str(evidence/f'{engine}-{path.strip("/").replace("/","-")}.png'))
                    print(f'{engine}: {len(pages)} pages checked', flush=True)
                finally:
                    browser.close()
            print(f'{len(pages)} dock pages; {len(results)} engine/safe-area visits; {len(failures)} failures')
            if not baseline:
                self.assertEqual(failures, [], '\n'.join(failures[:12]))
        finally:
            (evidence/('baseline.json' if baseline else 'results.json')).write_text(json.dumps({'inventory':inventory,'measurements':results,'failures':failures},indent=2),encoding='utf-8')
            kind.tearDownClass()
