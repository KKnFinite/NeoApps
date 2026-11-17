"""Authenticated vendor/Dispatcher workflows on disposable browser fixtures."""
import unittest
import os
from datetime import date,datetime,timedelta
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,Gateway,SortDateOperation,SortDateMission,NeoScorpionFuelAssignment,
    NeoScorpionFuelTruck,NeoScorpionSortTruck,NeoScorpionSortAssetState,NeoScorpionSettings,
    NeoScorpionVendorRequest, SortDateParkingAssignment)
from app.services import neoscorpion as service, neoscorpion_vendors as vendors
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures
from tests.neoscorpion_handoff_fixture import submit_and_verify


class VendorBrowserTest(unittest.TestCase):
    def test_all_sort_missions_refresh_on_vendor_truck(self):
        F = fixtures.MobileDrawerBrowserTest
        F.setUpClass()
        browser = None
        try:
            def departure(operation, tail, dest, departure_at):
                return SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                    gateway_code=operation.gateway_code, sort_name=operation.sort_name, mission_type='departure',
                    mission_source='manual', flight_number='FIX' + tail[1:4], origin=operation.gateway_code,
                    destination=dest, assigned_tail_number=tail, planned_datetime_utc=departure_at,
                    timezone='UTC', planned_fuel_load=25000)

            with F.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                actor = User.query.filter_by(username='drawer-admin').one()
                operation = SortDateOperation(generated_by_user_id=actor.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name='night', window_minutes=60)
                vendor_truck, working_truck = [NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number=number,
                    capacity_gallons=10000) for number in ('422808', '426606')]
                db.session.add_all([operation, vendor_truck, working_truck])
                db.session.flush()
                db.session.add_all([NeoScorpionSortTruck(sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
                    status='available', starting_gallons=6000, current_gallons=6000) for truck in (vendor_truck, working_truck)])
                db.session.add(NeoScorpionSettings(gateway_id=gateway.id))
                at = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(minutes=15)
                work_mission = departure(operation, 'N491UP', 'SDF', at + timedelta(hours=2))
                edit_mission = departure(operation, 'N350UP', 'ONT', at + timedelta(hours=3))
                delete_mission = departure(operation, 'N440UP', 'PHX', at + timedelta(hours=4))
                parking = SortDateParkingAssignment(sort_date_operation_id=operation.id,
                    tail_number='N350UP', ramp_code='B', position_code='06')
                db.session.add_all([work_mission, edit_mission, delete_mission, parking])
                db.session.flush()
                assignment = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,
                    sort_date_mission_id=work_mission.id, assigned_fueler_user_id=actor.id,
                    assigned_truck_id=working_truck.id, review_status='assigned')
                db.session.add(assignment)
                vendor, token = vendors.invite(gateway, actor, 'missions-vendor@example.test', 'Missions Contractor')
                db.session.commit()
                vendors.accept_invitation(token, 'BrowserFixture123!', 'BrowserFixture123!')
                db.session.commit()
                op_id, actor_id, vendor_id, gateway_id = operation.id, actor.id, vendor.id, gateway.id
                vendor_truck_id, working_truck_id = vendor_truck.id, working_truck.id
                assignment_id, edit_id, delete_id, parking_id = assignment.id, edit_mission.id, delete_mission.id, parking.id

            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                       side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, op_id)]):
                browser = F.pw.chromium.launch()
                driver = browser.new_page(viewport={'width': 375, 'height': 760})
                errors = []
                driver.on('pageerror', lambda error: errors.append(str(error)))
                F().login(driver, 'missions-vendor', path='/login')
                section = driver.locator('[data-vendor-sort-missions]')
                working = section.locator('[data-sort-mission-group=working]')
                left = section.locator('[data-sort-mission-group=left]')
                complete = section.locator('[data-sort-mission-group=complete]')
                expect(driver.get_by_text('AWAITING DISPATCHER APPROVAL')).to_be_visible()
                expect(section).to_have_count(0)
                with F.app.app_context():
                    vendors.set_participation(db.session.get(Gateway, gateway_id), db.session.get(SortDateOperation, op_id),
                        db.session.get(User, actor_id), vendor_id, 'approved', 0)
                    db.session.commit()
                expect(driver.get_by_text('NO APPROVED TRUCK')).to_be_visible(timeout=22000)
                expect(section).to_have_count(0)
                driver.get_by_text('REQUEST / CHANGE TRUCK', exact=True).click()
                driver.locator('select[name=truck_id]').select_option(str(vendor_truck_id))
                with driver.expect_navigation(wait_until='domcontentloaded'):
                    driver.get_by_role('button', name='REQUEST DISPATCH APPROVAL').click()
                expect(section).to_have_count(0)
                with F.app.app_context():
                    claim = NeoScorpionVendorRequest.query.filter_by(user_id=vendor_id, kind='claim').one()
                    vendors.decide_claim(db.session.get(Gateway, gateway_id), db.session.get(SortDateOperation, op_id),
                        db.session.get(User, actor_id), claim.id, True, 0)
                    db.session.commit()
                expect(section).to_be_visible(timeout=22000)
                self.assertEqual(section.locator('thead th').all_text_contents(), ['TAIL', 'DEST', 'PARKING', 'ETD'])
                self.assertEqual(section.locator('tbody th').all_text_contents(), ['CURRENTLY WORKING', 'LEFT TO WORK', 'COMPLETE'])
                self.assertEqual(section.locator('button, a, form, input').count(), 0)
                self.assertEqual(section.locator('xpath=preceding-sibling::*[1]').get_attribute('data-vendor-truck-controls'),
                    driver.locator('[data-vendor-truck-controls]').get_attribute('data-vendor-truck-controls'))
                expect(driver.get_by_text('TRUCK 422808', exact=True)).to_be_visible()
                expect(working).to_contain_text('None.')
                expect(complete).to_contain_text('None.')
                expect(left.locator('tr td:first-child')).to_have_text(['N491UP', 'N350UP', 'N440UP'])
                for width in (375, 1440):
                    driver.set_viewport_size({'width': width, 'height': 760})
                    self.assertTrue(section.evaluate('''el => {
                        const table = el.querySelector('table');
                        return table.getBoundingClientRect().right <= innerWidth
                            && parseFloat(getComputedStyle(table).fontSize) >= 12
                            && Array.from(table.querySelectorAll('th, td')).every(cell => cell.scrollWidth <= cell.clientWidth);
                    }'''))
                driver.set_viewport_size({'width': 375, 'height': 760})

                driver.get_by_text('REQUEST / CHANGE TRUCK', exact=True).click()
                draft = driver.get_by_label('Current gallons (only for a truck not yet configured)', exact=True)
                draft.fill('4321')
                new_departure = at + timedelta(hours=1)
                with F.app.app_context():
                    operation = db.session.get(SortDateOperation, op_id)
                    db.session.get(SortDateMission, edit_id).eta_datetime_utc = new_departure
                    db.session.get(SortDateParkingAssignment, parking_id).position_code = '08'
                    db.session.delete(db.session.get(SortDateMission, delete_id))
                    db.session.add(departure(operation, 'N560UP', 'DEN', at + timedelta(hours=5)))
                    db.session.commit()
                expect(left.locator('tr td:first-child')).to_have_text(['N350UP', 'N491UP', 'N560UP'], timeout=22000)
                expect(left.locator('tr').nth(1).locator('td')).to_have_text(['N350UP', 'ONT', 'B08', new_departure.strftime('%H:%M')])
                expect(draft).to_be_focused()
                expect(draft).to_have_value('4321')

                with F.app.app_context():
                    gateway, actor = db.session.get(Gateway, gateway_id), db.session.get(User, actor_id)
                    service.confirm_fueler_truck_on(gateway, actor, assignment_id, expected_cycle=1,
                        expected_tail='N491UP', expected_truck_id=working_truck_id, now_utc=at)
                    db.session.commit()
                expect(working).to_contain_text('N491UP', timeout=22000)
                expect(left).not_to_contain_text('N491UP')
                with F.app.app_context():
                    gateway, actor = db.session.get(Gateway, gateway_id), db.session.get(User, actor_id)
                    service.save_fueler_entry(gateway, actor, dict(assignment_id=assignment_id,
                        apu_running='no', remaining_left='6',remaining_ctr='0',remaining_right='6',
                        actual_left='15',actual_ctr='0',actual_right='15',transfer_fuel_gallons='3000'))
                    service.mark_fueler_off(gateway, actor, assignment_id)
                    submit_and_verify(gateway, actor, assignment_id)
                    db.session.commit()
                expect(complete).to_contain_text('N491UP', timeout=22000)
                expect(working).to_contain_text('None.')
                self.assertEqual(driver.request.get(F.origin + '/neoscorpion/fuel-dispatch').status, 403)

                with F.app.app_context():
                    vendors.set_participation(db.session.get(Gateway, gateway_id), db.session.get(SortDateOperation, op_id),
                        db.session.get(User, actor_id), vendor_id, 'revoked', 1)
                    db.session.commit()
                expect(section).to_have_count(0, timeout=22000)
                expect(driver.get_by_text('DISPATCHER APPROVAL REVOKED')).to_be_visible()
                self.assertFalse(errors, errors)
        finally:
            if browser:
                browser.close()
            F.tearDownClass()

    def test_vendor_approval_claim_inventory_and_responsive_board(self):
        F=fixtures.MobileDrawerBrowserTest
        F.setUpClass();browser=None
        def submit(page,button):
            with page.expect_navigation(wait_until='domcontentloaded'):
                button.click()
        try:
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();actor=User.query.filter_by(username='drawer-admin').one()
                at=datetime.utcnow().replace(microsecond=0)-timedelta(minutes=10)
                operation=SortDateOperation(generated_by_user_id=actor.id,gateway_id=gateway.id,gateway_code=gateway.code,
                    sort_date=date.today(),sort_name='night',window_minutes=60)
                truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='422808',capacity_gallons=10000)
                db.session.add_all([operation,truck]);db.session.flush()
                db.session.add_all([NeoScorpionSortAssetState(sort_date_operation_id=operation.id,revision=1),
                    NeoScorpionSortTruck(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,status='available',starting_gallons=6000,current_gallons=6000),NeoScorpionSettings(gateway_id=gateway.id)])
                mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,gateway_code=gateway.code,
                    sort_name='night',mission_type='departure',mission_source='manual',flight_number='UPS1038',origin=gateway.code,
                    destination='SDF',assigned_tail_number='N491UP',planned_datetime_utc=at+timedelta(hours=2),timezone='America/Chicago',planned_fuel_load=25000,fuel_status='assigned')
                db.session.add(mission);db.session.flush()
                assignment=NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,sort_date_mission_id=mission.id,
                    assigned_fueler_user_id=actor.id,assigned_truck_id=truck.id,review_status='assigned')
                db.session.add(assignment);db.session.commit()
                vendor,token=vendors.invite(gateway,actor,'browser-vendor@example.test','Verified Contractor')
                db.session.commit();vendors.accept_invitation(token,'BrowserFixture123!','BrowserFixture123!');db.session.commit()
                operation_id,truck_id,actor_id,gateway_id,assignment_id,vendor_id=operation.id,truck.id,actor.id,gateway.id,assignment.id,vendor.id
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,operation_id)]):
                browser=getattr(F.pw,os.environ.get('NEO_VENDOR_BROWSER','chromium')).launch();errors=[]
                admin=browser.new_context(viewport={'width':1440,'height':900}).new_page();admin.on('pageerror',lambda e:errors.append(str(e)))
                F().login(admin);F().ready(admin,'/neoscorpion/driver/manage')
                driver=browser.new_context(viewport={'width':390,'height':844}).new_page();driver.on('pageerror',lambda e:errors.append(str(e)))
                F().login(driver,'browser-vendor',path='/login')
                expect(driver).to_have_url(F.origin+'/neoscorpion/driver/my-truck')
                self.assertEqual(driver.request.get(F.origin+'/neoscorpion/driver/live').status,403)
                submit(admin,admin.get_by_role('button',name='APPROVE TONIGHT',exact=True))
                expect(admin.locator('article').first).to_contain_text('ENABLED · APPROVED')
                response=driver.goto(F.origin+'/neoscorpion/driver/my-truck')
                self.assertEqual(response.status,200,driver.locator('body').inner_text())
                driver.get_by_text('REQUEST / CHANGE TRUCK',exact=True).click()
                driver.locator('select[name=truck_id]').select_option(str(truck_id))
                submit(driver,driver.get_by_role('button',name='REQUEST DISPATCH APPROVAL'))
                expect(driver.locator('[data-vendor-live]')).to_contain_text('NO APPROVED TRUCK')
                expect(driver.locator('[data-vendor-live]')).to_contain_text('PENDING APPROVAL')
                admin.reload();submit(admin,admin.get_by_role('button',name='APPROVE CLAIM',exact=True))
                driver.reload();expect(driver.locator('[data-vendor-live]')).to_contain_text('TRUCK 422808')
                with F.app.app_context():
                    service.confirm_fueler_truck_on(db.session.get(Gateway,gateway_id),db.session.get(User,actor_id),assignment_id,
                        expected_cycle=1,expected_tail='N491UP',expected_truck_id=truck_id,now_utc=at);db.session.commit()
                driver.reload()
                driver.get_by_text('REQUEST / CHANGE TRUCK',exact=True).click()
                driver.get_by_label('Current gallons (only for a truck not yet configured)', exact=True).fill('1234')
                with F.app.app_context():
                    service.save_fueler_entry(db.session.get(Gateway,gateway_id),db.session.get(User,actor_id),
                        dict(assignment_id=assignment_id,apu_running='no',remaining_left='6',remaining_ctr='0',remaining_right='6'));db.session.commit()
                driver.locator('h2').first.click()  # Editing finishes without submitting the truck request.
                expect(driver.locator('[data-tank-code=left] [role=cell]').first).to_have_text('6.0',timeout=20000)
                expect(driver.get_by_label('Current gallons (only for a truck not yet configured)', exact=True)).to_have_value('1234')
                admin.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                F().ready(admin,'/neoscorpion/fuel-dispatch?assets=open')
                correction=admin.locator('#nightly-trucks .scorpion-inventory-inline').first
                correction.locator('[name=gallons]').fill('5500')
                with admin.expect_response(lambda response: response.request.method == 'POST'
                        and response.url.endswith('/driver/inventory')) as corrected:
                    correction.get_by_role('button', name='CORRECT').click()
                self.assertEqual(corrected.value.status, 200)
                expect(admin.locator('.neoscorpion-truck-visual-values').first).to_contain_text('5,500')
                with F.app.app_context():
                    service.mark_fueler_off(db.session.get(Gateway,gateway_id),db.session.get(User,actor_id),assignment_id);db.session.commit()
                    self.assertIsNone(db.session.get(NeoScorpionFuelAssignment,assignment_id).completed_at_utc)
                driver.reload();driver.get_by_text('OPTIONAL FUEL VERIFICATION · SKIP',exact=True).click()
                reading=driver.locator('form[action$="fuel-reading"]');reading.locator('[name=gallons]').fill('4000')
                submit(driver,reading.get_by_role('button',name='SUBMIT READING'))
                admin.reload();expect(admin.locator('#nightly-trucks .scorpion-inventory-pending')).to_contain_text('4000')
                # Actual Board View mode, not a separate imitation of truck cards.
                admin.locator('[data-operational-board-toggle]:visible').first.click()
                card=admin.locator('.neoscorpion-truck-visual-card').first
                for width,height in ((1440,900),(1920,1080),(390,844),(375,568)):
                    admin.set_viewport_size({'width':width,'height':height})
                    panel=admin.locator('.neoscorpion-truck-visual-panel')
                    if width>900:
                        self.assertLessEqual(panel.bounding_box()['height'],291)
                    for control in card.locator('form input:not([type=hidden]), form button').all():
                        self.assertEqual(control.is_visible(), width > 900)
                        if width>900:
                            self.assertLessEqual(control.bounding_box()['y']+control.bounding_box()['height'],panel.bounding_box()['y']+panel.bounding_box()['height']+1)
                    self.assertFalse(admin.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                    admin.screenshot(path=str(F.evidence/f'vendor-dispatch-{width}.png'),full_page=True)
                admin.set_viewport_size({'width':1440,'height':900})
                expect(admin.locator('body')).to_have_class(__import__('re').compile('operational-board-view'))
                submit(admin,card.get_by_role('button',name='APPROVE',exact=True))
                expect(admin.locator('.neoscorpion-truck-visual-values').first).to_contain_text('4,000')
                expect(admin.locator('body')).to_have_class(__import__('re').compile('operational-board-view'))
                for path in ('my-truck','live'):
                    driver.goto(F.origin+'/neoscorpion/driver/'+path)
                    for width,height in ((1440,900),(390,844),(375,568)):
                        driver.set_viewport_size({'width':width,'height':height})
                        self.assertFalse(driver.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                        driver.screenshot(path=str(F.evidence/f'vendor-{path}-{width}.png'),full_page=True)
                    self.assertEqual(driver.locator('input[name^=actual_],input[name^=remaining_],button:has-text("COMPLETE")').count(),0)
                # Revocation removes access on the next operational request.
                admin.goto(F.origin+'/neoscorpion/driver/manage')
                admin.set_viewport_size({'width':375,'height':568})
                self.assertFalse(admin.evaluate('document.documentElement.scrollWidth>innerWidth+1'),
                    admin.evaluate('Array.from(document.querySelectorAll("body *")).filter(el=>el.getBoundingClientRect().right>innerWidth+1).slice(-12).map(el=>({tag:el.tagName,cls:el.className,width:el.getBoundingClientRect().width,text:el.textContent.slice(0,90)}))'))
                submit(admin,admin.get_by_role('button',name='REVOKE',exact=True))
                expect(admin.locator('article').first).to_contain_text('ENABLED · REVOKED')
                self.assertEqual(driver.request.get(F.origin+'/neoscorpion/driver/live').status,403)
                self.assertFalse(errors,errors)
        finally:
            if browser:browser.close()
            F.tearDownClass()
