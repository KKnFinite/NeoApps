"""Protected, GET-only runtime diagnostic at desktop and phone widths."""
from datetime import datetime
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from unittest.mock import patch
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect
from app import create_app
from app.extensions import db
from app.models import Gateway,User
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.permission_rules import ensure_default_permission_rules
from app.services.password_policy import set_user_password


class CertificationBrowserTest(TestCase):
    def test_readonly_four_sizes(self):
        with TemporaryDirectory() as directory:
            config=type('Config',(),{'SECRET_KEY':'test','TESTING':True,
                'SQLALCHEMY_DATABASE_URI':'sqlite:///'+directory.replace('\\','/')+'/cert.db',
                'SQLALCHEMY_TRACK_MODIFICATIONS':False})
            app=create_app(config)
            with app.app_context():
                db.create_all();ensure_default_permission_rules()
                db.session.add(Gateway(code='RFD',name='RFD'));db.session.flush()
                user=User(username='cert_browser',email='cert_browser@example.test',role='grandmaster',
                    full_name='Certification Browser',employee_id='CERT-BROWSER',is_active=True,email_verified_at=datetime.utcnow())
                set_user_password(user,'TestPassword123!');db.session.add(user);db.session.flush()
                backfill_default_gateway_node_roles(user,role='grandmaster');db.session.commit()
            server=make_server('127.0.0.1',0,app,threaded=True)
            thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with patch('app.neonodes.neorain.certification_routes.RuntimeGoogleAdapter') as adapter,sync_playwright() as p:
                    adapter.return_value.diagnose.return_value={'field':'sta','status':'BLOCKED','authenticated_read_ok':True,'reason':'No Google-side race fence.'}
                    browser=p.chromium.launch(headless=True)
                    try:
                        for width,height in ((1440,900),(1920,1080),(390,844),(375,568)):
                            context=browser.new_context(viewport={'width':width,'height':height})
                            page=context.new_page();errors=[];posts=[]
                            page.on('pageerror',lambda error:errors.append(str(error)))
                            base=f'http://127.0.0.1:{server.server_port}'
                            page.goto(base+'/login');page.locator('[name="email"]').fill('cert_browser@example.test')
                            page.locator('[name="password"]').fill('TestPassword123!');page.locator('button[type="submit"]').click()
                            page.goto(base+'/neorain/google-certification')
                            page.on('request',lambda request:posts.append(request.url) if request.method!='GET' else None)
                            page.locator('[data-cert-field="sta"]').click()
                            expect(page.locator('#cert-result')).to_contain_text('BLOCKED')
                            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                            self.assertEqual(posts,[]);self.assertEqual(errors,[])
                            context.close()
                    finally:browser.close()
            finally:
                server.shutdown();thread.join(timeout=5)
                with app.app_context():db.session.remove();db.engine.dispose()
