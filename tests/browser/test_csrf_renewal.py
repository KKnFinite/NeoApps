"""Chromium/WebKit shift-length saves against real CSRF/auth guards, isolated DB."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs
from flask import jsonify,render_template_string,request
from flask_login import login_required
from playwright.sync_api import expect
from tests.browser import test_mobile_drawer as fixtures


class CsrfRenewalBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=fixtures.MobileDrawerBrowserTest
        cls.fixture.setUpClass()
        cls.offset=0;cls.saved=[]
        cls.clock=patch('app.services.csrf.time',SimpleNamespace(time=lambda:time.time()+cls.offset));cls.clock.start()
        @login_required
        def lab():
            return render_template_string('''{% extends "base.html" %}{% block content %}
            <form id="native" method="post" action="/_csrf-lab/save" data-interaction-form>
            <input name="action" value="keep-action"><input name="notes" value="Native draft">
            <button type="submit" name="intent" value="complete">Complete</button></form>
            <form id="ajax" method="post" action="/_csrf-lab/save"><input name="notes" value="AJAX draft">
            <button type="submit">Save</button></form><output id="result"></output>
            <script>window.originalDocument=true;
            document.querySelector('#ajax').addEventListener('submit',async event=>{
                event.preventDefault();try{const response=await fetch(event.target.getAttribute('action'),{
                method:'POST',body:new FormData(event.target),headers:{Accept:'application/json'}});
                document.querySelector('#result').textContent=String(response.status);
                }catch(error){document.querySelector('#result').textContent=error.message;}
            });</script>{% endblock %}''')
        @login_required
        def save():
            status=int(request.args.get('status','200'))
            if status!=200:return jsonify(error='Fixture rejected'),status
            cls.saved.append({key:value for key,value in request.form.items() if key!='csrf_token'})
            return jsonify(ok=True,fields=cls.saved[-1])
        cls.fixture.app.add_url_rule('/_csrf-lab',view_func=lab)
        cls.fixture.app.add_url_rule('/_csrf-lab/save',view_func=save,methods=['POST','PUT','PATCH','DELETE'])

    @classmethod
    def tearDownClass(cls):
        cls.clock.stop();cls.fixture.tearDownClass()

    def open(self,engine,width=1920):
        self.__class__.offset=0;self.__class__.saved.clear()
        browser=getattr(self.fixture.pw,engine).launch(headless=True)
        context=browser.new_context(viewport={'width':width,'height':900})
        origin=self.fixture.origin
        context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin) else route.abort())
        page=context.new_page();page.set_default_timeout(8000)
        self.fixture().login(page)
        page.goto(origin+'/_csrf-lab')
        return browser,context,page

    def age(self,page,hours=3):
        self.__class__.offset+=hours*3600
        page.evaluate('''offset=>{window.csrfOffset=offset;
            if(!window.originalNow){window.originalNow=Date.now;Date.now=()=>window.originalNow()+window.csrfOffset;}
        }''',self.__class__.offset*1000)

    def test_native_expired_submit_preserves_button_and_exactly_one_action(self):
        for engine,width in (('chromium',1920),('webkit',390)):
            with self.subTest(engine=engine):
                browser,context,page=self.open(engine,width)
                try:
                    page.locator('#native [name=notes]').fill('Keep edited native draft')
                    self.age(page)
                    with page.expect_response(lambda response:'/_csrf-lab/save' in response.url and response.request.method=='POST') as sent:
                        page.locator('#native button').evaluate('button=>{button.form.requestSubmit(button);button.form.requestSubmit(button);}')
                    page.wait_for_load_state('domcontentloaded')
                    self.assertEqual(sent.value.status,200)
                    values=parse_qs(sent.value.request.post_data)
                    self.assertEqual(values['intent'],['complete']);self.assertEqual(values['action'],['keep-action'])
                    self.assertEqual(values['notes'],['Keep edited native draft'])
                    self.assertEqual(len(self.saved),1)
                finally:context.close();browser.close()

    def test_overnight_ajax_and_dynamic_native_forms(self):
        for engine,width in (('chromium',1920),('webkit',390)):
            with self.subTest(engine=engine):
                browser,context,page=self.open(engine,width)
                try:
                    old=page.locator('meta[name=csrf-token]').get_attribute('content')
                    page.locator('#ajax [name=notes]').fill('Shift draft remains')
                    self.age(page,14)
                    with page.expect_response(lambda response:'/csrf-token' in response.url) as renewed:
                        page.evaluate("window.dispatchEvent(new Event('focus'))")
                    self.assertEqual(renewed.value.status,200)
                    self.assertNotEqual(page.locator('meta[name=csrf-token]').get_attribute('content'),old)
                    self.assertTrue(page.evaluate('window.originalDocument'))
                    page.locator('#ajax button').click();expect(page.locator('#result')).to_have_text('200')
                    expect(page.locator('#ajax [name=notes]')).to_have_value('Shift draft remains')
                    page.evaluate('''()=>{const form=document.createElement('form');form.id='dynamic';form.method='post';form.action='/_csrf-lab/save';
                        form.innerHTML='<input name="notes" value="Dynamic draft"><button name="intent" value="dynamic">Save</button>';document.body.append(form);}''')
                    self.age(page,3)
                    with page.expect_response(lambda response:'/_csrf-lab/save' in response.url and response.request.method=='POST') as saved:
                        page.locator('#dynamic button').click()
                    page.wait_for_load_state('domcontentloaded')
                    self.assertEqual(parse_qs(saved.value.request.post_data)['notes'],['Dynamic draft'])
                    self.assertEqual(len(self.saved),2)
                finally:context.close();browser.close()

    def test_renewal_failure_retains_draft_and_sends_nothing(self):
        browser,context,page=self.open('chromium')
        try:
            page.locator('#native [name=notes]').fill('Offline native draft');self.age(page)
            page.route('**/csrf-token',lambda route:route.abort())
            page.locator('#native button').click()
            expect(page.locator('[data-csrf-feedback]')).to_contain_text('Nothing was submitted')
            expect(page.locator('#native [name=notes]')).to_have_value('Offline native draft')
            self.assertTrue(page.evaluate('window.originalDocument'));self.assertEqual(len(self.saved),0)
        finally:context.close();browser.close()

    def test_two_tabs_renew_without_invalidating_each_other(self):
        browser,context,first=self.open('chromium')
        try:
            second=context.new_page();second.goto(self.fixture.origin+'/_csrf-lab')
            self.age(first)
            second.evaluate('''offset=>{const now=Date.now;Date.now=()=>now()+offset;}''',self.offset*1000)
            for page in (first,second):
                with page.expect_response(lambda response:'/csrf-token' in response.url):
                    page.evaluate("window.dispatchEvent(new Event('focus'))")
            for page in (first,second):
                page.locator('#ajax button').click();expect(page.locator('#result')).to_have_text('200')
            self.assertEqual(len(self.saved),2)
        finally:context.close();browser.close()

    def test_operational_screens_share_current_token_after_shift(self):
        browser,context,page=self.open('chromium')
        try:
            paths=('/motherbrain','/neosektor/tunnel-conductor','/neoermac/door-view','/neorain/inbound',
                   '/neoscorpion/fuel-dispatch','/neoscorpion/fueler','/neoscorpion/hanzo',
                   '/neosubzero/operations','/neostaffing/people','/portal/manage')
            for path in paths:
                with self.subTest(path=path):
                    response=page.goto(self.fixture.origin+path)
                    self.assertEqual(response.status,200)
                    self.age(page)
                    result=page.evaluate('''async()=>{const before=document.querySelector('meta[name="csrf-token"]').content;
                        const response=await fetch('/_csrf-lab/save',{method:'POST',body:new URLSearchParams({notes:'Representative guarded save'}),
                            headers:{Accept:'application/json'}});
                        return {status:response.status,changed:before!==document.querySelector('meta[name="csrf-token"]').content,
                            synced:Array.from(document.querySelectorAll('form[method=post] input[name=csrf_token]')).every(input=>input.value===window.neoAppsCsrfToken)};}''')
                    self.assertEqual(result,{'status':200,'changed':True,'synced':True})
        finally:context.close();browser.close()
