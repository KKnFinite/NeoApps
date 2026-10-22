"""Two actual administrator sessions, retained drafts and server-side recovery."""
from datetime import datetime
from pathlib import Path
import unittest
from playwright.sync_api import expect
from app.extensions import db
from app.models import User, PortalAppAccess, NeoScorpionSettings, NeoScorpionFuelTruck, NeoScorpionSortTruck
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_mobile_drawer as fixtures


class AdminReliabilityBrowserTest(unittest.TestCase):
    def test_two_admin_settings_and_duplicate_truck_desktop_mobile(self):
        F=fixtures.MobileDrawerBrowserTest; F.setUpClass()
        evidence=Path('instance/package2-evidence'); evidence.mkdir(exist_ok=True)
        try:
            with F.app.app_context():
                for name in ('package2-a','package2-b'):
                    user=User(username=name,email=name+'@example.test',role='master',email_verified_at=datetime.utcnow())
                    set_user_password(user,'BrowserFixture123!');db.session.add(user);db.session.flush()
                    backfill_default_gateway_node_roles(user,role='master')
                    db.session.add(PortalAppAccess(user_id=user.id,app_code='neostaffing',status='approved',role='master',is_active=True))
                db.session.commit()
            for engine in ('chromium','webkit'):
                browser=getattr(F.pw,engine).launch()
                try:
                    errors=[]
                    a,b=[browser.new_page(viewport={'width':1440,'height':900},service_workers='block') for _ in range(2)]
                    for page,name in ((a,'package2-a'),(b,'package2-b')):
                        page.on('pageerror',lambda e:errors.append(str(e)))
                        F().login(page,name); page.goto(F.origin+'/neoscorpion/settings')
                    density='[name=fuel_density_lbs_per_gallon]'
                    original=float(a.locator(density).input_value()); target=str(original+.1)
                    a.locator(density).fill(target);a.get_by_role('button',name='Save Settings',exact=True).click()
                    expect(a.locator(density)).to_have_value(target)
                    b.locator('[name=mission_row_height_percent]').select_option('115')
                    b.set_viewport_size({'width':375,'height':812})
                    with b.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/neoscorpion/settings')) as stale:
                        b.get_by_role('button',name='Save Settings',exact=True).click()
                    self.assertEqual(stale.value.status,409)
                    b.screenshot(path=str(evidence/f'{engine}-mobile-stale-settings-before-assert.png'),full_page=True)
                    expect(b.locator('[data-admin-form-error]')).to_be_in_viewport()
                    bounds=b.locator('[data-admin-form-error]').bounding_box()
                    self.assertLessEqual(bounds['x']+bounds['width'],375)
                    expect(b.locator('[data-admin-form-error]')).to_contain_text('changed since you opened it')
                    expect(b.locator(density)).to_have_value(str(original))
                    expect(b.locator('[name=mission_row_height_percent]')).to_have_value('115')
                    expect(b.get_by_role('button',name='Save Settings',exact=True)).to_be_enabled()
                    b.screenshot(path=str(evidence/f'{engine}-mobile-stale-settings.png'),full_page=True)
                    # A new current-values tab leaves the rejected draft intact.
                    with b.expect_popup() as popup:
                        b.get_by_role('link',name='Open current values in a new tab').click()
                    current=popup.value;current.wait_for_load_state()
                    expect(current.locator(density)).to_have_value(target)
                    current.locator('[name=mission_row_height_percent]').select_option('115')
                    current.get_by_role('button',name='Save Settings',exact=True).click()
                    expect(current.locator(density)).to_have_value(target)
                    expect(b.locator(density)).to_have_value(str(original))
                    current.close()
                    # Each remaining Settings section retains its own rejected
                    # draft and its original baseline, not a fresh overwrite token.
                    for scope,field,button_name in (
                        ('save_apu_rates','apu_rate_b757','Save APU Rates'),
                        ('save_assignment_planning_settings','assignment_setup_minutes','Save Assignment Planning')):
                        for page in (a,b): page.goto(F.origin+'/neoscorpion/settings')
                        af=a.locator(f'[data-admin-form="{scope}"]'); bf=b.locator(f'[data-admin-form="{scope}"]')
                        original_value=float(af.locator(f'[name="{field}"]').input_value())
                        first=f'{original_value+.01:.2f}' if scope=='save_apu_rates' else str(int(original_value)+1)
                        second=f'{original_value+.02:.2f}' if scope=='save_apu_rates' else str(int(original_value)+2)
                        af.locator(f'[name="{field}"]').fill(first)
                        with a.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/neoscorpion/settings')) as saved:
                            af.get_by_role('button',name=button_name,exact=True).click()
                        self.assertEqual(saved.value.status,302)
                        bf.locator(f'[name="{field}"]').fill(second)
                        with b.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/neoscorpion/settings')) as conflict:
                            bf.get_by_role('button',name=button_name,exact=True).click()
                        self.assertEqual(conflict.value.status,409)
                        expect(bf.locator(f'[name="{field}"]')).to_have_value(second)
                        expect(b.locator('[data-admin-form-error]')).to_be_in_viewport()
                    # SPEAR has the same guard without changing its decisions.
                    for page in (a,b):page.goto(F.origin+'/neoscorpion/settings/spear')
                    reserve='650' if engine=='chromium' else '700'
                    a.locator('[name=minimum_truck_reserve_gallons]').fill(reserve)
                    with a.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/settings/spear')) as spear_saved:
                        a.get_by_role('button',name='SAVE SPEAR SETTINGS',exact=True).click()
                    self.assertEqual(spear_saved.value.status,302)
                    expect(a.locator('[name=minimum_truck_reserve_gallons]')).to_have_value(reserve)
                    b.locator('[name=minimum_truck_reserve_gallons]').fill('750')
                    b.get_by_role('button',name='SAVE SPEAR SETTINGS',exact=True).click()
                    expect(b.locator('[data-admin-form-error]')).to_be_visible()
                    expect(b.locator('[name=minimum_truck_reserve_gallons]')).to_have_value('750')
                    # Existing uppercase/trim identity; no nightly assets created.
                    a.goto(F.origin+'/neoscorpion/truck-manager');new=a.locator('[data-admin-form=truck-new]')
                    new.locator('[name=truck_number]').fill('SAFE-'+engine)
                    new.locator('[name=description]').fill('First truck')
                    new.get_by_role('button',name='Add Truck',exact=True).click()
                    expect(a.locator('body')).to_contain_text('FUEL TRUCK SAVED')
                    b.goto(F.origin+'/neoscorpion/truck-manager');new=b.locator('[data-admin-form=truck-new]')
                    new.locator('[name=truck_number]').fill(' safe-'+engine+' ')
                    new.locator('[name=description]').fill('Keep my failed truck draft')
                    with b.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/truck-manager')) as duplicate:
                        new.get_by_role('button',name='Add Truck',exact=True).click()
                    self.assertEqual(duplicate.value.status,400)
                    expect(b.locator('.flash-error').first).to_contain_text('already exists')
                    expect(new.locator('[name=truck_number]')).to_have_value(' safe-'+engine+' ')
                    expect(new.locator('[name=description]')).to_have_value('Keep my failed truck draft')
                    expect(new.get_by_role('button',name='Add Truck',exact=True)).to_be_enabled()
                    b.screenshot(path=str(evidence/f'{engine}-mobile-duplicate-truck.png'),full_page=True)
                    new.locator('[name=truck_number]').fill('NEW-'+engine)
                    posts=[]
                    b.on('request',lambda request:posts.append(request.url) if request.method=='POST' and request.url.endswith('/truck-manager') else None)
                    with b.expect_navigation():
                        new.evaluate('(form)=>{const button=form.querySelector("button[type=submit]");form.requestSubmit(button);form.requestSubmit(button);}')
                    expect(b.locator('body')).to_contain_text('FUEL TRUCK SAVED')
                    b.reload();expect(new.locator('[name=truck_number]')).to_have_value('')
                    self.assertEqual(len(posts),1)
                    with F.app.app_context():
                        self.assertEqual(NeoScorpionSettings.query.one().fuel_density_lbs_per_gallon,float(target))
                        self.assertEqual(NeoScorpionFuelTruck.query.filter_by(truck_number=('NEW-'+engine).upper()).count(),1)
                        self.assertEqual(NeoScorpionSortTruck.query.count(),0)
                    self.assertEqual(errors,[])
                    print('Package 2 administrator browser passed:',engine,flush=True)
                finally:browser.close()
        finally:F.tearDownClass()
