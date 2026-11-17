"""Real integrated management UI; isolated auth, mail and operational records."""
import os
import unittest
from contextlib import closing
from datetime import date,datetime
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,SortDateOperation,NeoScorpionFuelTruck,NeoScorpionSortTruck,NeoScorpionVendorAccount,
    NeoScorpionTruckDriver,NeoScorpionSortAssetState,NeoScorpionVendorNight)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services import neoscorpion_vendors as vendors
from tests.browser import test_mobile_drawer as fixtures


class AssetsBrowserTest(unittest.TestCase):
    def test_vendor_account_controls_follow_saved_state(self):
        fixture = fixtures.MobileDrawerBrowserTest
        fixture.setUpClass()
        fixture.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS'] = 1000
        try:
            with fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                actor = User.query.filter_by(username='drawer-admin').one()
                operation = SortDateOperation(generated_by_user_id=actor.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name='night', window_minutes=60)
                db.session.add(operation)
                db.session.flush()
                operation_id = operation.id
                account_ids = {}
                for name in ('Registered', 'Pending', 'Invited'):
                    user, token = vendors.invite(gateway, actor, f'{name.lower()}-controls@example.test', name)
                    if name != 'Invited':
                        vendors.accept_invitation(token, 'BrowserFixture123!', 'BrowserFixture123!')
                    account_ids[name] = user.id
                db.session.add(NeoScorpionVendorNight(sort_date_operation_id=operation_id,
                    user_id=account_ids['Pending'], changed_by_user_id=actor.id, status='pending'))
                db.session.commit()

            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                       side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]):
                with closing(fixture.pw.chromium.launch()) as browser:
                    dispatch = browser.new_page(viewport={'width': 1440, 'height': 900})
                    errors = []
                    dispatch.on('pageerror', lambda error: errors.append(str(error)))
                    fixture().login(dispatch)
                    dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                    fixture().ready(dispatch, '/neoscorpion/fuel-dispatch?assets=open')
                    accounts = dispatch.locator('#nightly-trucks [data-assets-disclosure=accounts]')
                    accounts.locator(':scope > summary').click()
                    registered, pending, invited = [accounts.locator(f'[data-assets-unit="account-{user_id}"]')
                        for user_id in account_ids.values()]

                    def check(row, status, buttons, feedback=None):
                        expect(row.locator('[data-vendor-account-state]')).to_have_text(status)
                        expect(row.get_by_role('button')).to_have_text(buttons)
                        if feedback:
                            expect(row.get_by_role('status')).to_have_text(feedback)

                    def act(row, name, status=200, *, stale=False):
                        button = row.get_by_role('button', name=name, exact=True)
                        with dispatch.expect_response(lambda response: response.request.method == 'POST'
                                and response.url.endswith('/driver/manage')) as result:
                            if stale:
                                button.evaluate('''button => {
                                    const version = button.form.elements.version;
                                    const original = version.value;
                                    version.value = '0';
                                    button.click();
                                    version.value = original;
                                }''')
                            else:
                                button.click()
                        self.assertEqual(result.value.status, status, result.value.json().get('error'))

                    approve = 'APPROVE TONIGHT'
                    disable = 'DISABLE ACCOUNT'
                    enable = 'ENABLE ACCOUNT'
                    check(registered, 'ENABLED · NOT APPROVED · EMAIL VERIFIED', [approve, 'DENY', disable])
                    check(pending, 'ENABLED · PENDING · EMAIL VERIFIED', [approve, 'DENY', disable])
                    check(invited, 'ENABLED · NOT APPROVED · REGISTRATION PENDING', [disable])
                    self.assertTrue(registered.evaluate('''row => {
                        const bounds = Array.from(row.querySelectorAll('button'), button => button.getBoundingClientRect());
                        return bounds.every(box => box.top === bounds[0].top && box.height <= 32);
                    }'''))

                    expect(accounts.get_by_role('link', name='OPEN USER MANAGEMENT')).to_be_visible()
                    act(registered, 'DENY')
                    check(registered, 'ENABLED · DENIED · EMAIL VERIFIED', [approve, disable], 'Nightly approval denied.')
                    act(registered, approve)
                    check(registered, 'ENABLED · APPROVED · EMAIL VERIFIED', ['REVOKE', disable], 'Approved for tonight.')

                    act(registered, 'REVOKE', status=400, stale=True)
                    expect(registered.get_by_role('status')).to_contain_text('Approval changed. Refresh first.')
                    expect(registered.locator('[data-vendor-account-state]')).to_contain_text('APPROVED')
                    act(registered, 'REVOKE')
                    check(registered, 'ENABLED · REVOKED · EMAIL VERIFIED', [approve, disable], 'Nightly approval revoked.')

                    act(registered, disable)
                    check(registered, 'DISABLED · REVOKED · EMAIL VERIFIED', [enable], 'Account disabled.')
                    act(registered, enable)
                    check(registered, 'ENABLED · REVOKED · EMAIL VERIFIED', [approve, disable], 'Account enabled.')
                    act(invited, disable)
                    check(invited, 'DISABLED · NOT APPROVED · REGISTRATION PENDING', [enable], 'Account disabled.')
                    act(invited, enable)
                    check(invited, 'ENABLED · NOT APPROVED · REGISTRATION PENDING', [disable], 'Account enabled.')
                    self.assertEqual(errors, [])
        finally:
            fixture.tearDownClass()

    def test_vendor_registration_and_access_managed_from_nightly_trucks(self):
        fixture = fixtures.MobileDrawerBrowserTest
        fixture.setUpClass()
        fixture.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS'] = 1000
        try:
            with fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                dispatcher = User.query.filter_by(username='drawer-admin').one()
                operation = SortDateOperation(generated_by_user_id=dispatcher.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name='night', window_minutes=60)
                truck = NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number='WORKFLOW-1', capacity_gallons=10000)
                db.session.add_all([operation, truck])
                db.session.flush()
                operation_id, truck_id = operation.id, truck.id
                db.session.commit()

            sort_open = True
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                    side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)] if sort_open else []):
                engine = getattr(fixture.pw, os.environ.get('NEO_VENDOR_BROWSER', 'chromium'))
                with closing(engine.launch()) as browser:
                    dispatch = browser.new_page(viewport={'width': 1440, 'height': 900})
                    driver_context = browser.new_context(viewport={'width': 390, 'height': 844})
                    driver = driver_context.new_page()
                    errors = []
                    for page in (dispatch, driver):
                        page.on('pageerror', lambda error: errors.append(str(error)))
                    origin = fixture.origin
                    vendor_path = origin + '/neoscorpion/driver/'
                    fixture().login(dispatch)
                    dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                    fixture().ready(dispatch, '/neoscorpion/fuel-dispatch?assets=open')
                    nightly = dispatch.locator('#nightly-trucks')
                    accounts = nightly.locator('[data-assets-disclosure="accounts"]')
                    accounts.locator(':scope > summary').click()

                    def manage(label, scope, status=200):
                        with dispatch.expect_response(lambda response:
                                response.request.method == 'POST' and response.url.endswith('/driver/manage')) as request:
                            scope.get_by_role('button', name=label, exact=True).click()
                        self.assertEqual(request.value.status, status, request.value.text())
                        return request.value.json()

                    expect(accounts.get_by_role('link', name='OPEN USER MANAGEMENT')).to_be_visible()
                    expect(accounts.get_by_role('button', name='SEND INVITATION')).to_have_count(0)
                    email = 'workflow-vendor@example.test'
                    driver.goto(origin + '/create-account')
                    values = {'first_name': 'Disposable', 'last_name': 'Vendor', 'supervisor_name': 'Contract Supervisor',
                              'email': email, 'work_area': 'Truck Driver', 'access_reason': 'Synthetic truck driver',
                              'password': 'BrowserFixture123!', 'confirm_password': 'BrowserFixture123!'}
                    for name, value in values.items():
                        driver.locator(f'[name="{name}"]').fill(value)
                    driver.locator('[name="app_codes"][value="neogateway"]').check()
                    messages = []
                    with patch('app.services.email_service.send_email_verification',
                               side_effect=lambda user, token: messages.append((user.id, token)) or {'sent': True}):
                        driver.get_by_role('button', name='Request Access').click()
                        expect(driver.get_by_role('heading', name='Verify Your Email')).to_be_visible()
                    account_id, token = messages[0]
                    driver.goto(origin + '/verify-email/' + token)
                    expect(driver.get_by_role('heading', name='Verification Complete')).to_be_visible()
                    dispatch.goto(origin + '/portal/manage/access-requests')
                    application = dispatch.locator('.access-request-card').filter(has_text=email)
                    application.locator('[name="account_type"]').select_option('vendor')
                    application.get_by_role('button', name='APPROVE ACCESS').click()
                    expect(application).to_have_count(0)
                    fixture().ready(dispatch, '/neoscorpion/fuel-dispatch?assets=open')
                    accounts.locator(':scope > summary').click()
                    account = accounts.locator(f'[data-assets-unit="account-{account_id}"]')
                    expect(account).to_contain_text('ENABLED · NOT APPROVED · EMAIL VERIFIED')
                    driver.goto(origin + '/login')
                    expect(driver.get_by_role('link', name='Forgot Password')).to_be_visible()
                    for width in (390, 1440):
                        driver.set_viewport_size({'width': width, 'height': 844})
                        self.assertFalse(driver.evaluate('document.documentElement.scrollWidth > innerWidth'))
                        expect(driver.get_by_role('button', name='ENTER')).to_be_visible()
                    driver.set_viewport_size({'width': 390, 'height': 844})

                    def sign_in(password):
                        driver.get_by_label('Email', exact=True).fill('workflow-vendor@example.test')
                        driver.get_by_label('Password', exact=True).fill(password)
                        driver.get_by_role('button', name='ENTER', exact=True).click()
                        expect(driver).to_have_url(vendor_path + 'my-truck')
                        expect(driver.get_by_role('heading', name='Truck Driver')).to_be_visible()

                    sort_open = False
                    sign_in('BrowserFixture123!')
                    expect(driver.get_by_text('NO ACTIVE SORT')).to_be_visible()
                    expect(driver.locator('[name="truck_id"], [data-vendor-live]')).to_have_count(0)
                    sort_open = True
                    driver.reload()
                    expect(driver.get_by_text('AWAITING DISPATCHER APPROVAL')).to_be_visible()
                    expect(driver.locator('[name="truck_id"], [data-vendor-live]')).to_have_count(0)
                    fixture().ready(dispatch, '/neoscorpion/fuel-dispatch?assets=open')
                    accounts.locator(':scope > summary').click()
                    for path in ('/portal', '/neoscorpion/fuel-dispatch', '/neoscorpion/fueler',
                                 '/neostaffing/', '/admin/users'):
                        self.assertEqual(driver.request.get(origin + path).status, 403, path)

                    manage('APPROVE TONIGHT', account)
                    expect(account).to_contain_text('ENABLED · APPROVED')
                    expect(driver.get_by_text('REQUEST / CHANGE TRUCK', exact=True)).to_be_visible(timeout=22000)
                    self.assertEqual(driver.request.get(vendor_path + 'live').status, 403)

                    def claim():
                        driver.get_by_text('REQUEST / CHANGE TRUCK', exact=True).click()
                        driver.locator('select[name="truck_id"]').select_option(str(truck_id))
                        driver.locator('[name="gallons"]').fill('4000')
                        with driver.expect_navigation():
                            driver.get_by_role('button', name='REQUEST DISPATCH APPROVAL').click()

                    claim()
                    claims = nightly.locator('[data-assets-unit="claims"]')
                    manage('DENY', claims)
                    expect(driver.get_by_role('status')).to_contain_text('DENIED', timeout=22000)
                    claim()
                    manage('APPROVE CLAIM', claims)
                    expect(driver.get_by_role('heading', name='TRUCK WORKFLOW-1')).to_be_visible(timeout=22000)
                    expect(driver.get_by_role('button', name='DISCONNECT DRIVER')).to_be_visible()

                    driver.get_by_role('button', name='SIGN OUT', exact=True).click()
                    expect(driver).to_have_url(origin + '/login')
                    driver.get_by_role('link', name='Forgot Password').click()
                    resets = []
                    with patch('app.services.email_service.send_password_reset',
                            side_effect=lambda user, token, **options: resets.append((token, options))):
                        driver.get_by_label('Email').fill('workflow-vendor@example.test')
                        driver.get_by_role('button', name='SEND RESET LINK').click()
                        expect(driver.get_by_text('If an account exists', exact=False).first).to_be_visible()
                    reset_token, options = resets[0]
                    self.assertEqual(options, {})
                    driver.goto(origin + '/reset-password/' + reset_token)
                    new_password = 'BrowserReset123!'
                    driver.get_by_label('New Password', exact=True).fill(new_password)
                    driver.get_by_label('Confirm New Password', exact=True).fill(new_password)
                    driver.get_by_role('button', name='Update Password').click()
                    expect(driver).to_have_url(origin + '/login')
                    sign_in(new_password)
                    expect(driver.get_by_role('heading', name='TRUCK WORKFLOW-1')).to_be_visible()
                    with fixture.app.app_context():
                        db.session.get(User, account_id).auth_session_version += 1
                        db.session.commit()
                    driver.goto(vendor_path + 'my-truck')
                    expect(driver).to_have_url(origin + '/login')
                    sign_in(new_password)

                    live = driver.context.new_page()
                    self.assertEqual(driver.get_by_role('link', name='Live Operation').get_attribute('href'),
                                     '/neoscorpion/driver/live')
                    live.goto(vendor_path + 'live')
                    expect(live.get_by_role('heading', name='LIVE OPERATION · READ ONLY')).to_be_visible()

                    with driver.expect_navigation():
                        driver.get_by_role('button', name='DISCONNECT DRIVER').click()
                    expect(driver.get_by_role('heading', name='NO APPROVED TRUCK')).to_be_visible()
                    self.assertEqual(live.request.get(vendor_path + 'live').status, 403)
                    expect(live.get_by_role('alert')).to_contain_text('approved truck connection', timeout=22000)

                    driver.bring_to_front()
                    manual = nightly.locator(f'#nightly-truck-{truck_id} form').filter(
                        has=dispatch.locator('[name="action"][value="manual"]'))
                    manual.locator('[name="name"]').fill('Manual Driver')
                    manage('SET NAME / UNKNOWN', manual)
                    expect(nightly.locator('[data-assets-driver-name]')).to_contain_text('Manual Driver')
                    manage('REVOKE', account)
                    self.assertEqual(driver.request.get(vendor_path + 'revision').status, 403)
                    expect(driver.get_by_text('DISPATCHER APPROVAL REVOKED')).to_be_visible(timeout=22000)
                    manage('DISABLE ACCOUNT', account)
                    response = driver.request.get(vendor_path + 'account', max_redirects=0)
                    self.assertEqual(response.status, 302)
                    self.assertEqual(response.headers['location'], '/login')
                    expect(nightly.locator('[data-assets-driver-name]')).to_contain_text('Manual Driver')
                    self.assertEqual(errors, [])
        finally:
            fixture.tearDownClass()

    def test_chromium_webkit_integrated_controls_live_claims_and_mobile_readonly(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
        try:
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();actor=User.query.filter_by(username='drawer-admin').one();uid=actor.id
                op=SortDateOperation(generated_by_user_id=uid,gateway_id=gateway.id,gateway_code=gateway.code,
                    sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(op);db.session.flush();oid=op.id;cases=[];driver_ids=[]
                for index in range(2):
                    trucks=[]
                    for j in range(3):
                        truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number=f'ASSET-{index}-{j}',capacity_gallons=10000)
                        db.session.add(truck);db.session.flush();trucks.append(truck.id)
                    db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=trucks[0],
                        status='available',starting_gallons=6000,current_gallons=6000))
                    cases.append(trucks)
                    vendor, token = vendors.invite(gateway, actor,
                        f'assets-vendor-{index}@example.test', f'Asset Vendor {index}')
                    vendors.accept_invitation(token, 'BrowserFixture123!', 'BrowserFixture123!')
                    driver_ids.append(vendor.id)
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                    side_effect=lambda g,now=None:[db.session.get(SortDateOperation,oid)]):
                for index,(engine,trucks) in enumerate(zip((F.pw.chromium,F.pw.webkit),cases)):
                    browser=engine.launch();errors=[]
                    try:
                        context=browser.new_context(viewport={'width':1440,'height':900})
                        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                        F().login(page);page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        F().ready(page,'/neoscorpion/fuel-dispatch?assets=open');page.evaluate("window.assetsContinuity='kept'")
                        workspace=page.locator('[data-assets-workspace]')
                        expect(workspace.locator('.neoscorpion-assets-grid > section')).to_have_count(3)
                        self.assertEqual(workspace.locator('form form').count(),0)
                        def submit(form,name):
                            with page.expect_response(lambda r:r.request.method=='POST' and ('/fuel-dispatch/assets' in r.url or '/driver/manage' in r.url or '/driver/inventory' in r.url)) as response:
                                form.get_by_role('button',name=name,exact=True).click()
                            payload=response.value.json()
                            self.assertEqual(response.value.status,200,payload);self.assertTrue(payload['ok'],payload)
                            self.assertEqual(page.evaluate('window.assetsContinuity'),'kept')
                        workspace.locator(':scope > summary').click()
                        page.locator(f'[data-dispatch-truck-id="{trucks[0]}"] [data-assets-open]').click()
                        self.assertTrue(workspace.evaluate('n=>n.open'))
                        truck=page.locator(f'#nightly-truck-{trucks[0]}')
                        expect(truck).to_be_focused()
                        islands=workspace.locator('[data-assets-unit="islands"]')
                        islands.locator('select').select_option('2');submit(islands.locator('form'),'SAVE')
                        expect(islands.locator('.neoscorpion-assets-value')).to_have_text('2')
                        fuelers=workspace.locator('[data-assets-unit="fuelers"]')
                        add=fuelers.locator('form').filter(has=page.locator('[name="action"][value="add_fueler"]'))
                        if add.locator('select option[value="'+str(uid)+'"]').count():
                            add.locator('select').select_option(str(uid));submit(add,'Add Fueler')
                        nickname=fuelers.locator('form').filter(has=page.locator('[name="action"][value="set_fueler_nickname"]')).first
                        nickname.locator('[name="nickname"]').fill(f'OPS-{index}');submit(nickname,'SAVE')
                        expect(nickname.locator('[name="nickname"]')).to_have_value(f'OPS-{index}')
                        addtruck=workspace.locator('[data-assets-unit="add-truck"]')
                        addtruck.locator('select[name="fuel_truck_id"]').select_option(str(trucks[1]))
                        addtruck.locator('[name="current_gallons"]').fill('5000');submit(addtruck,'Add Truck Tonight')
                        expect(page.locator(f'#nightly-truck-{trucks[1]}')).to_be_visible(timeout=12000)
                        manual=truck.locator('form').filter(has=page.locator('[name="action"][value="manual"]'))
                        manual.locator('[name="name"]').fill('Night Contractor');submit(manual,'SET NAME / UNKNOWN')
                        expect(truck.locator('[data-assets-driver-name]')).to_contain_text('Night Contractor',timeout=12000)
                        verified=truck.locator('[data-vendor-driver-assignment]')
                        verified.locator('..').locator(':scope > summary').click()
                        verified.locator('select').select_option(str(uid));submit(verified,'SAVE DRIVER')
                        expect(truck.locator('[data-assets-driver-name]')).to_contain_text('Long Operational Dispatcher Name',timeout=12000)
                        verified.locator('select').select_option('');submit(verified,'SAVE DRIVER')
                        expect(truck.locator('[data-assets-driver-name]')).to_contain_text('UNKNOWN',timeout=12000)
                        submit(truck.locator('form').filter(has=page.locator('[name="action"][value="mark_topping_off"]')),'TOP OFF')
                        returned=truck.locator('form').filter(has=page.locator('[name="action"][value="complete_top_off"]'))
                        expect(returned).to_be_visible(timeout=12000);returned.locator('[name="current_gallons"]').fill('9000');submit(returned,'RETURN')
                        expect(truck.locator('[data-assets-truck-facts]')).to_contain_text('9,000',timeout=12000)
                        service=truck.locator('[data-assets-disclosure^="service-"]')
                        service.locator('summary').click()
                        statusform=service.locator('form').filter(has=page.locator('[name="action"][value="update_truck"]'))
                        statusform.locator('select').select_option('unavailable_oos');submit(statusform,'SAVE STATUS')
                        expect(truck.locator('[data-assets-truck-facts]')).to_contain_text('OOS',timeout=12000)
                        statusform.locator('select').select_option('available');submit(statusform,'SAVE STATUS')
                        expect(truck.locator('[data-assets-truck-facts]')).to_contain_text('IDLE',timeout=12000)
                        accounts=workspace.locator('[data-assets-disclosure="accounts"]')
                        accounts.locator(':scope > summary').click()
                        person = driver_ids[index]
                        account=workspace.locator(f'[data-assets-unit="account-{person}"]')
                        expect(account).to_be_visible(timeout=12000);submit(account.locator('form'),'APPROVE TONIGHT')
                        expect(account).to_contain_text('APPROVED',timeout=12000)
                        driver=browser.new_context(viewport={'width':390,'height':844}).new_page()
                        F().login(driver,f'assets-vendor-{index}',path='/login')
                        F().ready(driver,'/neoscorpion/driver/my-truck')
                        # Pending claims update independently of another truck's unsaved draft.
                        manual.locator('[name="name"]').fill('Protected draft');manual.locator('[name="name"]').focus()
                        driver.get_by_text('REQUEST / CHANGE TRUCK',exact=True).click()
                        claim=driver.locator('form').filter(has=driver.locator('select[name="truck_id"]'))
                        claim.locator('select[name="truck_id"]').select_option(str(trucks[2]));claim.locator('[name="gallons"]').fill('4000')
                        with driver.expect_navigation():claim.get_by_role('button',name='REQUEST DISPATCH APPROVAL').click()
                        pending=workspace.locator('[data-assets-unit="claims"]')
                        expect(pending).to_contain_text(f'ASSET-{index}-2',timeout=15000)
                        expect(manual.locator('[name="name"]')).to_have_value('Protected draft')
                        expect(manual.locator('[name="name"]')).to_be_focused()
                        submit(pending.locator('form'),'APPROVE CLAIM')
                        expect(pending.locator('form')).to_have_count(0,timeout=12000)
                        expect(page.locator(f'#nightly-truck-{trucks[2]}')).to_be_visible(timeout=12000)
                        expect(manual.locator('[name="name"]')).to_have_value('Protected draft')
                        submit(manual,'SET NAME / UNKNOWN')
                        with F.app.app_context():
                            selected=NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=oid,fuel_truck_id=trucks[2]).one()
                            self.assertEqual((selected.starting_gallons,selected.current_gallons),(4000,4000))
                        for width,height in ((1920,1080),(1440,900),(1024,768),(390,844),(375,568)):
                            page.set_viewport_size({'width':width,'height':height})
                            self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                            if width<901:
                                expect(workspace).to_be_visible()
                                self.assertEqual(workspace.locator('input:visible,select:visible,button:visible').count(),0)
                            page.screenshot(path=str(F.evidence/f'assets-{engine.name}-{width}.png'),full_page=True)
                        page.set_viewport_size({'width':1440,'height':900})
                        workspace.locator(':scope > summary').click()
                        page.locator('[data-operational-board-toggle]:visible').click()
                        page.locator(f'[data-dispatch-truck-id="{trucks[2]}"] [data-assets-open]').click()
                        expect(workspace).to_be_visible()
                        self.assertTrue(workspace.evaluate('n=>n.open'))
                        self.assertLessEqual(workspace.bounding_box()['height'],451)
                        target=page.locator(f'#nightly-truck-{trucks[2]}')
                        bounds=target.bounding_box();outer=workspace.bounding_box()
                        self.assertLess(bounds['y'],outer['y']+outer['height'])
                        page.screenshot(path=str(F.evidence/f'assets-{engine.name}-board.png'),full_page=False)
                        self.assertEqual(errors,[])
                    finally:browser.close()
        finally:F.tearDownClass()
